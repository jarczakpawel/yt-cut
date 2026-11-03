import json
import math
import subprocess
from pathlib import Path


def probe_video(filename, ffprobe, creationflags=0):
    path = Path(filename).expanduser().resolve()
    if not path.is_file():
        raise ValueError("Plik nie istnieje.")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,duration,avg_frame_rate",
         "-of", "json", str(path)],
        capture_output=True, text=True, errors="replace", timeout=30,
        creationflags=creationflags,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip()[-350:] or "Nie można odczytać pliku wideo.")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError("Nieprawidłowe metadane pliku wideo.") from error
    video = next((item for item in data.get("streams", []) if item.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError("Plik nie zawiera ścieżki wideo.")
    duration = data.get("format", {}).get("duration") or video.get("duration")
    try:
        duration_ms = round(float(duration) * 1000)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("Nie można ustalić długości nagrania.") from error
    if not math.isfinite(duration_ms) or duration_ms <= 0:
        raise ValueError("Nieprawidłowa długość nagrania.")
    fps = 30.0
    try:
        numerator, denominator = video.get("avg_frame_rate", "30/1").split("/", 1)
        ratio = float(numerator) / float(denominator)
        if math.isfinite(ratio) and ratio > 0:
            fps = ratio
    except (ValueError, ZeroDivisionError):
        pass
    return str(path), duration_ms, fps
