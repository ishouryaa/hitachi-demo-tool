"""
generate_full_explainer.py

Builds full_code_explainer.pdf: a single, self-contained, line-by-line
walkthrough of every file in the hitachi-demo-tool project as it currently
stands. This is meant to be read on its own - it does not assume the reader
has seen any earlier version of the code or any other document.

Unlike a hand-copied walkthrough, this script READS THE ACTUAL SOURCE FILES at
build time and slices them into the blocks shown below. That means the code in
the PDF always matches the code on disk - regenerate it any time the code
changes and it stays correct.

Run once: python generate_full_explainer.py
Not part of the actual demo-tool pipeline - just documentation tooling.
"""

import os

from fpdf import FPDF

RED = (230, 0, 40)
DARK_GRAY = (40, 40, 40)
MID_GRAY = (90, 90, 90)
LIGHT_GRAY_BG = (245, 245, 245)
CODE_BORDER = (225, 225, 225)

BASE = os.path.dirname(os.path.abspath(__file__))
_FILE_CACHE = {}


def load(path):
    """Read a source file (relative to this script) into a list of lines."""
    if path not in _FILE_CACHE:
        with open(os.path.join(BASE, path), encoding="utf-8") as f:
            _FILE_CACHE[path] = f.read().split("\n")
    return _FILE_CACHE[path]


# ---------------------------------------------------------------------------
# PDF building blocks
# ---------------------------------------------------------------------------
class ExplainerPDF(FPDF):
    def header(self):
        pass

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(160, 160, 160)
        self.cell(0, 8, f"Page {self.page_no()}", align="C")


def cover_page(pdf, sections):
    pdf.add_page()
    pdf.set_fill_color(*RED)
    pdf.rect(0, 0, 210, 45, style="F")
    pdf.set_xy(0, 15)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 24)
    pdf.cell(0, 15, "Hitachi Demo Tool", align="C", ln=1)
    pdf.set_font("Helvetica", "", 13)
    pdf.cell(0, 8, "Full Code Walkthrough", align="C", ln=1)

    pdf.set_xy(15, 60)
    pdf.set_text_color(*MID_GRAY)
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(
        0, 6,
        "This document explains, in plain language and line by line, exactly what "
        "every file in the hitachi-demo-tool folder does. It is written to be read on "
        "its own, start to finish, and referred back to whenever a refresher on how a "
        "file works is needed. The code shown is read straight from the source files, "
        "so it always matches what is actually running."
    )

    pdf.ln(6)
    pdf.set_x(15)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*DARK_GRAY)
    pdf.cell(0, 8, "Contents", ln=1)
    pdf.set_font("Helvetica", "", 10.5)
    for label in sections:
        pdf.set_x(15)
        pdf.cell(0, 7, f"-  {label}", ln=1)


def section_title(pdf, number, title, subtitle=None):
    pdf.add_page()
    pdf.set_fill_color(*RED)
    pdf.rect(0, 0, 210, 22, style="F")
    pdf.set_xy(10, 5)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 10, f"{number}. {title}", ln=1)
    pdf.ln(14)
    if subtitle:
        pdf.set_text_color(*MID_GRAY)
        pdf.set_font("Helvetica", "I", 10.5)
        pdf.multi_cell(0, 6, subtitle)
        pdf.ln(2)


def sub_heading(pdf, text):
    pdf.ln(2)
    pdf.set_text_color(*RED)
    pdf.set_font("Helvetica", "B", 12)
    pdf.multi_cell(0, 7, text)
    pdf.set_text_color(*DARK_GRAY)
    pdf.ln(1)


def _wrap(line, width=94):
    """Soft-wrap a long code line onto continuation lines so nothing is cut off."""
    if len(line) <= width:
        return [line]
    out = []
    while len(line) > width:
        out.append(line[:width])
        line = "    " + line[width:]
    out.append(line)
    return out


def code_block(pdf, code):
    pdf.set_font("Courier", "", 8.5)
    raw_lines = code.strip("\n").split("\n")
    lines = []
    for ln in raw_lines:
        lines.extend(_wrap(ln))

    line_h = 4.2
    box_h = line_h * len(lines) + 4

    # If the block won't fit on what's left of the page, start a new page first
    # so the grey box never gets clipped by the auto page break.
    if pdf.get_y() + box_h > pdf.h - 18:
        pdf.add_page()

    x0, y0 = pdf.get_x(), pdf.get_y()
    pdf.set_fill_color(*LIGHT_GRAY_BG)
    pdf.set_draw_color(*CODE_BORDER)
    pdf.rect(x0, y0, pdf.w - 20, box_h, style="DF")
    pdf.set_xy(x0 + 3, y0 + 2)
    pdf.set_text_color(*DARK_GRAY)
    for line in lines:
        pdf.set_x(x0 + 3)
        pdf.cell(0, line_h, line, ln=1)
    pdf.set_xy(x0, y0 + box_h + 3)
    pdf.set_text_color(*DARK_GRAY)


def block(pdf, path, start, end):
    """Show lines start..end (1-indexed, inclusive) of a source file."""
    lines = load(path)
    code_block(pdf, "\n".join(lines[start - 1:end]))


