# YT Cut

A lightweight desktop application for downloading and precisely trimming YouTube videos. Available for **Windows and Linux**.

## Download

### Windows 10/11 (64-bit)

**[Download the latest Windows release](https://github.com/jarczakpawel/yt-cut/releases/latest)**

1. Download **YT-Cut-Windows-x64.zip** from the release assets.
2. Extract the ZIP archive.
3. Open **YT-Cut.exe** inside the extracted **YT-Cut** directory.

The Windows package includes the application, FFmpeg, FFprobe, yt-dlp and Node.js. No separate Python installation is required. Keep the extracted files together.

Windows may display a Microsoft SmartScreen warning because the executable is not digitally signed.

### Linux (Debian)

Install Python 3, GTK 3 with Python GObject Introspection bindings, mpv, FFmpeg, yt-dlp, and Node.js 22 or later. For example:

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 mpv ffmpeg nodejs
```

Install an up-to-date yt-dlp from its [official installation instructions](https://github.com/yt-dlp/yt-dlp/wiki/Installation). Then run:

```bash
python3 yt-cut.py
```

The Linux version embeds mpv in a GTK window, so it needs a running X11/XWayland display.

## Features

- Download a YouTube video from a URL and preview it in the built-in player
- Set exact **START** and **END** timestamps, down to milliseconds
- Choose a playback position directly on the timeline
- Pause and return to the selected playback position
- Step through frames and restart playback from START
- Preview only the selected interval, with optional looping
- Export to **MP4 by default**, or select MP3 or WAV
- Optionally fade audio in and/or out, with adjustable fade duration
- Automatically name output files `cut1.mp4`, `cut2.mp3`, and so on
- Save exported files in **Downloads**
- When downloading a new video, unload the previous video and discard temporary files

## How to trim a video

1. Paste a YouTube URL and select **Download**.
2. Click the timeline to select a position (yellow marker). The white indicator shows the current playback position.
3. Use **Set START from point** and **Set END from point**, drag the red markers, or enter timestamps directly.
4. Select the output format and optional audio fades.
5. Choose **Cut and save**. The exported file appears in your Downloads directory.

**Timeline controls:**
- Click: select a playback position
- Shift + click: set START
- Ctrl + click: set END
- Drag the red handles: adjust START / END
- Play / Pause: preview from the selected point and return to it on pause

Timestamp fields support `HH:MM:SS.mmm`. Export is performed with FFmpeg using accurate seeking and re-encoding. Actual video-frame precision depends on the source media and decoder; a millisecond timestamp does not guarantee an independently decodable frame at that exact instant.

## Platforms and dependencies

| | Windows | Linux |
| --- | --- | --- |
| UI | PySide6 (Qt) | GTK 3 |
| Video preview | Qt Multimedia | mpv |
| Download | yt-dlp | yt-dlp |
| Export | FFmpeg | FFmpeg |
| JavaScript runtime | Bundled Node.js | Node.js |

Release archives are built automatically from the source code in this repository when a new version is published. The executable is unsigned.

YouTube downloading requires an internet connection and depends on yt-dlp compatibility with YouTube. Download only content you are authorized to use.

## Third-party software

Windows distributions include third-party binaries, subject to their respective licenses:

- [FFmpeg and its licensing](https://ffmpeg.org/legal.html) - Windows builds from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [Node.js](https://github.com/nodejs/node)
- [Qt / PySide6](https://doc.qt.io/qtforpython-6/)
