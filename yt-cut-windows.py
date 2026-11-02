import os
import re
import sys
import math
import shutil
import subprocess
import tempfile
import time
import threading
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import Qt, QTimer, QUrl, Signal, QObject, QRectF, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QVideoSink
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QComboBox, QCheckBox, QDoubleSpinBox, QProgressBar,
    QFrame, QSizePolicy,
)

BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
TOOLS = BASE / "tools"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
if TOOLS.is_dir():
    os.environ["PATH"] = str(TOOLS) + os.pathsep + os.environ.get("PATH", "")
FFMPEG = str(TOOLS / "ffmpeg.exe") if (TOOLS / "ffmpeg.exe").is_file() else "ffmpeg"
FFPROBE = str(TOOLS / "ffprobe.exe") if (TOOLS / "ffprobe.exe").is_file() else "ffprobe"
YTDLP = str(TOOLS / "yt-dlp.exe") if (TOOLS / "yt-dlp.exe").is_file() else "yt-dlp"
DOWNLOADS = Path.home() / "Downloads"


def clock(ms):
    h, remainder = divmod(max(0, int(ms)), 3600000)
    m, remainder = divmod(remainder, 60000)
    s, ms = divmod(remainder, 1000)
    return f"{h:02}:{m:02}:{s:02}.{ms:03}"


def parse_clock(text):
    parts = text.strip().replace(",", ".").split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError("Wpisz GG:MM:SS.mmm")
    total = 0.0
    for i, text in enumerate(reversed(parts)):
        value = float(text)
        if not math.isfinite(value) or value < 0 or (i < 2 and len(parts) > i + 1 and value >= 60):
            raise ValueError("Nieprawidłowy czas")
        total += value * 60**i
    return round(total * 1000)


class Events(QObject):
    progress = Signal(int, float, str)
    downloaded = Signal(int, str, int, float, str)
    failed = Signal(int, str)
    exported = Signal(int, str)


def human_time(ms):
    h, remainder = divmod(max(0, int(ms)), 3600000)
    m, remainder = divmod(remainder, 60000)
    sec, millis = divmod(remainder, 1000)
    return f"{h} h {m} min {sec}{f'.{millis:03d}' if millis else ''} s"


