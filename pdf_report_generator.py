import hashlib
import json
import os
import re
import sys
import tempfile

from dotenv import load_dotenv
from fpdf import FPDF
from google import genai
from google.genai import types

from key_moments_extractor import extract_key_moments_from_vtt, generate_with_rate_limit
from screenshot_extractor import extract_screenshots
from transcript_parser import parse_vtt

load_dotenv()

MODEL_NAME = "gemini-3.1-flash-lite"
# Deployed environments (e.g. Azure Functions) run from a read-only folder -
# the system temp dir is writable everywhere, including locally.
STEP_CACHE_DIR = os.path.join(tempfile.gettempdir(), "hitachi_step_writer_cache")

HITACHI_RED = (230, 0, 40)
HITACHI_DARK_GRAY = (40, 40, 40)

STEP_PROMPT_TEMPLATE = """You are writing a step in a "how to use this product" navigation guide
for a client, based on a screenshot taken during a live product demo.

What the presenter said at this point in the demo (for context):
"{spoken_text}"

The spoken context above may be in English, Hindi, or a mix of both. Understand it
regardless of language, but no matter what language it's in, your "title" and "bullets"
output below must always be written in English.

Look at the attached screenshot and write ONE guide step describing this moment. Return ONLY
a JSON object (no markdown fences, no commentary) with exactly this shape:
{{
    "title": "short feature/step name in English, e.g. 'Starting a Recording'",
    "bullets": ["2 to 4 short bullet points in English describing what this feature does and/or how to navigate to it"]
}}

Base the step on both the screenshot and the spoken context. If the screenshot shows a specific
UI element (button, menu, panel), name it and describe how to reach/use it. Keep bullets concise
- one short sentence each.
"""

BATCH_STEP_PROMPT = """You are writing a "how to use this product" navigation guide for a client,
based on a series of screenshots taken during a live product demo. Below are {count} numbered
screenshots, each preceded by what the presenter said at that moment (for context).

The spoken context may be in English, Hindi, or a mix of both. Understand it regardless of
language, but your "title" and "bullets" output must ALWAYS be written in English.

Write ONE guide step per screenshot. Return ONLY a JSON array (no markdown fences, no
commentary) with EXACTLY {count} elements, in the SAME ORDER as the screenshots. Element i
corresponds to "Screenshot i". Each element must have exactly this shape:
{{
    "title": "short feature/step name in English, e.g. 'Starting a Recording'",
    "bullets": ["2 to 4 short bullet points in English describing what this feature does and/or how to navigate to it"]
}}

Base each step on both that screenshot and its spoken context. If a screenshot shows a specific
UI element (button, menu, panel), name it and describe how to reach/use it. Keep bullets concise
- one short sentence each. Return exactly {count} elements even if some screenshots look similar.
"""


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text


def _find_spoken_text(timestamp: str, segments: list) -> str:
    """Find the transcript text whose start matches this timestamp (fallback: nearest)."""
    for seg in segments:
        if seg["start"] == timestamp:
            return seg["text"]
    return ""


def _step_cache_path(image_bytes: bytes, spoken_text: str) -> str:
    digest = hashlib.sha256(image_bytes + spoken_text.encode("utf-8")).hexdigest()
    return os.path.join(STEP_CACHE_DIR, f"{digest}.json")


def write_step(image_path: str, spoken_text: str, api_key: str = None, use_cache: bool = True) -> dict:
    """Ask Gemini to describe one screenshot as a navigation-guide step."""
    with open(image_path, "rb") as f:
        image_bytes = f.read()

    cache_file = _step_cache_path(image_bytes, spoken_text)
    if use_cache and os.path.exists(cache_file):
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)

    api_key = api_key or os.environ.get("GEMINIAPIKEY")
    if not api_key:
        raise RuntimeError(
            "Missing API key"
        )

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=60_000))

    image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
    prompt = STEP_PROMPT_TEMPLATE.format(spoken_text=spoken_text or "(no transcript text found)")

    response = generate_with_rate_limit(
        client,
        model=MODEL_NAME,
        contents=[image_part, prompt],
        config=types.GenerateContentConfig(temperature=0, seed=42),
    )

    raw_text = _strip_code_fences(response.text)
    try:
        step = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Gemini did not return valid JSON for step. Raw response:\n{raw_text}") from e

    title = step.get("title") or "Untitled Step"
    bullets = step.get("bullets") or []
    if not isinstance(bullets, list):
        bullets = [str(bullets)]
    cleaned = {"title": str(title), "bullets": [str(b) for b in bullets]}

    os.makedirs(STEP_CACHE_DIR, exist_ok=True)
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, indent=2)

    return cleaned


