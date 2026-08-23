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
from PIL import Image

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
language, but all output must ALWAYS be written in English.

First, having now seen every screenshot and spoken segment in this demo, write ONE short
overview title (3-8 words) naming the specific product/feature area this demo guide covers as a
whole - e.g. "CRM Dashboard & Reporting Walkthrough", not a generic label like "Product Demo".

Then write ONE guide step per screenshot, each with EXACTLY 2 bullet points - not fewer, not
more. Each guide is printed two-screenshots-per-page, so space per step is limited: make every
bullet a complete, information-dense sentence that combines related details rather than
splitting them across more bullets.

Return ONLY a JSON object (no markdown fences, no commentary) with EXACTLY this shape:
{{
    "overview_title": "short overview title in English",
    "steps": [
        {{"title": "short feature/step name in English, e.g. 'Starting a Recording'", "bullets": ["bullet 1", "bullet 2"]}}
    ]
}}

"steps" must have EXACTLY {count} elements, in the SAME ORDER as the screenshots - element i
corresponds to "Screenshot i". Base each step on both that screenshot and its spoken context. If
a screenshot shows a specific UI element (button, menu, panel), name it and describe how to
reach/use it. Return exactly {count} step elements even if some screenshots look similar.
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


def write_steps_batch(items: list, api_key: str = None, use_cache: bool = True) -> dict:
    """Ask Gemini to describe ALL screenshots in a single call.

    items is a list of (image_path, spoken_text) tuples, in order. Returns
    {"overview_title": str, "steps": [{"title", "bullets"}, ...]}, with steps
    in the same order as items.

    This replaces the old one-call-per-screenshot loop: on the free tier, 14
    screenshots used to mean 14 rate-limited calls (~13s apart = minutes of
    waiting) and burned the daily quota. Batching makes it a single request -
    and since that one call already sees every screenshot, it also writes the
    overview_title, instead of spending a separate Gemini call on that.
    """
    if not items:
        return {"overview_title": "", "steps": []}

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

    if not isinstance(parsed, dict):
        raise ValueError(f"Expected a JSON object from Gemini, got: {type(parsed)}")

    overview_title = str(parsed.get("overview_title") or "").strip()
    raw_steps = parsed.get("steps")
    if not isinstance(raw_steps, list):
        raise ValueError(f"Expected a 'steps' array from Gemini, got: {type(raw_steps)}")

    # Map results back by position. If Gemini returned the wrong count, fill any
    # gaps with a placeholder so every screenshot still gets a page.
    steps = []
    for i in range(len(loaded)):
        raw = raw_steps[i] if i < len(raw_steps) and isinstance(raw_steps[i], dict) else {}
        steps.append(_coerce_step(raw))

    result = {"overview_title": overview_title, "steps": steps}

    os.makedirs(STEP_CACHE_DIR, exist_ok=True)
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    return result


PAGE_MARGIN = 10
CONTENT_WIDTH = 210 - 2 * PAGE_MARGIN
BLOCK_TOP_MARGIN = 14
BLOCK_HEIGHT = 132  # mm - two of these + a divider fit one A4 page
BLOCK_GAP = 8
MAX_RENDERED_BULLETS = 2


def _fit_image_size(image_path: str, max_w: float, max_h: float) -> tuple:
    """Returns (w, h) in mm that fit image_path within max_w x max_h, preserving aspect ratio."""
    with Image.open(image_path) as im:
        iw, ih = im.size
    scale = min(max_w / iw, max_h / ih)
    return iw * scale, ih * scale


