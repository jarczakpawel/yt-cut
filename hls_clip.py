import concurrent.futures
import json
import os
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path


class FastUnavailable(Exception):
    pass


def fetch_manifest(fmt):
    request = urllib.request.Request(fmt["url"], headers=fmt.get("http_headers") or {})
    with urllib.request.urlopen(request, timeout=15) as response:
        lines = response.read().decode("utf-8").splitlines()
    segments = []
    position = 0.0
    duration = None
    for line in lines:
        line = line.strip()
        if line.startswith(("#EXT-X-BYTERANGE", "#EXT-X-MAP", "#EXT-X-DISCONTINUITY")):
            raise FastUnavailable("Unsupported HLS playlist")
        if line.startswith("#EXT-X-KEY") and "METHOD=NONE" not in line:
            raise FastUnavailable("Encrypted HLS playlist")
        if line.startswith("#EXTINF:"):
            duration = float(line.split(":", 1)[1].split(",", 1)[0])
        elif line and not line.startswith("#") and duration is not None:
            segments.append((position, duration, urllib.parse.urljoin(fmt["url"], line)))
            position += duration
            duration = None
    if not segments or "#EXT-X-ENDLIST" not in lines:
        raise FastUnavailable("No VOD segments")
    return segments


def download(url, start_ms, end_ms, directory, yt_dlp, ffmpeg, progress, cancelled, set_process):
    work = Path(directory) / "hls"
    work.mkdir(exist_ok=True)
    result = Path(directory) / "preview.mkv"
    command = [
        yt_dlp, "--no-config", "--no-playlist", "--js-runtimes", "node",
        "--no-warnings", "-J", url,
    ]
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                            start_new_session=os.name != "nt")
    set_process(proc)
    output, errors = proc.communicate()
    if cancelled():
        raise InterruptedError("Cancelled")
    if proc.returncode:
        raise FastUnavailable(errors.decode("utf-8", errors="replace")[-450:])
    info = json.loads(output)
    formats = info.get("formats") or []
    videos = [f for f in formats
              if f.get("protocol") in ("m3u8", "m3u8_native")
              and f.get("url") and f.get("vcodec", "").startswith("avc1") and f.get("height")]
    audios = [f for f in formats
              if f.get("protocol") in ("m3u8", "m3u8_native")
              and f.get("url") and f.get("vcodec") == "none"]
    if not videos or not audios:
        raise FastUnavailable("No compatible HLS streams")
    video = max(videos, key=lambda f: (f.get("height") or 0, f.get("fps") or 0))
    audio = next((f for f in audios if f.get("format_id") == "234"), audios[-1])
    start, end = start_ms / 1000, end_ms / 1000
    tasks = []
    offsets = []
    for label, fmt in (("video", video), ("audio", audio)):
        segments = fetch_manifest(fmt)
        selected = [(idx, seg) for idx, seg in enumerate(segments)
                    if seg[0] < end and seg[0] + seg[1] > start]
        if not selected:
            raise FastUnavailable("Time range is outside the video")
        offsets.append(start - selected[0][1][0])
        for idx, (_, _, segment_url) in selected:
            tasks.append((label, idx, segment_url, fmt.get("http_headers") or {}))
    if cancelled():
        raise InterruptedError("Cancelled")
    total = len(tasks)
    progress(0, "Pobieranie segmentów")

    def fetch(item):
        label, idx, segment_url, headers = item
        if cancelled():
            raise InterruptedError("Cancelled")
        target = work / f"{label}-{idx:05}.ts"
        for attempt in range(3):
            try:
                request = urllib.request.Request(segment_url, headers=headers)
                with urllib.request.urlopen(request, timeout=25) as response, target.open("wb") as dest:
                    shutil.copyfileobj(response, dest, 256 * 1024)
                if cancelled():
                    raise InterruptedError("Cancelled")
                if target.stat().st_size == 0:
                    raise OSError("Empty segment")
                return
            except InterruptedError:
                raise
            except (OSError, ValueError):
                if attempt == 2:
                    raise
                time.sleep(0.4 * (attempt + 1))

    count = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(fetch, task) for task in tasks]
        try:
            for future in concurrent.futures.as_completed(futures):
                future.result()
                count += 1
                progress(count / total * 70, "Pobieranie segmentów")
                if cancelled():
                    raise InterruptedError("Cancelled")
        except BaseException:
            for future in futures:
                future.cancel()
            raise

    for label in ("video", "audio"):
        with (work / f"{label}.ts").open("wb") as dest:
            for task in tasks:
                if task[0] == label:
                    with (work / f"{task[0]}-{task[1]:05}.ts").open("rb") as part:
                        shutil.copyfileobj(part, dest, 1024 * 1024)
    if cancelled():
        raise InterruptedError("Cancelled")
    duration = (end_ms - start_ms) / 1000
    command = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
               "-progress", "pipe:1",
               "-ss", f"{offsets[0]:.3f}", "-i", str(work / "video.ts"),
               "-ss", f"{offsets[1]:.3f}", "-i", str(work / "audio.ts"),
               "-t", f"{duration:.3f}", "-map", "0:v:0", "-map", "1:a:0",
               "-c:v", "libx264", "-preset", "ultrafast", "-crf", "19",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
               "-f", "matroska", str(result)]
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace", bufsize=1,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                            start_new_session=os.name != "nt")
    set_process(proc)
    errors = []
    progress(70, "Kodowanie lokalne")
    for line in proc.stdout:
        line = line.strip()
        if line.startswith("out_time_us="):
            try:
                elapsed = int(line.split("=", 1)[1])
                progress(min(99, 70 + elapsed / ((end_ms - start_ms) * 1000) * 29), "Kodowanie lokalne")
            except ValueError:
                pass
        elif line and not line.startswith(("progress=", "frame=", "fps=", "stream_", "bitrate=",
                                            "total_size=", "out_time=", "out_time_ms=", "dup_frames=",
                                            "drop_frames=", "speed=", "quality=")):
            errors.append(line)
            errors = errors[-8:]
    if proc.wait():
        raise RuntimeError("FFmpeg: " + "\n".join(errors))
    if cancelled():
        raise InterruptedError("Cancelled")
    shutil.rmtree(work, ignore_errors=True)
    return str(result)