def _batch_cache_path(items: list) -> str:
    """Cache key for a whole batch: hash of every image's bytes + spoken text."""
    h = hashlib.sha256()
    for image_bytes, spoken_text in items:
        h.update(image_bytes)
        h.update(spoken_text.encode("utf-8"))
    return os.path.join(STEP_CACHE_DIR, f"batch_{h.hexdigest()}.json")


def _coerce_step(raw: dict) -> dict:
    title = (raw or {}).get("title") or "Untitled Step"
    bullets = (raw or {}).get("bullets") or []
    if not isinstance(bullets, list):
        bullets = [str(bullets)]
    return {"title": str(title), "bullets": [str(b) for b in bullets]}


def write_steps_batch(items: list, api_key: str = None, use_cache: bool = True) -> list:
    """Ask Gemini to describe ALL screenshots in a single call.

    items is a list of (image_path, spoken_text) tuples, in order. Returns a
    list of {"title", "bullets"} dicts, one per item, in the same order.

    This replaces the old one-call-per-screenshot loop: on the free tier, 14
    screenshots used to mean 14 rate-limited calls (~13s apart = minutes of
    waiting) and burned the daily quota. Batching makes it a single request.
    """
    if not items:
        return []

    # Read all image bytes up front (also needed for the cache key).
    loaded = []
    for image_path, spoken_text in items:
        with open(image_path, "rb") as f:
            loaded.append((f.read(), spoken_text or ""))

    cache_file = _batch_cache_path(loaded)
    if use_cache and os.path.exists(cache_file):
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)

    api_key = api_key or os.environ.get("GEMINIAPIKEY")
    if not api_key:
        raise RuntimeError("Missing API key")

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=120_000))

    # Interleave a labeled text marker + the image, for each screenshot.
    contents = [BATCH_STEP_PROMPT.format(count=len(loaded))]
    for i, (image_bytes, spoken_text) in enumerate(loaded, start=1):
        contents.append(f"--- Screenshot {i} ---\nSpoken context: {spoken_text or '(no transcript text found)'}")
        contents.append(types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"))

    response = generate_with_rate_limit(
        client,
        model=MODEL_NAME,
        contents=contents,
        config=types.GenerateContentConfig(temperature=0, seed=42),
    )

    raw_text = _strip_code_fences(response.text)
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Gemini did not return valid JSON for the batch. Raw response:\n{raw_text}") from e

    if not isinstance(parsed, list):
        raise ValueError(f"Expected a JSON array from Gemini, got: {type(parsed)}")

    # Map results back by position. If Gemini returned the wrong count, fill any
    # gaps with a placeholder so every screenshot still gets a page.
    steps = []
    for i in range(len(loaded)):
        raw = parsed[i] if i < len(parsed) and isinstance(parsed[i], dict) else {}
        steps.append(_coerce_step(raw))

    os.makedirs(STEP_CACHE_DIR, exist_ok=True)
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(steps, f, indent=2)

    return steps


def build_pdf(steps: list, output_path: str = "demo_guide.pdf", title: str = "Product Demo Guide"):
    """Lay out screenshot + title + bullets for each step into a Hitachi-branded PDF."""
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=20)

    # Cover page
    pdf.add_page()
    pdf.set_fill_color(*HITACHI_RED)
    pdf.rect(0, 0, 210, 40, style="F")
    pdf.set_xy(0, 15)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 24)
    pdf.cell(0, 15, "Hitachi", align="C", ln=1)
    pdf.set_xy(0, 60)
    pdf.set_text_color(*HITACHI_DARK_GRAY)
    pdf.set_font("Helvetica", "B", 18)
    pdf.multi_cell(0, 10, title, align="C")
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(120, 120, 120)
    pdf.ln(4)
    pdf.multi_cell(0, 7, "Auto-generated navigation guide from the recorded demo call.", align="C")

    for i, step in enumerate(steps, start=1):
        pdf.add_page()

        # Header bar with step number
        pdf.set_fill_color(*HITACHI_RED)
        pdf.rect(0, 0, 210, 16, style="F")
        pdf.set_xy(10, 3)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 10, f"Step {i} of {len(steps)}", ln=1)

        pdf.ln(14)
        pdf.set_text_color(*HITACHI_DARK_GRAY)
        pdf.set_font("Helvetica", "B", 16)
        pdf.multi_cell(0, 9, step["title"])
        pdf.ln(2)

        if step.get("timestamp"):
            pdf.set_font("Helvetica", "I", 9)
            pdf.set_text_color(150, 150, 150)
            pdf.cell(0, 6, f"Timestamp: {step['timestamp']}", ln=1)
            pdf.ln(2)

        screenshot_path = step.get("screenshot_path")
        if screenshot_path and os.path.exists(screenshot_path):
            page_width = pdf.w - 20  # 10mm margins each side
            pdf.image(screenshot_path, x=10, w=page_width)
            pdf.ln(6)

        pdf.set_text_color(*HITACHI_DARK_GRAY)
        pdf.set_font("Helvetica", "", 11)
        for bullet in step.get("bullets", []):
            pdf.set_x(14)
            pdf.multi_cell(0, 7, f"-  {bullet}")
        pdf.ln(2)

    pdf.output(output_path)
    return output_path