def explain(pdf, bullets):
    pdf.set_font("Helvetica", "", 10.5)
    pdf.set_text_color(*DARK_GRAY)
    for b in bullets:
        pdf.set_x(14)
        pdf.multi_cell(0, 5.6, f"-  {b}")
    pdf.ln(2)


def para(pdf, text):
    pdf.set_font("Helvetica", "", 10.5)
    pdf.set_text_color(*DARK_GRAY)
    pdf.multi_cell(0, 5.6, text)
    pdf.ln(1)


def summary_box(pdf, text):
    pdf.ln(2)
    x0, y0 = 10, pdf.get_y()
    pdf.set_fill_color(*RED)
    pdf.rect(x0, y0, 2, 14, style="F")
    pdf.set_xy(x0 + 6, y0)
    pdf.set_font("Helvetica", "B", 10.5)
    pdf.set_text_color(*DARK_GRAY)
    pdf.multi_cell(pdf.w - 30, 5.6, text)
    pdf.ln(3)


# ===========================================================================
# Build the document
# ===========================================================================
pdf = ExplainerPDF(orientation="P", unit="mm", format="A4")
pdf.set_auto_page_break(auto=True, margin=18)

sections = [
    "Introduction - what this tool does",
    "1. graph_client.py - talking to Microsoft Teams / Graph API",
    "2. transcript_parser.py - reading the .vtt transcript file",
    "3. key_moments_extractor.py - asking Gemini to find important moments",
    "4. screenshot_extractor.py - grabbing video frames with OpenCV",
    "5. pdf_report_generator.py - writing steps and building the PDF",
    "6. function_app.py - the Azure Function + web form (async job flow)",
    "7. blob_storage.py - saving PDFs and tracking job status",
    "8. Configuration - requirements.txt, host.json, .funcignore, local.settings.json",
    "9. .env - local secrets",
    "10. The full pipeline, end to end",
    "11. Recent major changes (async + batching) and quirks worth knowing",
]
cover_page(pdf, sections)

# ---------------------------------------------------------------------------
# Introduction
# ---------------------------------------------------------------------------
pdf.add_page()
pdf.set_text_color(*RED)
pdf.set_font("Helvetica", "B", 16)
pdf.cell(0, 10, "Introduction", ln=1)
pdf.ln(2)

para(
    pdf,
    "hitachi-demo-tool takes a recorded Microsoft Teams demo call and turns it into a "
    "Hitachi-branded PDF 'navigation guide': a series of pages, each showing a screenshot "
    "from the call plus a short AI-written title and bullet points describing what's "
    "happening at that moment. The idea is that after a salesperson demos a product to a "
    "client over Teams, this tool automatically produces a leave-behind document the client "
    "can use to remember how to navigate the product themselves."
)

para(pdf, "The core pipeline runs in five stages, in this order:")
explain(pdf, [
    "Fetch the recording (.mp4) and transcript (.vtt) for a given Teams meeting from "
    "Microsoft Graph - graph_client.py.",
    "Parse the transcript file into a list of timestamped, speaker-tagged text segments - "
    "transcript_parser.py.",
    "Send the whole transcript to Google's Gemini AI and ask it to pick out the "
    "'key moments' worth screenshotting - key_moments_extractor.py.",
    "Open the video and grab a screenshot at each key moment's timestamp - "
    "screenshot_extractor.py.",
    "Ask Gemini (in a single batched call) to write a short title and bullets for every "
    "screenshot, then lay everything out into a branded PDF - pdf_report_generator.py.",
])

para(
    pdf,
    "On top of that pipeline, the project is wrapped as an Azure Function App "
    "(function_app.py) so anyone in the organization can trigger it from a simple web form, "
    "without installing Python or touching a terminal. Because the whole pipeline can take "
    "several minutes - longer than Azure's ~230-second limit on a single web request - the "
    "work runs on a background thread and the web page politely polls for the result. Every "
    "finished PDF is also saved to Azure Blob Storage, which doubles as the place job status "
    "is tracked (blob_storage.py)."
)

summary_box(
    pdf,
    "Read this document top to bottom once, then use the Contents list on the cover page "
    "to jump back to whichever file you need a refresher on."
)

# ---------------------------------------------------------------------------
# 1. graph_client.py
# ---------------------------------------------------------------------------
section_title(
    pdf, 1, "graph_client.py",
    "Talks to Microsoft Graph to find a Teams meeting and download its recording and transcript."
)

sub_heading(pdf, "Imports and setup (lines 1-11)")
block(pdf, "graph_client.py", 1, 11)
explain(pdf, [
    "os reads environment variables (secrets/config); requests is the HTTP library used for "
    "every call to Microsoft Graph.",
    "load_dotenv() copies key=value pairs from a local .env file into the environment so "
    "os.environ.get(...) can see them. This only matters locally - on Azure the values come "
    "from App Settings instead.",
    "GRAPH_BASE is the root URL for Graph's v1.0 REST API; every call appends a path onto it.",
    "GraphConfigError is a custom exception raised when required credentials are missing, so "
    "callers can tell that apart from other failures.",
])