def _render_step_block(pdf: FPDF, step: dict, index: int, total: int, y_top: float) -> None:
    """Renders one step (header, title, image, bullets) into a fixed-height
    block starting at y_top, so exactly two blocks stack onto one A4 page."""
    pdf.set_fill_color(*HITACHI_RED)
    pdf.rect(0, y_top, 210, 10, style="F")
    pdf.set_xy(PAGE_MARGIN, y_top + 1.5)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 7, f"Step {index} of {total}")

    cursor_y = y_top + 13
    pdf.set_xy(PAGE_MARGIN, cursor_y)
    pdf.set_text_color(*HITACHI_DARK_GRAY)
    pdf.set_font("Helvetica", "B", 13)
    pdf.multi_cell(CONTENT_WIDTH, 6, step["title"])
    cursor_y = pdf.get_y() + 1

    if step.get("timestamp"):
        pdf.set_xy(PAGE_MARGIN, cursor_y)
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(150, 150, 150)
        pdf.cell(0, 4, f"Timestamp: {step['timestamp']}")
        cursor_y += 5

    bullets = [b for b in step.get("bullets", []) if b][:MAX_RENDERED_BULLETS]
    bullets_height = len(bullets) * 5.5 + 2

    screenshot_path = step.get("screenshot_path")
    if screenshot_path and os.path.exists(screenshot_path):
        max_img_h = (y_top + BLOCK_HEIGHT) - cursor_y - bullets_height - 3
        w, h = _fit_image_size(screenshot_path, CONTENT_WIDTH, max(max_img_h, 20))
        x = PAGE_MARGIN + (CONTENT_WIDTH - w) / 2
        pdf.image(screenshot_path, x=x, y=cursor_y + 2, w=w, h=h)
        cursor_y = cursor_y + 2 + h + 3

    pdf.set_text_color(*HITACHI_DARK_GRAY)
    pdf.set_font("Helvetica", "", 10)
    for bullet in bullets:
        pdf.set_xy(PAGE_MARGIN + 4, cursor_y)
        pdf.multi_cell(CONTENT_WIDTH - 4, 5.2, f"-  {bullet}")
        cursor_y = pdf.get_y()


def build_pdf(steps: list, output_path: str = "navigation_guide.pdf", subject_title: str = None):
    """Lay out two screenshot+title+bullets steps per page into a Hitachi-branded PDF."""
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=False)

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
    pdf.multi_cell(0, 10, "Product Demo Guide", align="C")
    if subject_title:
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(*HITACHI_RED)
        pdf.ln(2)
        pdf.multi_cell(0, 8, subject_title, align="C")
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(120, 120, 120)
    pdf.ln(4)
    pdf.multi_cell(0, 7, "Auto-generated navigation guide from the recorded demo call.", align="C")

    for start in range(0, len(steps), 2):
        pair = steps[start:start + 2]
        pdf.add_page()
        _render_step_block(pdf, pair[0], start + 1, len(steps), BLOCK_TOP_MARGIN)

        if len(pair) > 1:
            divider_y = BLOCK_TOP_MARGIN + BLOCK_HEIGHT + BLOCK_GAP / 2
            pdf.set_draw_color(220, 220, 220)
            pdf.line(PAGE_MARGIN, divider_y, 210 - PAGE_MARGIN, divider_y)
            second_y = BLOCK_TOP_MARGIN + BLOCK_HEIGHT + BLOCK_GAP
            _render_step_block(pdf, pair[1], start + 2, len(steps), second_y)

    pdf.output(output_path)
    return output_path


def generate_navigation_pdf(
    video_path: str,
    vtt_path: str,
    output_pdf: str = "navigation_guide.pdf",
    screenshots_dir: str = "screenshots",
    use_cache: bool = True,
) -> dict:
    """Full pipeline: transcript -> key moments -> screenshots -> Gemini step writeups -> PDF.

    Returns {"path": output_pdf, "title": overview_title} - the title is
    Gemini's short summary of what the demo covers, written onto the cover
    page and available to callers for naming the downloaded file.
    """
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
    batch_result = write_steps_batch(batch_items, use_cache=use_cache)
    overview_title = batch_result.get("overview_title") or ""
    written = batch_result.get("steps", [])

    steps = []
    for moment, step in zip(captured, written):
        step["timestamp"] = moment["timestamp"]
        step["screenshot_path"] = moment["screenshot_path"]
        steps.append(step)
        print(f"  [{moment['timestamp']}] {step['title']}")

    print(f"Building PDF ({len(steps)} step(s)) - \"{overview_title or 'Product Demo Guide'}\"...")
    output_path = build_pdf(steps, output_pdf, subject_title=overview_title)
    print(f"Saved: {output_path}")
    return {"path": output_path, "title": overview_title}


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python pdf_report_generator.py <recording.mp4> <transcript.vtt> [output.pdf] [--no-cache]")
        sys.exit(1)

    video_path = sys.argv[1]
    vtt_path = sys.argv[2]
    positional_rest = [a for a in sys.argv[3:] if not a.startswith("--")]
    output_pdf = positional_rest[0] if positional_rest else "navigation_guide.pdf"
    use_cache = "--no-cache" not in sys.argv[3:]

    if not os.path.exists(video_path):
        print(f"Error: video file not found: {video_path}")
        sys.exit(1)

    try:
        generate_navigation_pdf(video_path, vtt_path, output_pdf, use_cache=use_cache)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