def generate_navigation_pdf(
    video_path: str,
    vtt_path: str,
    output_pdf: str = "demo_guide.pdf",
    screenshots_dir: str = "screenshots",
    use_cache: bool = True,
) -> str:
    """Full pipeline: transcript -> key moments -> screenshots -> Gemini step writeups -> PDF."""
    print("Extracting key moments from transcript...")
    key_moments = extract_key_moments_from_vtt(vtt_path, use_cache=use_cache)
    print(f"Found {len(key_moments)} key moment(s).")

    print("Capturing screenshots...")
    moments_with_screenshots = extract_screenshots(video_path, key_moments, screenshots_dir)

    segments = parse_vtt(vtt_path)

    # Keep only moments that actually got a screenshot, preserving order.
    captured = [m for m in moments_with_screenshots if m.get("screenshot_path")]
    for m in moments_with_screenshots:
        if not m.get("screenshot_path"):
            print(f"  Skipping {m['timestamp']} - no screenshot captured.")

    print(f"Writing guide steps with Gemini (1 batched call for {len(captured)} screenshot(s))...")
    batch_items = [
        (m["screenshot_path"], _find_spoken_text(m["timestamp"], segments)) for m in captured
    ]
    written = write_steps_batch(batch_items, use_cache=use_cache)

    steps = []
    for moment, step in zip(captured, written):
        step["timestamp"] = moment["timestamp"]
        step["screenshot_path"] = moment["screenshot_path"]
        steps.append(step)
        print(f"  [{moment['timestamp']}] {step['title']}")

    print(f"Building PDF ({len(steps)} step(s))...")
    output_path = build_pdf(steps, output_pdf)
    print(f"Saved: {output_path}")
    return output_path


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python pdf_report_generator.py <recording.mp4> <transcript.vtt> [output.pdf] [--no-cache]")
        sys.exit(1)

    video_path = sys.argv[1]
    vtt_path = sys.argv[2]
    positional_rest = [a for a in sys.argv[3:] if not a.startswith("--")]
    output_pdf = positional_rest[0] if positional_rest else "demo_guide.pdf"
    use_cache = "--no-cache" not in sys.argv[3:]

    if not os.path.exists(video_path):
        print(f"Error: video file not found: {video_path}")
        sys.exit(1)

    try:
        generate_navigation_pdf(video_path, vtt_path, output_pdf, use_cache=use_cache)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