sub_heading(pdf, "GraphClient.__init__ - reading credentials (lines 13-41)")
block(pdf, "graph_client.py", 13, 41)
explain(pdf, [
    "On creation, the client reads four values from the environment: the tenant ID, client "
    "ID and client secret (the app's identity in Azure AD), plus an optional default user ID.",
    "default_user_id is only a fallback for single-user local testing. In the deployed tool "
    "the meeting organizer is passed in per call, so it works for anyone in the tenant.",
    "It builds a list of any required variables that are missing and, if the list isn't "
    "empty, raises GraphConfigError with a clear message instead of failing cryptically later.",
    "_token, _headers and _object_id_cache start empty - they're filled lazily the first time "
    "they're needed.",
])

sub_heading(pdf, "Resolving who the organizer is (lines 43-69)")
block(pdf, "graph_client.py", 43, 69)
explain(pdf, [
    "The recordings/transcripts endpoints need the organizer's directory object ID (a GUID) - "
    "they will NOT accept an email address directly, unlike most Graph endpoints.",
    "_resolve_user_id picks whatever organizer we were given (or the default), and if it looks "
    "like an email (contains '@'), converts it into the GUID via _resolve_object_id.",
    "_resolve_object_id calls GET /users/{email} to look up the real object ID, and caches the "
    "result so repeated calls in one run don't hit Graph again.",
])

sub_heading(pdf, "Authentication - getting an access token (lines 71-96)")
block(pdf, "graph_client.py", 71, 96)
explain(pdf, [
    "_get_token performs the OAuth 'client credentials' flow: it POSTs the app's ID and secret "
    "to Azure AD and gets back an app-only access token (no user sign-in involved).",
    "The headers property is lazy: the first time it's read it fetches a token and builds the "
    "Authorization header; after that it reuses the cached header.",
    "refresh_token forces a brand-new token, useful if a call ever fails with a 401.",
])

sub_heading(pdf, "Finding the meeting by its join URL (lines 98-111)")
block(pdf, "graph_client.py", 98, 111)
explain(pdf, [
    "Given the Teams join link the user pasted, this asks Graph for the online meeting whose "
    "JoinWebUrl matches, filtered under the organizer's user ID.",
    "Any single quotes in the URL are doubled ('') because the value sits inside a quoted OData "
    "$filter string - this avoids breaking the query.",
    "If no meeting matches, it raises a clear ValueError instead of returning nothing.",
])

sub_heading(pdf, "Listing and downloading recordings & transcripts (lines 113-152)")
block(pdf, "graph_client.py", 113, 152)
explain(pdf, [
    "list_recordings / list_transcripts return the available recording or transcript entries "
    "for a meeting; download_recording / download_transcript fetch the actual file content.",
    "Transcripts are requested with an 'Accept: text/vtt' header so Graph returns the WebVTT "
    "caption format this tool knows how to parse.",
    "_download is the shared helper: it streams the response to disk in 8 KB chunks so large "
    "video files don't have to be held entirely in memory.",
])

sub_heading(pdf, "fetch_meeting_artifacts - the one function callers use (lines 155-183)")
block(pdf, "graph_client.py", 155, 183)
explain(pdf, [
    "This is the convenient end-to-end entry point the rest of the app calls. Give it a join "
    "URL and organizer email and it does everything: find the meeting, then download the first "
    "recording and first transcript into out_dir.",
    "It returns a dict with the meeting ID, subject, and the local paths to the downloaded "
    "recording and transcript (either path is None if that artifact wasn't available yet).",
])

sub_heading(pdf, "Command-line entry point (lines 186-204)")
block(pdf, "graph_client.py", 186, 204)
explain(pdf, [
    "Lets you run this file directly for testing: python graph_client.py <join_url> "
    "[organizer_email] [output_dir], and it prints where the recording and transcript landed.",
])

# ---------------------------------------------------------------------------
# 2. transcript_parser.py
# ---------------------------------------------------------------------------
section_title(
    pdf, 2, "transcript_parser.py",
    "Turns the raw WebVTT (.vtt) caption file into a clean list of timestamped, speaker-tagged segments."
)

sub_heading(pdf, "The regular expressions (lines 1-6)")
block(pdf, "transcript_parser.py", 1, 6)
explain(pdf, [
    "CUE_TIME_RE matches a VTT timing line like '00:01:23.456 --> 00:01:25.789' and captures "
    "both the start and end timestamps.",
    "VOICE_TAG_RE matches Teams' speaker tags, which wrap text like <v Speaker Name>...</v>, "
    "capturing the speaker name and the words separately.",
])

sub_heading(pdf, "parse_vtt - splitting the file into segments (lines 9-52)")
block(pdf, "transcript_parser.py", 9, 52)
explain(pdf, [
    "The file is split into blocks on blank lines - each block is one caption 'cue'.",
    "The 'WEBVTT' header line and any block without a timing line or text is skipped.",
    "For each cue it pulls out the start/end times, joins the text lines, and if there's a "
    "<v ...> speaker tag it separates the speaker name from what they said.",
    "Each segment becomes a dict: {start, end, speaker, text}. The list of these dicts is what "
    "the rest of the pipeline works with.",
])

