import re

CUE_TIME_RE = re.compile(
    r"(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})"
)
VOICE_TAG_RE = re.compile(r"^<v\s+([^>]+)>(.*)</v>$", re.DOTALL)


def parse_vtt(path: str) -> list:
    """Parse a WebVTT file into a list of segment dicts."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    blocks = re.split(r"\n\s*\n", content.strip())

    segments = []
    for block in blocks:
        lines = [line for line in block.splitlines() if line.strip()]
        if not lines or lines[0].strip() == "WEBVTT":
            continue

        time_line = None
        text_lines = []
        for line in lines:
            if CUE_TIME_RE.search(line):
                time_line = line
            elif time_line is not None:
                text_lines.append(line)

        if time_line is None or not text_lines:
            continue

        match = CUE_TIME_RE.search(time_line)
        start, end = match.group(1), match.group(2)

        raw_text = " ".join(text_lines).strip()
        speaker = None
        voice_match = VOICE_TAG_RE.match(raw_text)
        if voice_match:
            speaker = voice_match.group(1).strip()
            text = voice_match.group(2).strip()
        else:
            text = raw_text

        segments.append({
            "start": start,
            "end": end,
            "speaker": speaker,
            "text": text,
        })

    return segments


if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python transcript_parser.py <transcript.vtt>")
        sys.exit(1)

    segments = parse_vtt(sys.argv[1])
    print(json.dumps(segments, indent=2))
    print(f"\nParsed {len(segments)} segments.")