class Timeline(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.drag = None
        self.setMinimumHeight(87)
        self.setMouseTracking(True)
        self.setToolTip("Kliknij, aby ustawić punkt. Przeciągaj czerwone znaczniki. Shift+klik: START, Ctrl+klik: KONIEC.")

    def x_at(self, time):
        return 22 + (self.width() - 44) * time / max(1, self.window.duration)

    def time_at(self, x):
        return round(max(0, min(1, (x - 22) / max(1, self.width() - 44))) * self.window.duration)

    def paintEvent(self, event):
        a = self.window
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        left, width = 22, self.width() - 44
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#303641"))
        p.drawRoundedRect(QRectF(left, 29, width, 14), 4, 4)
        if a.duration < 1:
            p.setPen(QColor("#aab4c4"))
            p.drawText(left, 74, "Wklej URL i kliknij Pobierz")
            p.end()
            return
        sx, ex = self.x_at(a.start), self.x_at(a.end)
        p.setBrush(QColor("#ef5550"))
        p.drawRect(QRectF(sx, 29, max(2, ex - sx), 14))
        duration_s = a.duration / 1000
        step = next((t for t in (.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600) if width * t / duration_s >= 100), 7200)
        t = 0.0
        p.setPen(QPen(QColor("#929baa"), 1))
        while t <= duration_s + .0001:
            x = self.x_at(t * 1000)
            p.drawLine(round(x), 49, round(x), 54)
            p.drawText(round(min(x, left + width - 68)), 72, clock(round(t * 1000))[:-4] if step >= 1 else clock(round(t * 1000)))
            t += step
        p.setPen(QPen(QColor("#ff8e85"), 2))
        for x in (sx, ex):
            p.drawLine(round(x), 12, round(x), 48)
            p.setBrush(QColor("#ff8e85"))
            p.drawPolygon(QPolygonF([QPointF(x - 6, 10), QPointF(x + 6, 10), QPointF(x, 19)]))
        x = self.x_at(a.anchor)
        p.setPen(QPen(QColor("#ffd065"), 2))
        p.drawLine(round(x), 3, round(x), 49)
        p.drawText(round(min(x + 5, left + width - 40)), 10, "PUNKT")
        x = self.x_at(a.position)
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.drawLine(round(x), 18, round(x), 45)
        p.setBrush(QColor("#ffffff"))
        p.drawPolygon(QPolygonF([QPointF(x - 5, 8), QPointF(x + 5, 8), QPointF(x, 16)]))
        p.end()

    def mousePressEvent(self, event):
        a = self.window
        if not a.media or event.button() != Qt.LeftButton:
            return
        x = event.position().x()
        if event.modifiers() & Qt.ShiftModifier:
            self.drag = "start"
        elif event.modifiers() & Qt.ControlModifier:
            self.drag = "end"
        elif min(abs(x - self.x_at(a.start)), abs(x - self.x_at(a.end))) <= 14:
            self.drag = "start" if abs(x - self.x_at(a.start)) <= abs(x - self.x_at(a.end)) else "end"
        else:
            self.drag = "cursor"
        self.set_value(x)

    def mouseMoveEvent(self, event):
        if self.drag:
            self.set_value(event.position().x())

    def mouseReleaseEvent(self, event):
        if self.drag:
            self.set_value(event.position().x())
            self.drag = None

    def set_value(self, x):
        a = self.window
        ms = self.time_at(x)
        if self.drag == "start":
            a.start = min(ms, a.end - 1)
            a.update_fields()
            a.seek(a.start)
        elif self.drag == "end":
            a.end = max(ms, a.start + 1)
            a.update_fields()
            a.seek(a.end)
        else:
            a.anchor = ms
            a.seek(ms)
        self.update()


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("YT Cut")
        self.resize(1060, 790)
        self.setMinimumSize(765, 630)
        self.duration = 0
        self.start = 0
        self.end = 0
        self.anchor = 0
        self.position = 0
        self.fps = 30
        self.media = None
        self.directory = None
        self.old_dirs = []
        self.download_job = 0
        self.export_job = 0
        self.download_proc = None
        self.export_proc = None
        self.restoring = False
        self.last_error = ""
        self.is_downloading = False
        self.download_has_percent = False
        self.download_started_at = 0.0
        self.progress_first_at = 0.0
        self.progress_last_at = 0.0
        self.progress_first_value = 0.0
        self.progress_value = 0.0
        self.progress_display_at = -1
        self.signals = Events()
        self.signals.progress.connect(self.on_progress)
        self.signals.downloaded.connect(self.on_downloaded)
        self.signals.failed.connect(self.on_failed)
        self.signals.exported.connect(self.on_exported)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.audio.setVolume(1)
        self.player.positionChanged.connect(self.position_changed)
        self.player.errorOccurred.connect(self.media_error)
        self.player.mediaStatusChanged.connect(self.media_status_changed)

        center = QWidget()
        self.setCentralWidget(center)
        main = QVBoxLayout(center)
        main.setContentsMargins(20, 20, 20, 20)
        main.setSpacing(12)

        urlrow = QHBoxLayout()
        self.url = QLineEdit()
        self.url.setPlaceholderText("Wklej URL YouTube...")
        self.url.returnPressed.connect(self.download)
        urlrow.addWidget(self.url, 1)
        self.download_button = QPushButton("Pobierz")
        self.download_button.setObjectName("primary")
        self.download_button.clicked.connect(self.download)
        urlrow.addWidget(self.download_button)
        self.cancel_button = QPushButton("Anuluj")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_download)
        urlrow.addWidget(self.cancel_button)
        main.addLayout(urlrow)

        section_row = QHBoxLayout()
        self.only_fragment = QCheckBox("Pobierz tylko fragment")
        section_row.addWidget(self.only_fragment)
        section_row.addWidget(QLabel("OD:"))
        self.fragment_from = QLineEdit("00:00:00.000")
        self.fragment_from.setFixedWidth(150)
        section_row.addWidget(self.fragment_from)
        section_row.addWidget(QLabel("DO:"))
        self.fragment_to = QLineEdit("00:01:00.000")
        self.fragment_to.setFixedWidth(150)
        section_row.addWidget(self.fragment_to)
        section_row.addStretch()
        self.fragment_from.setEnabled(False)
        self.fragment_to.setEnabled(False)
        self.only_fragment.toggled.connect(self.fragment_from.setEnabled)
        self.only_fragment.toggled.connect(self.fragment_to.setEnabled)
        self.fragment_duration_label = QLabel("Długość: 00:01:00.000")
        self.fragment_duration_label.setObjectName("clock")
        section_row.addWidget(self.fragment_duration_label)
        self.only_fragment.toggled.connect(self.update_fragment_duration)
        self.fragment_from.textChanged.connect(self.update_fragment_duration)
        self.fragment_to.textChanged.connect(self.update_fragment_duration)
        main.addLayout(section_row)

        self.progress = QProgressBar()
        self.progress.setMaximum(1000)
        self.progress.setValue(0)
        self.progress.setFormat("Ready")
        self.progress.setTextVisible(True)
        main.addWidget(self.progress)

        self.title_text = QLabel("Brak wczytanego filmu")
        self.title_text.setObjectName("subtle")
        main.addWidget(self.title_text)

        self.video = QVideoWidget()
        self.video.setMinimumHeight(280)
        self.video.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.player.setVideoOutput(self.video)
        main.addWidget(self.video, 1)

        controls = QHBoxLayout()
        self.play_button = QPushButton("▶  Odtwórz")
        self.play_button.setObjectName("primary")
        self.play_button.clicked.connect(self.play_pause)
        controls.addWidget(self.play_button)
        self.back_button = QPushButton("◀ Klatka")
        self.back_button.clicked.connect(lambda: self.frame_step(-1))
        controls.addWidget(self.back_button)
        self.next_button = QPushButton("Klatka ▶")
        self.next_button.clicked.connect(lambda: self.frame_step(1))
        controls.addWidget(self.next_button)
        self.restart_button = QPushButton("↺ Od START")
        self.restart_button.clicked.connect(self.restart)
        controls.addWidget(self.restart_button)
        controls.addStretch()
        self.position_text = QLabel(f"{clock(0)} / {clock(0)}")
        self.position_text.setObjectName("clock")
        controls.addWidget(self.position_text)
        main.addLayout(controls)

        self.timeline = Timeline(self)
        main.addWidget(self.timeline)

        times = QHBoxLayout()
        self.start_edit = QLineEdit("00:00:00.000")
        self.end_edit = QLineEdit("00:00:00.000")
        for name, field, kind in (("START", self.start_edit, "start"), ("KONIEC", self.end_edit, "end")):
            col = QVBoxLayout()
            col.addWidget(QLabel(name))
            col.addWidget(field)
            field.setFixedWidth(160)
            field.editingFinished.connect(lambda k=kind: self.edit_time(k))
            times.addLayout(col)
        marks = QVBoxLayout()
        self.mark_start_button = QPushButton("Ustaw START z punktu")
        self.mark_end_button = QPushButton("Ustaw KONIEC z punktu")
        self.mark_start_button.clicked.connect(lambda: self.mark("start"))
        self.mark_end_button.clicked.connect(lambda: self.mark("end"))
        marks.addWidget(self.mark_start_button)
        marks.addWidget(self.mark_end_button)
        times.addLayout(marks)
        times.addStretch()
        self.selection = QCheckBox("Odtwarzaj zaznaczenie")
        self.selection.setChecked(True)
        times.addWidget(self.selection)
        self.loop = QCheckBox("Pętla")
        times.addWidget(self.loop)
        main.addLayout(times)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        main.addWidget(sep)

        settings = QHBoxLayout()
        settings.addWidget(QLabel("Zapisz jako:"))
        self.format = QComboBox()
        self.format.addItem("Wideo MP4 (domyślnie)", "mp4")
        self.format.addItem("Audio MP3", "mp3")
        self.format.addItem("Audio WAV", "wav")
        settings.addWidget(self.format)
        self.fade_in = QCheckBox("Płynne podgłaśnianie")
        self.fade_out = QCheckBox("Płynne ściszanie")
        settings.addWidget(self.fade_in)
        settings.addWidget(self.fade_out)
        settings.addWidget(QLabel("Czas:"))
        self.fade_seconds = QDoubleSpinBox()
        self.fade_seconds.setRange(.1, 10)
        self.fade_seconds.setSingleStep(.1)
        self.fade_seconds.setValue(1)
        self.fade_seconds.setSuffix(" s")
        settings.addWidget(self.fade_seconds)
        settings.addStretch()
        main.addLayout(settings)

        foot = QHBoxLayout()
        self.status = QLabel("Gotowy. Wklej link do YouTube.")
        self.status.setWordWrap(True)
        foot.addWidget(self.status, 1)
        self.export_button = QPushButton("Wytnij i zapisz do Pobrane")
        self.export_button.setObjectName("primary")
        self.export_button.clicked.connect(self.export)
        foot.addWidget(self.export_button)
        main.addLayout(foot)
        self.update_enabled()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(30)
        self.setStyleSheet("""
QMainWindow { background:#161920; }
QWidget { color:#ebedf2; font-size:13px; }
QLabel#subtle { color:#a4adbc; }
QLabel#clock { font-family:Consolas, monospace; font-size:14px; font-weight:600; }
QLineEdit,QComboBox,QDoubleSpinBox { background:#242a34; border:1px solid #424a58; padding:8px; border-radius:7px; min-height:21px; }
QLineEdit:focus { border-color:#ed5a54; }
QPushButton { background:#2b313c; border:1px solid #414956; border-radius:7px; padding:9px 13px; }
QPushButton:hover { background:#3a414e; }
QPushButton:disabled { color:#66707e; }
QPushButton#primary { background:#ed5350; border:1px solid #f5766d; color:white; font-weight:600; }
QPushButton#primary:hover { background:#fa6a61; }
QVideoWidget { background:#101216; }
QProgressBar { background:#303640; border:0; border-radius:5px; min-height:18px; text-align:center; font-weight:600; }
QProgressBar::chunk { background:#eb5650; border-radius:3px; }
QCheckBox { spacing:5px; }
""")

    def update_fragment_duration(self, *_):
        if not self.only_fragment.isChecked():
            self.fragment_duration_label.setText("Cały film")
            return
        try:
            begin = parse_clock(self.fragment_from.text())
            end = parse_clock(self.fragment_to.text())
            if end <= begin:
                raise ValueError("Invalid time range")
            self.fragment_duration_label.setText(f"Długość: {human_time(end - begin)}")
        except ValueError:
            self.fragment_duration_label.setText("Nieprawidłowy zakres")

    def update_enabled(self):
        ready = self.media is not None
        for widget in (self.play_button, self.back_button, self.next_button, self.restart_button,
                       self.mark_start_button, self.mark_end_button, self.start_edit, self.end_edit):
            widget.setEnabled(ready)
        self.export_button.setEnabled(ready and (self.export_proc is None or self.export_proc.poll() is not None))

    def update_fields(self):
        self.start_edit.setText(clock(self.start))
        self.end_edit.setText(clock(self.end))
        self.timeline.update()

    def seek(self, value):
        self.position = max(0, min(self.duration, int(value)))
        self.player.setPosition(self.position)
        self.position_text.setText(f"{clock(self.position)} / {clock(self.duration)}")
        self.timeline.update()

    def position_changed(self, position):
        if not self.restoring:
            self.position = max(0, min(self.duration, int(position)))
            self.position_text.setText(f"{clock(self.position)} / {clock(self.duration)}")
            self.timeline.update()

    def media_status_changed(self, status):
        if status == QMediaPlayer.LoadedMedia and self.media:
            self.player.pause()
            self.seek(self.anchor)

    def media_error(self, error, message):
        if error != QMediaPlayer.NoError and self.media:
            self.status.setText(f"Błąd odtwarzacza: {message}")

    def tick(self):
        if self.is_downloading and self.download_has_percent and int(time.monotonic()) != self.progress_display_at:
            now = time.monotonic()
            elapsed = now - self.progress_first_at
            delta = self.progress_value - self.progress_first_value
            eta = elapsed * (1 - self.progress_value) / delta if delta > .02 and elapsed >= 3 and now - self.progress_last_at < 10 else None
            remaining = f"~{human_time(round(eta) * 1000)}" if eta is not None else "obliczanie..."
            if self.progress_value >= .99:
                remaining = "finalizacja..."
            display = f"{self.progress_value * 100:.1f}%   |   czas: {human_time(int(elapsed) * 1000)}   |   pozostało: {remaining}"
            self.progress.setFormat(display)
            self.status.setText(display)
            self.progress_display_at = int(now)
        if self.is_downloading and not self.download_has_percent:
            elapsed = round(time.monotonic() - self.download_started_at)
            self.progress.setFormat(f"Downloading fragment... {elapsed} s" if self.only_fragment.isChecked()
                                    else f"Connecting... {elapsed} s")
        if not self.media or self.restoring:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            end = self.end if self.selection.isChecked() else self.duration
            if self.position >= end:
                if self.loop.isChecked():
                    self.seek(self.start if self.selection.isChecked() else 0)
                    self.player.play()
                else:
                    self.pause_restore()

    def pause_restore(self):
        self.restoring = True
        self.player.pause()
        self.seek(self.anchor)
        self.play_button.setText("▶  Odtwórz")
        QTimer.singleShot(120, self.unlock_restoring)

    def unlock_restoring(self):
        self.restoring = False
        self.seek(self.anchor)

    def play_pause(self):
        if not self.media:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.pause_restore()
        else:
            start = self.anchor
            if self.selection.isChecked() and not self.start <= start < self.end:
                start = self.start
            self.seek(start)
            self.player.play()
            self.play_button.setText("⏸  Pauza")

    def frame_step(self, direction):
        if not self.media:
            return
        self.player.pause()
        self.play_button.setText("▶  Odtwórz")
        next_pos = max(0, min(self.duration, self.position + direction * 1000 / self.fps))
        self.anchor = round(next_pos)
        self.seek(self.anchor)

    def restart(self):
        if not self.media:
            return
        self.anchor = self.start if self.selection.isChecked() else 0
        self.seek(self.anchor)
        self.player.play()
        self.play_button.setText("⏸  Pauza")

    def edit_time(self, kind):
        if not self.media:
            return
        field = self.start_edit if kind == "start" else self.end_edit
        try:
            value = parse_clock(field.text())
            if kind == "start":
                if not 0 <= value < self.end:
                    raise ValueError("START musi być przed KONIEC")
                self.start = value
            else:
                if not self.start < value <= self.duration:
                    raise ValueError("KONIEC musi być po START i przed końcem filmu")
                self.end = value
        except ValueError as error:
            self.status.setText(str(error))
        self.update_fields()

    def mark(self, kind):
        if not self.media:
            return
        value = self.anchor
        if kind == "start":
            if value >= self.end:
                self.status.setText("START musi być przed KONIEC")
                return
            self.start = value
        else:
            if value <= self.start:
                self.status.setText("KONIEC musi być po START")
                return
            self.end = value
        self.update_fields()

    def clear_media(self):
        self.player.stop()
        self.player.setSource(QUrl())
        self.media = None
        self.position = self.anchor = self.start = self.end = self.duration = 0
        self.title_text.setText("Brak wczytanego filmu")
        self.position_text.setText(f"{clock(0)} / {clock(0)}")
        self.update_fields()
        self.play_button.setText("▶  Odtwórz")
        self.update_enabled()

    def stop_process(self, process):
        if process and process.poll() is None:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                               capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=4)
            except (OSError, subprocess.TimeoutExpired):
                process.terminate()

    def cleanup(self):
        remaining = []
        for path in self.old_dirs:
            try:
                shutil.rmtree(path)
            except OSError:
                remaining.append(path)
        self.old_dirs = remaining

    def download(self):
        url = self.url.text().strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            self.status.setText("Wklej poprawny URL filmu.")
            return
        section = None
        if self.only_fragment.isChecked():
            try:
                start_ms = parse_clock(self.fragment_from.text())
                end_ms = parse_clock(self.fragment_to.text())
                if end_ms <= start_ms:
                    raise ValueError("DO musi być późniejsze niż OD")
                section = f"*{clock(start_ms)}-{clock(end_ms)}"
            except ValueError as exc:
                self.status.setText(f"Nieprawidłowy zakres pobierania: {exc}")
                return
        self.download_job += 1
        self.export_job += 1
        self.stop_process(self.download_proc)
        self.stop_process(self.export_proc)
        self.clear_media()
        if self.directory:
            self.old_dirs.append(self.directory)
        self.directory = tempfile.mkdtemp(prefix="yt-cut-")
        QTimer.singleShot(800, self.cleanup)
        self.download_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, 0)
        self.progress.setFormat("Pobieranie fragmentu..." if section else "Pobieranie filmu...")
        self.status.setText("Pobieranie fragmentu..." if section else "Pobieranie filmu...")
        self.is_downloading = True
        self.download_has_percent = False
        self.download_started_at = time.monotonic()
        self.progress_first_at = 0.0
        self.progress_display_at = -1
        job, directory = self.download_job, self.directory
        threading.Thread(target=self.download_worker, args=(job, url, directory, section), daemon=True).start()

    def download_worker(self, job, url, directory, section):
        args = [YTDLP, "--no-playlist", "--no-mtime", "--newline", "--no-colors",
                "--progress", "--js-runtimes", "node", "--no-warnings", "-f", "bv*+ba/b",
                "--merge-output-format", "mkv", "-o", str(Path(directory) / "source.%(ext)s"),
                "--print", "after_move:YT_CUT_FILE:%(filepath)s"]
        if section:
            args += ["--download-sections", section,
                     "-S", "proto:m3u8,vcodec:h264",
                     "--downloader-args", "ffmpeg_o:-progress pipe:2 -nostats"]
        args.append(url)
        errors, path = [], None
        section_us = None
        if section:
            begin, end = section[1:].split("-", 1)
            section_us = (parse_clock(end) - parse_clock(begin)) * 1000
        try:
            if job != self.download_job:
                return
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, errors="replace", creationflags=CREATE_NO_WINDOW)
            if job != self.download_job:
                self.stop_process(proc)
            else:
                self.download_proc = proc
            for raw in proc.stdout:
                line = raw.strip()
                if line.startswith("YT_CUT_FILE:"):
                    path = line.removeprefix("YT_CUT_FILE:").strip()
                if line:
                    errors.append(line)
                    errors = errors[-12:]
                if section and line.startswith("out_time_us="):
                    try:
                        micros = int(line.split("=", 1)[1])
                        self.signals.progress.emit(job, max(0, min(.70, micros / section_us * .70)), "Pobieranie")
                    except ValueError:
                        pass
                elif not section:
                    found = re.search(r"\[download\]\s+(\d+(?:\.\d+)?)%", line)
                    if found:
                        self.signals.progress.emit(job, float(found.group(1)) / 100, "Pobieranie")
            rc = proc.wait()
            if job != self.download_job:
                return
            if rc:
                raise RuntimeError("\n".join(errors[-5:]))
            if not path or not Path(path).is_file():
                files = [p for p in Path(directory).glob("source.*") if p.is_file() and p.suffix not in (".part", ".ytdl")]
                if not files:
                    raise RuntimeError("Brak pobranego pliku")
                path = str(max(files, key=lambda x: x.stat().st_size))
            if section:
                preview = str(Path(directory) / "preview.mkv")
                encode = [FFMPEG, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
                          "-progress", "pipe:1", "-i", path, "-t", f"{section_us / 1_000_000:.3f}",
                          "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264",
                          "-preset", "ultrafast", "-crf", "19", "-pix_fmt", "yuv420p",
                          "-c:a", "aac", "-b:a", "192k", "-f", "matroska", preview]
                self.signals.progress.emit(job, .70, "Pobieranie")
                proc = subprocess.Popen(encode, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, errors="replace", creationflags=CREATE_NO_WINDOW)
                if job != self.download_job:
                    self.stop_process(proc)
                else:
                    self.download_proc = proc
                for raw in proc.stdout:
                    line = raw.strip()
                    if line.startswith("out_time_us="):
                        try:
                            micros = int(line.split("=", 1)[1])
                            self.signals.progress.emit(job, min(.99, .70 + micros / section_us * .29), "Pobieranie")
                        except ValueError:
                            pass
                    elif line and not line.startswith(("progress=", "frame=", "fps=", "stream_", "bitrate=", "total_size=", "out_time=", "out_time_ms=", "dup_frames=", "drop_frames=", "speed=", "quality=")):
                        errors.append(line)
                if proc.wait() != 0:
                    raise RuntimeError("\n".join(errors[-6:]))
                if job != self.download_job:
                    return
                Path(path).unlink(missing_ok=True)
                path = preview
            probe = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0",
                                   "-show_entries", "stream=avg_frame_rate", "-show_entries", "format=duration",
                                   "-of", "json", path], capture_output=True, text=True,
                                   creationflags=CREATE_NO_WINDOW, timeout=30)
            if probe.returncode:
                raise RuntimeError(probe.stderr.strip())
            info = __import__("json").loads(probe.stdout)
            duration = round(float(info["format"]["duration"]) * 1000)
            if duration <= 1:
                raise RuntimeError("Nieprawidłowy czas trwania")
            fps = 30.0
            streams = info.get("streams", [])
            if streams:
                numerator, denominator = streams[0].get("avg_frame_rate", "30/1").split("/")
                if int(denominator):
                    fps = float(numerator) / float(denominator)
            self.signals.downloaded.emit(job, path, duration, fps if fps > 0 else 30, section or "")
        except Exception as error:
            if job == self.download_job:
                self.signals.failed.emit(job, str(error))
        finally:
            if job == self.download_job:
                self.download_proc = None
            else:
                try:
                    shutil.rmtree(directory)
                except OSError:
                    pass

    def on_progress(self, job, fraction, name):
        if job != (self.download_job if name == "Pobieranie" else self.export_job):
            return
        if name == "Pobieranie":
            now = time.monotonic()
            if not self.download_has_percent or fraction + .1 < self.progress_value:
                self.progress_first_at = now
                self.progress_first_value = fraction
            self.download_has_percent = True
            self.progress_last_at = now
            self.progress_value = fraction
            self.progress_display_at = -1
        self.progress.setRange(0, 1000)
        self.progress.setValue(round(max(0, min(1, fraction)) * 1000))
        self.progress.setFormat(f"{name}: {fraction * 100:.1f}%")
        self.status.setText(f"{name}: {fraction * 100:.1f}%")

    def on_downloaded(self, job, path, duration, fps, section):
        if job != self.download_job:
            return
        self.is_downloading = False
        self.media, self.duration, self.end, self.fps = path, duration, duration, fps
        self.position = self.anchor = self.start = 0
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.pause()
        self.update_fields()
        self.update_enabled()
        self.title_text.setText(f"Wczytano {'fragment '+section[1:]+' • ' if section else 'film • '}{clock(duration)}")
        self.status.setText("Gotowe. Wybierz fragment i kliknij Wytnij.")
        self.progress.setRange(0, 1000)
        self.progress.setValue(1000)
        self.progress.setFormat("Download complete 100%")
        self.download_button.setEnabled(True)
        self.cancel_button.setEnabled(False)

    def on_failed(self, job, message):
        if job == self.download_job:
            self.is_downloading = False
            self.download_button.setEnabled(True)
            self.cancel_button.setEnabled(False)
            self.status.setText("Błąd pobierania: " + message[-420:])
        elif job == self.export_job:
            self.update_enabled()
            self.status.setText("Błąd eksportu: " + message[-420:])
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setFormat("Error")

    def cancel_download(self):
        self.is_downloading = False
        self.download_job += 1
        self.stop_process(self.download_proc)
        self.clear_media()
        if self.directory:
            self.old_dirs.append(self.directory)
            self.directory = None
        QTimer.singleShot(1000, self.cleanup)
        self.download_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.status.setText("Pobieranie anulowane.")
        self.progress.setValue(0)

    def export(self):
        if not self.media or self.export_proc and self.export_proc.poll() is None:
            return
        self.edit_time("start")
        self.edit_time("end")
        if not 0 <= self.start < self.end <= self.duration:
            self.status.setText("Nieprawidłowe zaznaczenie.")
            return
        mode = self.format.currentData()
        DOWNLOADS.mkdir(parents=True, exist_ok=True)
        numbers = [int(match.group(1)) for p in DOWNLOADS.iterdir()
                   if (match := re.fullmatch(r"cut(\d+)\.(mp4|mp3|wav)", p.name, re.I))]
        number = max(numbers, default=0) + 1
        target = DOWNLOADS / f"cut{number}.{mode}"
        temp = DOWNLOADS / f".cut{number}.{mode}.part"
        start = self.start / 1000
        length = (self.end - self.start) / 1000
        effects, fade = [], self.fade_seconds.value()
        if self.fade_in.isChecked():
            effects.append(f"afade=t=in:st=0:d={min(fade, length / (2 if self.fade_out.isChecked() else 1)):.3f}")
        if self.fade_out.isChecked():
            duration = min(fade, length / (2 if self.fade_in.isChecked() else 1))
            effects.append(f"afade=t=out:st={max(0, length - duration):.3f}:d={duration:.3f}")
        args = [FFMPEG, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
                "-progress", "pipe:1", "-ss", f"{start:.3f}", "-i", self.media,
                "-t", f"{length:.3f}", "-sn", "-dn"]
        if mode == "mp4":
            args += ["-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264",
                     "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
                     "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
        else:
            args += ["-map", "0:a:0", "-vn"]
            args += ["-c:a", "libmp3lame", "-q:a", "2"] if mode == "mp3" else ["-c:a", "pcm_s16le"]
        if effects:
            args += ["-af", ",".join(effects)]
        args += ["-f", mode, str(temp)]
        self.export_job += 1
        job = self.export_job
        self.export_button.setEnabled(False)
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setFormat("Exporting 0%")
        self.status.setText("Eksport...")
        threading.Thread(target=self.export_worker, args=(job, args, target, temp, length), daemon=True).start()

    def export_worker(self, job, args, target, temp, length):
        errors = []
        try:
            if job != self.export_job:
                return
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, errors="replace", creationflags=CREATE_NO_WINDOW)
            if job != self.export_job:
                self.stop_process(proc)
            else:
                self.export_proc = proc
            for raw in proc.stdout:
                line = raw.strip()
                if line.startswith("out_time_us="):
                    try:
                        fraction = max(0, min(1, int(line.split("=", 1)[1]) / (length * 1_000_000)))
                        self.signals.progress.emit(job, fraction, "Eksport")
                    except ValueError:
                        pass
                elif line and not line.startswith(("progress=", "frame=", "fps=", "stream_", "bitrate=", "total_size=", "out_time=", "out_time_ms=", "dup_frames=", "drop_frames=", "speed=", "quality=")):
                    errors.append(line)
                    errors = errors[-10:]
            rc = proc.wait()
            if job != self.export_job:
                return
            if rc:
                raise RuntimeError("\n".join(errors[-5:]) or f"FFmpeg: {rc}")
            temp.replace(target)
            self.signals.exported.emit(job, str(target))
        except Exception as error:
            if job == self.export_job:
                self.signals.failed.emit(job, str(error))
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            if job == self.export_job:
                self.export_proc = None

    def on_exported(self, job, path):
        if job != self.export_job:
            return
        self.progress.setValue(1000)
        self.progress.setFormat("Export complete 100%")
        self.status.setText(f"Zapisano: {path}")
        self.export_button.setEnabled(True)

    def closeEvent(self, event):
        self.download_job += 1
        self.export_job += 1
        self.stop_process(self.download_proc)
        self.stop_process(self.export_proc)
        self.player.stop()
        self.player.setSource(QUrl())
        if self.directory:
            self.old_dirs.append(self.directory)
        self.cleanup()
        super().closeEvent(event)