sub_heading(pdf, "Command-line entry point (lines 55-65)")
block(pdf, "transcript_parser.py", 55, 65)
explain(pdf, [
    "Run directly (python transcript_parser.py transcript.vtt) it prints the parsed segments as "
    "JSON and a count - handy for eyeballing that parsing worked.",
])

# ---------------------------------------------------------------------------
# 3. key_moments_extractor.py
# ---------------------------------------------------------------------------
section_title(
    pdf, 3, "key_moments_extractor.py",
    "Sends the whole transcript to Gemini and asks it to list the important, screenshot-worthy moments."
)

sub_heading(pdf, "Imports, model and cache location (lines 1-20)")
block(pdf, "key_moments_extractor.py", 1, 20)
explain(pdf, [
    "Uses Google's google-genai SDK. MODEL_NAME picks the specific Gemini model - a fast, "
    "cheap 'flash-lite' model, chosen to stay within free-tier limits.",
    "Results are cached under the system temp directory (not the project folder) because on "
    "Azure the app runs from a read-only folder - only temp is writable everywhere.",
])

sub_heading(pdf, "Rate-limit constants and the retry wrapper (lines 22-63)")
block(pdf, "key_moments_extractor.py", 22, 63)
explain(pdf, [
    "The free Gemini tier allows only a handful of requests per minute, so MIN_SECONDS_BETWEEN"
    "_CALLS spaces calls out, and _last_call_time remembers when the last one happened.",
    "generate_with_rate_limit wraps every Gemini call: it waits if not enough time has passed, "
    "then makes the call, and on a 429 'RESOURCE_EXHAUSTED' error it retries with a backoff.",
    "Crucially, if the error is a PER-DAY quota (which won't reset in seconds), it fails fast "
    "with a clear message instead of wasting minutes retrying something that can't succeed.",
    "The retry delay is read from Gemini's own 'retryDelay' hint when present, otherwise it "
    "grows with each attempt.",
])

sub_heading(pdf, "The prompt sent to Gemini (lines 65-95)")
block(pdf, "key_moments_extractor.py", 65, 95)
explain(pdf, [
    "This is the instruction given to the model. It explains the transcript is from a product "
    "demo and may mix English and Hindi, but the output must always be in English.",
    "It asks Gemini to find every 'look at this / do this' moment - a feature mention, a click, "
    "a navigation - and ignore small talk.",
    "It demands a strict JSON array where each item has a timestamp (matching a segment's start "
    "exactly), a description, and a category. Asking for strict JSON makes the reply parseable.",
])

sub_heading(pdf, "Small helpers (lines 98-108)")
block(pdf, "key_moments_extractor.py", 98, 108)
explain(pdf, [
    "_strip_code_fences removes any ```json ... ``` markdown wrapper Gemini sometimes adds, so "
    "the text can be parsed as raw JSON.",
    "_cache_path builds a unique filename from a hash of the transcript segments - identical "
    "input reuses the same cache file.",
])

sub_heading(pdf, "extract_key_moments - the main call (lines 111-168)")
block(pdf, "key_moments_extractor.py", 111, 168)
explain(pdf, [
    "First it checks the cache: if we've already analyzed this exact transcript, it returns the "
    "saved result and skips the API call entirely.",
    "Otherwise it builds the Gemini client (with a 60-second timeout so a stuck connection "
    "fails fast), formats the prompt with the transcript JSON, and calls the rate-limited "
    "wrapper.",
    "It strips fences, parses the JSON, and validates each moment - keeping only ones with both "
    "a timestamp and description, and forcing the category to one of the four allowed values.",
    "The cleaned list is written to the cache before being returned.",
])

sub_heading(pdf, "Convenience wrapper and CLI (lines 171-192)")
block(pdf, "key_moments_extractor.py", 171, 192)
explain(pdf, [
    "extract_key_moments_from_vtt just parses a .vtt file first, then calls extract_key_moments "
    "on the segments - the form most callers use.",
    "The __main__ block lets you test it against a transcript file straight from the terminal.",
])

# ---------------------------------------------------------------------------
# 4. screenshot_extractor.py
# ---------------------------------------------------------------------------
section_title(
    pdf, 4, "screenshot_extractor.py",
    "Opens the recorded video and grabs a still frame at each key moment's timestamp using OpenCV."
)

sub_heading(pdf, "Imports and timestamp parsing (lines 1-19)")
block(pdf, "screenshot_extractor.py", 1, 19)
explain(pdf, [
    "cv2 is OpenCV, used to read frames from the video file.",
    "timestamp_to_seconds converts an 'HH:MM:SS.mmm' string into a float number of seconds, "
    "which is how OpenCV wants to be told where to seek. A bad format raises a clear ValueError.",
    "_safe_filename swaps colons for dashes so a timestamp can be used inside a filename.",
])

