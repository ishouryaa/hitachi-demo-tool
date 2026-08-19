import os
import re
import sys

import cv2

TIMESTAMP_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})\.(\d{3})$")


def timestamp_to_seconds(timestamp: str) -> float:
    match = TIMESTAMP_RE.match(timestamp.strip())
    if not match:
        raise ValueError(f"Invalid timestamp format: {timestamp!r} (expected HH:MM:SS.mmm)")
    hours, minutes, seconds, millis = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000.0


def _safe_filename(timestamp: str) -> str:
    return timestamp.replace(":", "-")


def capture_screenshot(video_path: str, timestamp: str, out_path: str) -> bool:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video file: {video_path}")

    try:
        seconds = timestamp_to_seconds(timestamp)
        cap.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
        success, frame = cap.read()
        if not success:
            return False
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        cv2.imwrite(out_path, frame)
        return True
    finally:
        cap.release()


def extract_screenshots(video_path: str, key_moments: list, output_dir: str = "screenshots") -> list:
    os.makedirs(output_dir, exist_ok=True)
    results = []

    for i, moment in enumerate(key_moments):
        timestamp = moment["timestamp"]
        filename = f"{i:03d}_{_safe_filename(timestamp)}.jpg"
        out_path = os.path.join(output_dir, filename)

        try:
            captured = capture_screenshot(video_path, timestamp, out_path)
        except ValueError as e:
            print(f"  Skipping moment at {timestamp!r}: {e}")
            captured = False

        moment_with_screenshot = {**moment, "screenshot_path": out_path if captured else None}
        results.append(moment_with_screenshot)

        status = "OK" if captured else "FAILED"
        print(f"  [{status}] {timestamp} -> {out_path if captured else '(no frame)'}")

    return results


if __name__ == "__main__":
    import json

    from key_moments_extractor import extract_key_moments_from_vtt

    if len(sys.argv) < 3:
        print("Usage: python screenshot_extractor.py <recording.mp4> <transcript.vtt> [output_dir]")
        sys.exit(1)

    video_path = sys.argv[1]
    vtt_path = sys.argv[2]
    output_dir = sys.argv[3] if len(sys.argv) > 3 else "screenshots"

    if not os.path.exists(video_path):
        print(f"Error: video file not found: {video_path}")
        sys.exit(1)

    print("Extracting key moments from transcript...")
    try:
        moments = extract_key_moments_from_vtt(vtt_path)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

    print(f"Found {len(moments)} key moment(s). Capturing screenshots...")
    results = extract_screenshots(video_path, moments, output_dir)

    print(json.dumps(results, indent=2))
    captured_count = sum(1 for r in results if r["screenshot_path"])
    print(f"\nCaptured {captured_count}/{len(results)} screenshot(s) to '{output_dir}'.")