def self_check():
    for path in (FFMPEG, FFPROBE, YTDLP, str(TOOLS / "node.exe")):
        if not Path(path).is_file():
            raise RuntimeError(f"Brak narzędzia: {path}")
        result = subprocess.run([path, "--version"] if path.endswith(("yt-dlp.exe", "node.exe")) else [path, "-version"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                creationflags=CREATE_NO_WINDOW, timeout=25)
        if result.returncode:
            raise RuntimeError(f"Nie działa narzędzie: {path}")
    app = QApplication([])
    player = QMediaPlayer()
    output = QAudioOutput()
    player.setAudioOutput(output)
    del player, output
    app.quit()


def media_check():
    log = Path.cwd() / "media-check.log"
    try:
        with tempfile.TemporaryDirectory(prefix="ytcut-check-") as directory:
            sample = str(Path(directory) / "sample.mp4")
            out = str(Path(directory) / "result.mp4")
            create = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                      "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25",
                      "-t", "1.5", "-c:v", "libx264", "-preset", "ultrafast",
                      "-pix_fmt", "yuv420p", sample]
            subprocess.run(create, check=True, timeout=20, creationflags=CREATE_NO_WINDOW)
            export = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                                     "-ss", ".25", "-i", sample, "-t", ".5", "-c:v",
                                     "libx264", "-crf", "18", "-preset", "fast", out],
                                    capture_output=True, text=True, timeout=20,
                                    creationflags=CREATE_NO_WINDOW)
            if export.returncode:
                raise RuntimeError(f"FFmpeg export failed: {export.stderr[-1800:]}")
            check = subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                                    "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
                                    out], capture_output=True, text=True, timeout=15,
                                   creationflags=CREATE_NO_WINDOW)
            if check.returncode or not (0.4 <= float(check.stdout.strip()) <= .6):
                raise RuntimeError("Media export validation failed")
            app = QApplication([])
            player = QMediaPlayer()
            sink = QVideoSink()
            player.setVideoSink(sink)
            frames = [0]
            sink.videoFrameChanged.connect(lambda frame: frames.__setitem__(0, frames[0] + int(frame.isValid())))
            player.setSource(QUrl.fromLocalFile(sample))
            player.play()
            deadline = time.monotonic() + 12
            while frames[0] == 0 and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.025)
            if not frames[0]:
                raise RuntimeError(f"No decoded video frames, error: {player.errorString()}, status: {player.mediaStatus()}")
            player.stop()
            player.setSource(QUrl())
            del player, sink
            app.quit()
            log.write_text(f"PASS: {frames[0]} decoded frame(s), FFmpeg export verified\n")
    except Exception as exc:
        log.write_text(f"FAIL: {exc}\n")
        raise SystemExit(1)


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        self_check()
    elif "--media-check" in sys.argv:
        media_check()
    else:
        if sys.platform == "win32":
            import ctypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
            kernel.CreateMutexW.restype = ctypes.c_void_p
            instance_mutex = kernel.CreateMutexW(None, False, "Local\\YT-Cut-jarczakpawel")
            if ctypes.get_last_error() == 183:
                ctypes.windll.user32.MessageBoxW(None, "YT Cut is already running.", "YT Cut", 0x40)
                sys.exit(0)
        app = QApplication(sys.argv)
        app.setApplicationName("YT Cut")
        window = Window()
        window.show()
        sys.exit(app.exec())