sub_heading(pdf, "capture_screenshot - one frame (lines 22-37)")
block(pdf, "screenshot_extractor.py", 22, 37)
explain(pdf, [
    "Opens the video, seeks to the requested moment (in milliseconds), reads one frame, and "
    "writes it out as a JPG.",
    "It returns True/False for success, makes sure the output folder exists first, and always "
    "releases the video handle in a finally block so the file isn't left open.",
])

sub_heading(pdf, "extract_screenshots - all moments (lines 40-61)")
block(pdf, "screenshot_extractor.py", 40, 61)
explain(pdf, [
    "Loops over every key moment, names each screenshot with its index and timestamp, and "
    "captures it.",
    "It returns a new list of the moments, each with a 'screenshot_path' added (or None if the "
    "frame couldn't be grabbed), and prints an OK/FAILED line for each.",
])

sub_heading(pdf, "Command-line entry point (lines 64-93)")
block(pdf, "screenshot_extractor.py", 64, 93)
explain(pdf, [
    "Run directly, it chains the previous step: extract key moments from a transcript, then "
    "capture a screenshot for each, reporting how many succeeded.",
])

# ---------------------------------------------------------------------------
# 5. pdf_report_generator.py
# ---------------------------------------------------------------------------
section_title(
    pdf, 5, "pdf_report_generator.py",
    "The orchestrator: runs the whole pipeline, asks Gemini to describe the screenshots, and builds the branded PDF."
)

sub_heading(pdf, "Imports, model and constants (lines 1-25)")
block(pdf, "pdf_report_generator.py", 1, 25)
explain(pdf, [
    "It pulls in the other pipeline modules (key moments, screenshots, transcript parsing) plus "
    "fpdf for PDF building and the Gemini SDK.",
    "It reuses generate_with_rate_limit from key_moments_extractor so step-writing obeys the "
    "same rate limits.",
    "STEP_CACHE_DIR (again under the writable temp dir) caches the AI descriptions. The two "
    "HITACHI_* colours define the red/grey brand look.",
])

sub_heading(pdf, "The two prompts (lines 27-67)")
block(pdf, "pdf_report_generator.py", 27, 67)
explain(pdf, [
    "STEP_PROMPT_TEMPLATE describes ONE screenshot - the older, one-call-per-image approach.",
    "BATCH_STEP_PROMPT is the new approach: it describes ALL screenshots in a single request "
    "and asks for a JSON array with exactly one element per screenshot, in order.",
    "Both insist the output title and bullets be in English even if the demo audio was Hindi.",
])

sub_heading(pdf, "Small helpers (lines 70-88)")
block(pdf, "pdf_report_generator.py", 70, 88)
explain(pdf, [
    "_strip_code_fences: same fence-removal trick as before.",
    "_find_spoken_text: given a moment's timestamp, finds the matching transcript segment's "
    "words to give Gemini context for that screenshot.",
    "_step_cache_path: cache filename for a single-image description.",
])

sub_heading(pdf, "write_step - describing one screenshot (lines 91-135)")
block(pdf, "pdf_report_generator.py", 91, 135)
explain(pdf, [
    "The original single-image path: check cache, else send the image bytes plus the spoken "
    "context to Gemini and parse back a {title, bullets} object.",
    "It's kept for reference and one-off use, but the live pipeline now uses the batched version "
    "below instead.",
])

sub_heading(pdf, "Batch helpers (lines 138-152)")
block(pdf, "pdf_report_generator.py", 138, 152)
explain(pdf, [
    "_batch_cache_path hashes every image plus its spoken text together, so re-running the same "
    "set of screenshots reuses one cached result.",
    "_coerce_step defensively normalises whatever Gemini returns into a clean {title, bullets} "
    "dict, even if a field is missing or the wrong type.",
])

sub_heading(pdf, "write_steps_batch - all screenshots in ONE call (lines 155-218)")
block(pdf, "pdf_report_generator.py", 155, 196)
explain(pdf, [
    "This is the key speed/cost fix. Instead of one Gemini call per screenshot (14 screenshots "
    "meant 14 rate-limited calls, minutes of waiting, and burning the daily quota), it sends "
    "them all at once.",
    "It reads every image's bytes, checks the batch cache, then builds a single 'contents' list "
    "that interleaves a labelled marker + the image for each screenshot.",
    "It makes one rate-limited Gemini call with the whole set.",
])
block(pdf, "pdf_report_generator.py", 198, 218)
explain(pdf, [
    "It parses the JSON array back, then maps results to screenshots by position. If Gemini "
    "returns the wrong number of items, gaps are filled with a placeholder so every screenshot "
    "still gets a page.",
    "The final list is cached and returned.",
])

sub_heading(pdf, "build_pdf - laying out the document (lines 221-280)")
block(pdf, "pdf_report_generator.py", 221, 241)
explain(pdf, [
    "Creates an A4 PDF and draws a branded cover page: the red Hitachi banner, the guide title, "
    "and a one-line subtitle.",
])
block(pdf, "pdf_report_generator.py", 243, 280)
explain(pdf, [
    "Then one page per step: a red header bar with 'Step N of M', the AI title, the timestamp, "
    "the screenshot scaled to the page width, and the bullet points.",
    "Finally it writes the file out and returns the path.",
])

