# YT Cut

A lightweight desktop application for downloading and precisely trimming YouTube videos. Available for **Windows and Linux**.

## Download

### Windows 10/11 (64-bit)

**[Download the latest Windows release](https://github.com/jarczakpawel/yt-cut/releases/latest)**

1. Download **YT-Cut-Windows-x64.zip** from the release assets.
2. Extract the ZIP archive.
3. Open **YT-Cut.exe** inside the extracted **YT-Cut** directory.

The Windows package includes the application, FFmpeg, FFprobe, yt-dlp, Node.js and libmpv. No separate Python installation is required. Keep the extracted files together.

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
- **Download only a selected time range** from a longer video, without downloading the entire video
- See the calculated interval length update immediately when editing the FROM / TO fields
- See live download progress for full videos, and FFmpeg processing progress for selected intervals
- Set exact **START** and **END** timestamps, down to milliseconds
- Choose a playback position directly on the timeline
- Pause and return to the selected playback position
- Step through frames and restart playback from START
- Preview only the selected interval, with optional looping
- Export to **MP4 by default**, or select MP3 or WAV
- Optionally fade audio in and/or out, with adjustable fade duration
- **Crop video directly in the player** by dragging four corner handles or drawing a new selection\n- **Responsive Windows UI** keeps the full crop preview in the player, moves crop controls below it and scrolls lower settings on small screens
- Resize from **four round corner handles or any of the four edges**; the pointer changes to horizontal, vertical or diagonal resize cursors
- Drag inside the selected crop to reposition it; adjust X/Y/width/height in pixels or choose an aspect ratio (16:9, 9:16, 1:1, 4:3, 3:4)
- Crop boundaries are mapped to source pixels, with even output dimensions for H.264 and unmodified audio
- Automatically name output files `cut1.mp4`, `cut2.mp3`, and so on
- Save exported files in **Downloads**
- When downloading a new video, unload the previous video and discard temporary files

## How to trim a video

1. Paste a YouTube URL and select **Download**.
2. To download only part of a long video, enable **Download only a fragment** under the URL, enter **FROM** and **TO**, then start the download. Otherwise the entire video is downloaded. The preview timeline starts at zero for the downloaded fragment.
3. Click the timeline to select a position (yellow marker). The white indicator shows the current playback position.
4. Use **Set START from point** and **Set END from point**, drag the red markers, or enter timestamps directly.
5. Optionally select **Crop video** next to the playback and frame-step controls below the player. Draw or resize the crop directly over the paused frame, then choose **Apply crop** or **Cancel**. **Reset crop** appears only while cropping or when a crop has been applied. Cropping only affects MP4.
6. Select the output format and optional audio fades.
7. Choose **Cut and save**. The exported file appears in your Downloads directory.

**Timeline controls:**
- Click: select a playback position
- Shift + click: set START
- Ctrl + click: set END
- Drag the red handles: adjust START / END
- Play / Pause: preview from the selected point and return to it on pause

For partial downloads, YT Cut reads YouTube's VOD HLS playlists, selects only the segments overlapping the requested interval, and downloads up to 10 segments concurrently. The small video and audio segment files are assembled locally. FFmpeg then re-encodes the selected portion for frame-accurate cuts. The original full-length source video is **not downloaded**. If YouTube provides no compatible unencrypted HLS streams, YT Cut falls back to yt-dlp's FFmpeg time-range downloader.

Download progress covers 0-70% (segments) and 70-99% (local encoding). Once enough progress has been observed, YT Cut displays an approximate **ETA for the current stage**, not a false fixed overall ETA. The display shows hours, minutes and seconds. Progress reaches 100% only after the output file is verified.

YouTube network throttling and video encoding performance vary by video, server and computer; the ETA is an estimate, not a guarantee.

Timestamp fields support `HH:MM:SS.mmm`. Export is performed with FFmpeg using accurate seeking and re-encoding. Actual video-frame precision depends on the source media and decoder; a millisecond timestamp does not guarantee an independently decodable frame at that exact instant.

## Platforms and dependencies

| | Windows | Linux |
| --- | --- | --- |
| UI | PySide6 (Qt) | GTK 3 |
| Video preview | libmpv in a native embedded HWND | mpv |
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
- [mpv / libmpv](https://mpv.io/installation/) - Windows binary by [shinchiro](https://github.com/shinchiro/mpv-winbuild-cmake/releases)
- [Vulkan-Loader](https://github.com/KhronosGroup/Vulkan-Loader) - bundled loader built from verified Khronos sources (Apache-2.0)
- [Qt / PySide6](https://doc.qt.io/qtforpython-6/)

On Windows, the player is embedded into a native Qt widget using libmpv's HWND embedding, initialized only after the window is shown. VP9/Opus and other supported formats play directly without making a converted proxy. The player writes `%LOCALAPPDATA%\YT-Cut\startup.log` before loading libmpv, so startup failures can be diagnosed even without an mpv.log. After initialization, mpv writes `%LOCALAPPDATA%\YT-Cut\mpv.log`. The original source is preserved for export and cropping.

The Windows package bundles a pinned x64 `vulkan-1.dll` alongside `libmpv-2.dll`. Recent mpv builds import the Vulkan loader during DLL loading, even when playback uses another graphics API. The application loads its bundled dependency first to avoid relying on a GPU driver installation.
