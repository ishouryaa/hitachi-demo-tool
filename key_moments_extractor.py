import hashlib
import json
import os
import re
import sys
import tempfile
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types

from transcript_parser import parse_vtt

load_dotenv()

MODEL_NAME = "gemini-3.1-flash-lite"
# Deployed environments (e.g. Azure Functions) run from a read-only folder -
# the system temp dir is writable everywhere, including locally.
CACHE_DIR = os.path.join(tempfile.gettempdir(), "hitachi_key_moments_cache")

# Free-tier Gemini quota is 5 requests/minute for this model. Space calls out
# so a demo with many key moments (one Gemini call per step) doesn't burst
# past that, and retry with backoff as a safety net if we still hit a 429.
MIN_SECONDS_BETWEEN_CALLS = 13
_last_call_time = 0


def generate_with_rate_limit(client, max_attempts: int = 5, **kwargs):
    """Wraps client.models.generate_content with proactive spacing and
    429 (RESOURCE_EXHAUSTED) retry/backoff.
    """
    global _last_call_time

    for attempt in range(1, max_attempts + 1):
        elapsed = time.time() - _last_call_time
        if elapsed < MIN_SECONDS_BETWEEN_CALLS:
            time.sleep(MIN_SECONDS_BETWEEN_CALLS - elapsed)

        try:
            response = client.models.generate_content(**kwargs)
            _last_call_time = time.time()
            return response
        except Exception as e:
            _last_call_time = time.time()
            msg = str(e)
            if "RESOURCE_EXHAUSTED" not in msg and "429" not in msg:
                raise
            # A per-day quota won't reset in seconds no matter how many times
            # we retry - fail fast instead of burning minutes (and risking
            # the whole function timing out) on retries that can't succeed.
            if "PerDay" in msg:
                raise RuntimeError(
                    "Gemini free-tier DAILY quota exhausted for this API key/project. "
                    "This will not resolve by retrying - wait for the daily reset or "
                    "use a different key/project. Original error: " + msg
                ) from e
            if attempt == max_attempts:
                raise
            delay_match = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+)", msg)
            delay = int(delay_match.group(1)) + 3 if delay_match else 15 * attempt
            print(f"  Gemini rate limit hit, retrying in {delay}s (attempt {attempt}/{max_attempts})...")
            time.sleep(delay)

PROMPT_TEMPLATE = """You are reviewing the transcript of a product demo call between a
company representative and a client. The transcript is a JSON list of timestamped
segments, each with "start", "end", "speaker", and "text".

The transcript may be in English, Hindi, or a mix of both (including speakers
switching languages mid-sentence). Understand and analyze all of it regardless of
language - but no matter what language the source text is in, your "description"
output must always be written in English.

Read through the ENTIRE transcript below and identify every moment where the speaker
is describing something important and visual/actionable in the demo - specifically:
mentioning a feature, telling the viewer where to click, where to navigate to, what
button/menu/screen to look at, or any other "look at this / do this" moment.

Ignore filler, small talk, greetings, and purely conversational segments that don't
reference something on-screen or actionable.

Return ONLY a JSON array (no markdown fences, no commentary) where each element has
exactly this shape:
{{
    "timestamp": "HH:MM:SS.mmm",
    "description": "short description in English of what's happening / important here",
    "category": "feature" | "navigation" | "click_action" | "other"
}}

The "timestamp" field must exactly match the "start" field of the transcript segment
the moment came from.

Transcript segments:
{segments_json}
"""


def _strip_code_fences(text: str) -> str: #avoids wrapping json file wrong
    text = text.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text


def _cache_path(segments: list) -> str:
    digest = hashlib.sha256(json.dumps(segments, sort_keys=True).encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, f"{digest}.json")


def extract_key_moments(segments: list, api_key: str = None, use_cache: bool = True) -> list:
    cache_file = _cache_path(segments)
    if use_cache and os.path.exists(cache_file):
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)

    api_key = api_key or os.environ.get("GEMINIAPIKEY")
    if not api_key:
        raise RuntimeError(
            "Missing API Key"
        )

    # Cap request time so a stalled connection fails fast instead of hanging forever.
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=60_000))

    prompt = PROMPT_TEMPLATE.format(segments_json=json.dumps(segments, indent=2))

    response = generate_with_rate_limit(
        client,
        model=MODEL_NAME,
        contents=prompt,
        config=types.GenerateContentConfig(temperature=0, seed=42),
    )

    raw_text = _strip_code_fences(response.text)

    try:
        key_moments = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Gemini did not return valid JSON. Raw response:\n{raw_text}"
        ) from e

    if not isinstance(key_moments, list):
        raise ValueError(f"Expected a JSON array from Gemini, got: {type(key_moments)}")

    valid_categories = {"feature", "navigation", "click_action", "other"}
    cleaned = []
    for moment in key_moments:
        if not isinstance(moment, dict):
            continue
        timestamp = moment.get("timestamp")
        description = moment.get("description")
        category = moment.get("category", "other")
        if category not in valid_categories:
            category = "other"
        if timestamp and description:
            cleaned.append({
                "timestamp": timestamp,
                "description": description,
                "category": category,
            })

    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, indent=2)

    return cleaned


def extract_key_moments_from_vtt(vtt_path: str, api_key: str = None, use_cache: bool = True) -> list:
    """Convenience wrapper: parse a .vtt file and extract key moments from it."""
    segments = parse_vtt(vtt_path)
    return extract_key_moments(segments, api_key=api_key, use_cache=use_cache)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python key_moments_extractor.py <transcript.vtt> [--no-cache]")
        sys.exit(1)

    vtt_path = sys.argv[1]
    use_cache = "--no-cache" not in sys.argv[2:]

    try:
        moments = extract_key_moments_from_vtt(vtt_path, use_cache=use_cache)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

    print(json.dumps(moments, indent=2))
    print(f"\nFound {len(moments)} key moment(s).")