sub_heading(pdf, "generate_navigation_pdf - the whole pipeline (lines 283-322)")
block(pdf, "pdf_report_generator.py", 283, 322)
explain(pdf, [
    "This ties everything together: extract key moments -> capture screenshots -> keep only the "
    "ones that produced an image -> pair each with its spoken text.",
    "It then makes the single batched Gemini call for all the descriptions, attaches each "
    "timestamp and screenshot path back onto the results, and calls build_pdf.",
    "The print statements are what you see streaming in the Azure logs while it runs.",
])

sub_heading(pdf, "Command-line entry point (lines 325-344)")
block(pdf, "pdf_report_generator.py", 325, 344)
explain(pdf, [
    "Run directly: python pdf_report_generator.py recording.mp4 transcript.vtt [output.pdf] "
    "[--no-cache] - useful for generating a PDF locally without going through Azure.",
])

# ---------------------------------------------------------------------------
# 6. function_app.py
# ---------------------------------------------------------------------------
section_title(
    pdf, 6, "function_app.py",
    "The Azure Function App: the web form plus the async job flow (start -> background thread -> poll for result)."
)

sub_heading(pdf, "Why the design looks the way it does (lines 1-46)")
block(pdf, "function_app.py", 1, 46)
explain(pdf, [
    "The long docstring explains the whole strategy. A single web request on Azure is cut off "
    "after ~230 seconds, but the pipeline can take longer, so the work can't happen inside one "
    "blocking request.",
    "Instead: run_demo_capture starts the work on a background thread and returns instantly; "
    "get_job_status is polled by the page until the PDF is ready.",
    "It also explains why a background thread is used instead of a queue-triggered function: on "
    "the Flex Consumption plan the queue worker often fails to scale up from zero, so the job "
    "would just sit there. Running on the already-warm web instance sidesteps that.",
])

sub_heading(pdf, "Imports and the app object (lines 48-61)")
block(pdf, "function_app.py", 48, 61)
explain(pdf, [
    "It imports the blob-storage helpers (upload + job status + download URL), the Graph fetch, "
    "and the PDF pipeline.",
    "app = func.FunctionApp(...) with FUNCTION auth means the HTTP endpoints require a key by "
    "default (individual routes can override this).",
])

sub_heading(pdf, "The web form + its JavaScript (lines 63-169)")
block(pdf, "function_app.py", 63, 90)
explain(pdf, [
    "FORM_HTML is the entire page served to users: two fields (join link, organizer email) and "
    "a Generate button, styled in Hitachi red.",
    "{function_key} is filled in server-side so the page can authenticate its API calls without "
    "the key being hard-coded in the repo.",
])
block(pdf, "function_app.py", 92, 135)
explain(pdf, [
    "pollStatus is the loop that checks /api/job-status every 4 seconds, up to ~10 minutes.",
    "When status becomes 'done' it navigates the browser to the returned download_url, which "
    "triggers the PDF download. On 'error' it shows the real error message. Otherwise it keeps "
    "showing 'Working...'.",
])
block(pdf, "function_app.py", 137, 168)
explain(pdf, [
    "The submit handler POSTs the join URL + email to run-demo-capture, gets back a job_id, and "
    "then hands that id to pollStatus. All the waiting happens client-side.",
])

sub_heading(pdf, "serve_form - serving the page (lines 172-181)")
block(pdf, "function_app.py", 172, 181)
explain(pdf, [
    "A GET on the app root returns the HTML form. It's marked ANONYMOUS so anyone with the link "
    "can open it; the sensitive calls the page makes are still key-protected.",
    "The function key is read from an app setting (RUN_DEMO_CAPTURE_KEY) and injected into the "
    "page. It must be a HOST key so it works for both API calls the page makes.",
])

sub_heading(pdf, "run_demo_capture - the fast start endpoint (lines 184-231)")
block(pdf, "function_app.py", 184, 231)
explain(pdf, [
    "Reads the join URL and organizer email from either query params or the JSON body, and "
    "returns a clear 400 if either is missing.",
    "It creates a random job_id, writes an initial 'queued' status, then starts _run_job on a "
    "daemon background thread and immediately returns the job_id with HTTP 202.",
    "Because it does no heavy work itself, this request finishes in well under a second - it can "
    "never hit the 230-second limit.",
])

sub_heading(pdf, "_run_job - the background worker (lines 234-298)")
block(pdf, "function_app.py", 234, 298)
explain(pdf, [
    "This is the pipeline, running off the web request on its own thread. It marks the job "
    "'running', fetches the meeting artifacts, generates the PDF, and uploads it.",
    "Every stage is wrapped so any failure writes an 'error' status with a readable message "
    "instead of dying silently - and a final catch-all guarantees the job never gets stuck with "
    "no terminal status.",
    "On success it writes 'done' along with the blob name of the saved PDF.",
    "It works inside a TemporaryDirectory so all the intermediate files are cleaned up "
    "automatically.",
])

sub_heading(pdf, "get_job_status - what the page polls (lines 301-327)")
block(pdf, "function_app.py", 301, 327)
explain(pdf, [
    "Marked ANONYMOUS on purpose: the job_id is an unguessable random UUID that only lives in "
    "the requesting browser, so it acts as its own access token. This avoided a key-mismatch "
    "bug that was breaking polling.",
    "It reads the job's status blob. If 'error', it returns the message; if 'done', it mints a "
    "short-lived download URL for the finished PDF; otherwise it just reports the current state.",
])

# ---------------------------------------------------------------------------
# 7. blob_storage.py
# ---------------------------------------------------------------------------
section_title(
    pdf, 7, "blob_storage.py",
    "Saves each finished PDF to Azure Blob Storage and stores job status, so the web layer can stay fast."
)

sub_heading(pdf, "Purpose, imports and constants (lines 1-29)")
block(pdf, "blob_storage.py", 1, 29)
explain(pdf, [
    "Two jobs: keep a central copy of every generated PDF, and track async job status as tiny "
    "JSON blobs so the HTTP functions don't have to hold state themselves.",
    "It reuses the storage account the Function App already has (via AzureWebJobsStorage) - no "
    "new resource or secret needed.",
    "Constants name the two containers (one for PDFs, one for status) and how long a download "
    "link stays valid (60 minutes).",
])

sub_heading(pdf, "Shared helpers (lines 32-50)")
block(pdf, "blob_storage.py", 32, 50)
explain(pdf, [
    "_sanitize_filename strips anything unsafe out of a meeting subject so it can be part of a "
    "blob name.",
    "_get_client builds the storage client from the connection string (raising clearly if it's "
    "not configured), and _get_container returns a container, creating it if it doesn't exist "
    "yet.",
])

sub_heading(pdf, "upload_pdf - saving the PDF (lines 53-70)")
block(pdf, "blob_storage.py", 53, 70)
explain(pdf, [
    "Names the blob with a timestamp plus the sanitized meeting subject and uploads the bytes.",
    "If storage isn't configured (e.g. running locally without the emulator) it just logs a "
    "warning and returns None rather than crashing the whole run.",
])

sub_heading(pdf, "generate_download_url - the no-login download link (lines 73-90)")
block(pdf, "blob_storage.py", 73, 90)
explain(pdf, [
    "Creates a SAS (Shared Access Signature) URL: a plain link that grants read access to just "
    "that one PDF for a limited time, with no Azure sign-in required.",
    "The content-disposition header makes the browser download it as a file named "
    "demo_guide.pdf instead of opening it inline. This is the link the page navigates to when "
    "the job is done.",
])

sub_heading(pdf, "write_job_status / read_job_status (lines 93-119)")
block(pdf, "blob_storage.py", 93, 119)
explain(pdf, [
    "write_job_status saves a small JSON blob named after the job_id, holding the status "
    "('queued'/'running'/'done'/'error'), a timestamp, and any extras (like the error text or "
    "the finished blob name).",
    "read_job_status fetches that blob back, returning None if the job_id isn't found. This is "
    "the tiny shared 'database' the start endpoint, the worker thread, and the poll endpoint all "
    "talk through.",
])

# ---------------------------------------------------------------------------
# 8. Configuration files
# ---------------------------------------------------------------------------
section_title(
    pdf, 8, "Configuration files",
    "The small files that tell Azure and pip how to build and run the app."
)

sub_heading(pdf, "requirements.txt - Python dependencies")
block(pdf, "requirements.txt", 1, len(load("requirements.txt")))
explain(pdf, [
    "The exact set of libraries Azure installs when it builds the app: azure-functions and "
    "azure-storage-blob for the platform and storage, requests for Graph, python-dotenv for "
    "local secrets, google-genai for Gemini, opencv-python-headless for screenshots (the "
    "'headless' build has no GUI parts, which suits a server), and fpdf2 for the PDF.",
])

sub_heading(pdf, "host.json - Function host settings")
block(pdf, "host.json", 1, len(load("host.json")))
explain(pdf, [
    "Configures the Azure Functions host. The extension bundle brings in the storage bindings; "
    "functionTimeout is set to 30 minutes so a long background job isn't killed early.",
    "(Note: the ~230-second limit is on the HTTP RESPONSE specifically, which is why the work "
    "was moved off the request and onto a background thread.)",
])

sub_heading(pdf, ".funcignore - what NOT to deploy")
block(pdf, ".funcignore", 1, len(load(".funcignore")))
explain(pdf, [
    "Lists files and folders to exclude from the deployment package - local secrets, the sample "
    "recording/transcript, generated PDFs, caches and virtualenvs. Keeps the upload small and "
    "keeps secrets out of the cloud.",
])

sub_heading(pdf, "local.settings.json - local-only settings")
block(pdf, "local.settings.json", 1, len(load("local.settings.json")))
explain(pdf, [
    "Used only when running the app on your own machine. It points storage at the local "
    "development emulator and declares the Python worker runtime. It is never deployed (it's in "
    ".funcignore).",
])

# ---------------------------------------------------------------------------
# 9. .env
# ---------------------------------------------------------------------------
section_title(
    pdf, 9, ".env",
    "Local-only secrets. Never committed, never deployed - on Azure these live in App Settings instead."
)
para(
    pdf,
    "The .env file holds the five secret values the tool needs. Only the NAMES are shown here - "
    "the actual values are private and must never be shared or checked into source control."
)
code_block(pdf, """GRAPHTENANTID=<your Azure AD tenant id>
GRAPHCLIENTID=<the app registration's client id>
GRAPHCLIENTSECRET=<the app registration's client secret>
GRAPHUSERID=<optional default organizer, for single-user local testing>
GEMINIAPIKEY=<your Google Gemini API key>""")
explain(pdf, [
    "The three GRAPH* values are the app's identity in Azure AD - they let it authenticate to "
    "Microsoft Graph without a human signing in.",
    "GRAPHUSERID is optional and only used as a fallback locally; in production the organizer is "
    "passed per request.",
    "GEMINIAPIKEY authenticates the calls to Google's Gemini AI.",
    "On Azure these same names are set as App Settings (and the Graph/Gemini secrets are kept in "
    "Key Vault), so the deployed app reads them from the environment exactly the same way.",
])

# ---------------------------------------------------------------------------
# 10. Full pipeline end to end
# ---------------------------------------------------------------------------
section_title(
    pdf, 10, "The full pipeline, end to end",
    "Following a single click all the way through, so the pieces connect."
)
explain(pdf, [
    "1. A user opens the web form (serve_form) and pastes a Teams join link + the organizer's "
    "email, then clicks Generate.",
    "2. The page POSTs to run_demo_capture. It validates the input, writes a 'queued' status to "
    "blob storage, starts _run_job on a background thread, and instantly returns a job_id.",
    "3. The page starts polling get_job_status every 4 seconds with that job_id.",
    "4. On the background thread, _run_job calls fetch_meeting_artifacts (graph_client) to "
    "download the recording.mp4 and transcript.vtt from Microsoft Graph.",
    "5. It calls generate_navigation_pdf, which: parses the transcript (transcript_parser), asks "
    "Gemini for the key moments (key_moments_extractor), grabs a screenshot at each one "
    "(screenshot_extractor), and asks Gemini - in one batched call - to write a title + bullets "
    "for every screenshot (write_steps_batch).",
    "6. build_pdf lays the screenshots and text into the branded PDF.",
    "7. _run_job uploads the PDF (upload_pdf) and writes a 'done' status with the blob name.",
    "8. The next poll sees 'done', get_job_status mints a short-lived download URL, and the page "
    "navigates to it - the browser downloads demo_guide.pdf. If anything failed, the page shows "
    "the exact error instead.",
])

# ---------------------------------------------------------------------------
# 11. Recent changes & quirks
# ---------------------------------------------------------------------------
section_title(
    pdf, 11, "Recent major changes & quirks worth knowing",
    "The things that trip people up, and what changed most recently."
)

sub_heading(pdf, "What changed most recently")
explain(pdf, [
    "Async job flow (function_app.py): the tool used to do everything inside one web request "
    "and failed with a generic 'Server Error' on longer demos because Azure cuts a response off "
    "at ~230 seconds. Now the work runs on a background thread and the page polls for the "
    "result, so length no longer matters.",
    "A queue-triggered worker was tried first but, on the Flex Consumption plan, the queue "
    "instance kept failing to scale up from zero - jobs just sat there. The background-thread "
    "approach runs on the already-warm web instance and needs no extra Azure setup.",
    "Batched Gemini call (pdf_report_generator.py): step descriptions used to be one Gemini "
    "call per screenshot - 14 screenshots meant 14 rate-limited calls (minutes of waiting) and "
    "blew through the free daily quota. write_steps_batch now sends all screenshots in a single "
    "call, so a demo uses about two Gemini requests total.",
    "Job status + SAS download links (blob_storage.py): new helpers store per-job status and "
    "hand the browser a temporary, no-login link straight to the finished PDF.",
])

sub_heading(pdf, "Quirks worth knowing")
explain(pdf, [
    "Object ID vs email: the recordings/transcripts Graph endpoints need the organizer's GUID, "
    "not their email - graph_client resolves the email to a GUID automatically.",
    "Caching lives in the system temp folder, not the project folder, because Azure runs the "
    "app from a read-only directory. Caches speed up re-runs of the same meeting but vanish when "
    "the instance recycles.",
    "The free Gemini tier has both a per-minute and a per-day limit. The per-day one won't reset "
    "by retrying, so the code fails fast with a clear message when it's hit.",
    "The function key injected into the form must be a HOST key, so it works for both the "
    "start and status calls the page makes.",
    "Language: demos may mix English and Hindi; every Gemini prompt insists the written output "
    "be English regardless of the spoken language.",
    "This background-thread design assumes someone keeps the page open while it runs (the "
    "polling keeps the instance warm). If a job ever stalls at 'running', setting the Function "
    "App's 'Always Ready instances' to 1 makes it bulletproof.",
])

# ---------------------------------------------------------------------------
OUTPUT = os.path.join(BASE, "full_code_explainer.pdf")
pdf.output(OUTPUT)
print(f"Wrote {OUTPUT}")
