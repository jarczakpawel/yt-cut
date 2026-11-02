import os
import re
import json
import sys
import math
import shutil
import subprocess
import tempfile
import time
import threading
from pathlib import Path
from urllib.parse import urlparse
from hls_clip import download as download_hls, FastUnavailable
from crop_geometry import ffmpeg_crop
from crop_editor_qt import CropPanel
from mpv_player import VideoSurface, MpvPlayer

from PySide6.QtCore import Qt, QTimer, Signal, QObject, QRectF, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF, QPalette
from PySide6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QComboBox, QCheckBox, QDoubleSpinBox, QProgressBar,
    QFrame, QStackedWidget, QScrollArea, QSizePolicy, QListView,
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
    downloaded = Signal(int, str, int, float, str, str)
    failed = Signal(int, str, str)
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
        self.setWindowTitle("YT Cut v1.0.19")
        self.resize(1060, 750)
        self.setMinimumSize(640, 420)
        self.duration = 0
        self.start = 0
        self.end = 0
        self.anchor = 0
        self.position = 0
        self.fps = 30
        self.media = None
        self.preview_media = None
        self.crop_rect = None
        self.crop_original = None
        self.directory = None
        self.old_dirs = []
        self.download_job = 0
        self.export_job = 0
        self.download_proc = None
        self.export_proc = None
        self.exporting = False
        self.export_lock = threading.Lock()
        self.restoring = False
        self.last_error = ""
        self.is_downloading = False
        self.download_has_percent = False
        self.download_started_at = 0.0
        self.download_phase = "Pobieranie"
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

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setCentralWidget(self.scroll)
        center = QWidget()
        center.setObjectName("mainContent")
        center.setAutoFillBackground(True)
        palette = center.palette()
        palette.setColor(QPalette.Window, QColor("#161920"))
        center.setPalette(palette)
        self.scroll.viewport().setStyleSheet("background-color:#161920;")
        self.scroll.setWidget(center)
        main = QVBoxLayout(center)
        main.setContentsMargins(16, 12, 16, 12)
        main.setSpacing(8)
        self.content_layout = main

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

        self.video = VideoSurface()
        self.video_stack = QStackedWidget()
        self.video_stack.addWidget(self.video)
        self.video_stack.setMinimumHeight(290)
        self.video_stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.crop_panel = None
        main.addWidget(self.video_stack, 1)
        self.crop_toolbar_index = main.count()
        self.player = None

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
        self.crop_button = QPushButton("Kadruj obraz")
        self.crop_button.clicked.connect(self.toggle_crop)
        controls.addWidget(self.crop_button)
        self.crop_reset = QPushButton("Resetuj kadr")
        self.crop_reset.clicked.connect(self.reset_crop)
        self.crop_reset.setVisible(False)
        controls.addWidget(self.crop_reset)
        self.crop_cancel = QPushButton("Anuluj kadr")
        self.crop_cancel.clicked.connect(self.cancel_crop)
        self.crop_cancel.setVisible(False)
        controls.addWidget(self.crop_cancel)
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
        marks = QHBoxLayout()
        self.mark_start_button = QPushButton("Ustaw START")
        self.mark_end_button = QPushButton("Ustaw KONIEC")
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
        self.format.setView(QListView())
        self.format.view().setStyleSheet(
            "QListView { background:#242a34; color:#ebedf2; border:1px solid #424a58; "
            "selection-background-color:#ed5350; selection-color:#ffffff; outline:0; }"
            "QListView::item { padding:6px 8px; min-height:25px; }"
            "QListView::item:selected { background:#ed5350; color:#ffffff; }")
        settings.addWidget(self.format)
        self.fade_in = QCheckBox("Podgłośnienie (START)")
        self.fade_out = QCheckBox("Ściszenie (KONIEC)")
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
        self.cancel_export_button = QPushButton("Anuluj eksport")
        self.cancel_export_button.clicked.connect(self.cancel_export)
        self.cancel_export_button.setVisible(False)
        foot.addWidget(self.cancel_export_button)
        main.addLayout(foot)
        self.update_enabled()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(30)
        self.setStyleSheet("""
QMainWindow,QScrollArea,QWidget#mainContent { background:#161920; }
QWidget { color:#ebedf2; font-size:13px; }
QComboBox QAbstractItemView { background:#242a34; color:#ebedf2; selection-background-color:#ed5350; selection-color:#ffffff; }
QComboBox QAbstractItemView::item:selected { background:#ed5350; color:#ffffff; }
QScrollBar:vertical { background:#1c212a; width:10px; margin:0; }
QScrollBar::handle:vertical { background:#46505f; border-radius:5px; min-height:35px; }
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical { height:0; }
QLabel#subtle { color:#a4adbc; }
QLabel#clock { font-family:Consolas, monospace; font-size:14px; font-weight:600; }
QLineEdit,QComboBox,QDoubleSpinBox { background:#242a34; border:1px solid #424a58; padding:8px; border-radius:7px; min-height:21px; }
QSpinBox { background:#242a34; color:#ebedf2; border:1px solid #424a58; padding:3px; border-radius:6px; min-height:22px; }
QWidget#cropTools { background:#20252e; border:1px solid #343c47; border-radius:7px; }
QLineEdit:focus { border-color:#ed5a54; }
QPushButton { background:#2b313c; border:1px solid #414956; border-radius:7px; padding:9px 13px; }
QPushButton:hover { background:#3a414e; }
QPushButton:disabled { color:#66707e; }
QPushButton#primary { background:#ed5350; border:1px solid #f5766d; color:white; font-weight:600; }
QPushButton#primary:hover { background:#fa6a61; }
VideoSurface { background:#101216; }
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
        self.crop_button.setEnabled(ready)
        self.crop_reset.setEnabled(ready)
        self.crop_cancel.setEnabled(ready)
        self.crop_reset.setVisible(self.crop_panel is not None)
        self.crop_cancel.setVisible(self.crop_panel is not None)
        for widget in (self.play_button, self.back_button, self.next_button, self.restart_button,
                       self.mark_start_button, self.mark_end_button, self.start_edit, self.end_edit):
            widget.setEnabled(ready)
        self.export_button.setEnabled(ready and not self.exporting)
        self.cancel_export_button.setVisible(self.exporting)

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

    def tick(self):
        if self.player is None:
            return
        if self.player.error:
            self.status.setText(
                f"Błąd silnika mpv: {self.player.error} | diagnostyka: {self.player.startup_log}")
        if self.media and self.player.playbackState() == MpvPlayer.PlayingState:
            self.position_changed(self.player.position())
            if self.player.position() == 0 and time.monotonic() - getattr(self, "play_requested_at", time.monotonic()) > 5:
                state = self.player.state
                self.status.setText(
                    f"mpv: brak postępu | gotowy={self.player.ready} | "
                    f"pause={state.get('pause')} | idle={state.get('core-idle')} | "
                    f"video={state.get('vo-configured')} | log: {self.player.log_path}")
        if self.is_downloading and self.download_has_percent and int(time.monotonic()) != self.progress_display_at:
            now = time.monotonic()
            elapsed = now - self.progress_first_at
            delta = self.progress_value - self.progress_first_value
            goal = .70 if self.download_phase == "Pobieranie segmentów" else .99 if self.download_phase == "Kodowanie lokalne" else 1
            eta = elapsed * (goal - self.progress_value) / delta if delta > .02 and elapsed >= 3 and now - self.progress_last_at < 10 else None
            remaining = f"~{human_time(round(eta) * 1000)}" if eta is not None else "obliczanie..."
            if self.progress_value >= .99:
                remaining = "finalizacja..."
            display = f"{self.download_phase}: {self.progress_value * 100:.1f}%   |   czas: {human_time(int(elapsed) * 1000)}   |   ETA etapu: {remaining}"
            self.progress.setFormat(display)
            self.status.setText(display)
            self.progress_display_at = int(now)
        if self.is_downloading and not self.download_has_percent:
            elapsed = round(time.monotonic() - self.download_started_at)
            self.progress.setFormat(f"Downloading fragment... {elapsed} s" if self.only_fragment.isChecked()
                                    else f"Connecting... {elapsed} s")
        if not self.media or self.restoring:
            return
        if self.player.playbackState() == MpvPlayer.PlayingState:
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
        if not self.player.ready:
            self.status.setText(
                f"mpv nie został uruchomiony: {self.player.error or 'inicjalizacja'} | "
                f"{self.player.startup_log}")
            return
        if self.player.playbackState() == MpvPlayer.PlayingState:
            self.pause_restore()
        else:
            start = self.anchor
            if self.selection.isChecked() and not self.start <= start < self.end:
                start = self.start
            self.seek(start)
            self.player.play()
            self.play_requested_at = time.monotonic()
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
        self.play_requested_at = time.monotonic()
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
        self.close_crop()
        self.crop_rect = None
        self.update_crop_status()
        self.player.stop()
        self.player.setSource(None)
        self.media = None
        self.preview_media = None
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
        if self.exporting:
            self.cancel_export()
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
        self.download_phase = "Pobieranie"
        self.progress_first_at = 0.0
        self.progress_display_at = -1
        job, directory = self.download_job, self.directory
        threading.Thread(target=self.download_worker, args=(job, url, directory, section), daemon=True).start()

    def download_worker(self, job, url, directory, section):
        path = None
        errors = []
        start_ms = end_ms = None
        if section:
            a, b = section[1:].split("-", 1)
            start_ms, end_ms = parse_clock(a), parse_clock(b)
        try:
            if job != self.download_job:
                return
            if section:
                try:
                    path = download_hls(
                        url, start_ms, end_ms, directory, YTDLP, FFMPEG,
                        lambda percent, phase: self.signals.progress.emit(job, percent / 100, phase),
                        lambda: job != self.download_job,
                        lambda proc: setattr(self, "download_proc", proc) if job == self.download_job else self.stop_process(proc),
                    )
                    self.signals.progress.emit(job, .99, "Finalizacja")
                except FastUnavailable:
                    self.signals.progress.emit(job, 0, "Pobieranie")
            if path is None:
                args = [YTDLP, "--no-playlist", "--no-mtime", "--newline", "--no-colors",
                        "--progress", "--js-runtimes", "node", "--no-warnings",
                        "-f", "bv*+ba/b", "--merge-output-format", "mkv",
                        "-o", str(Path(directory) / "source.%(ext)s"),
                        "--print", "after_move:YT_CUT_FILE:%(filepath)s"]
                if section:
                    args += ["--download-sections", section, "-S", "proto:m3u8,vcodec:h264",
                             "--downloader-args", "ffmpeg_o:-progress pipe:2 -nostats"]
                args.append(url)
                proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, errors="replace", creationflags=CREATE_NO_WINDOW)
                self.download_proc = proc
                for raw in proc.stdout:
                    line = raw.strip()
                    if line.startswith("YT_CUT_FILE:"):
                        path = line.removeprefix("YT_CUT_FILE:").strip()
                    if line:
                        errors.append(line)
                        errors = errors[-15:]
                    if section and line.startswith("out_time_us="):
                        try:
                            value = int(line.split("=", 1)[1])
                            self.signals.progress.emit(job, min(.70, value / ((end_ms - start_ms) * 1000) * .70), "Pobieranie")
                        except ValueError:
                            pass
                    elif not section:
                        found = re.search(r"\[download\]\s+(\d+(?:\.\d+)?)%", line)
                        if found:
                            self.signals.progress.emit(job, float(found.group(1)) / 100, "Pobieranie")
                if proc.wait():
                    raise RuntimeError("\n".join(errors[-6:]))
                if job != self.download_job:
                    return
                if not path or not Path(path).is_file():
                    files = [file for file in Path(directory).glob("source.*")
                             if file.is_file() and file.suffix not in (".part", ".ytdl")]
                    if not files:
                        raise RuntimeError("Brak pobranego pliku")
                    path = str(max(files, key=lambda file: file.stat().st_size))
                if section:
                    preview = str(Path(directory) / "preview.mkv")
                    args = [FFMPEG, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
                            "-progress", "pipe:1", "-i", path,
                            "-t", f"{(end_ms - start_ms) / 1000:.3f}",
                            "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264",
                            "-preset", "ultrafast", "-crf", "19", "-pix_fmt", "yuv420p",
                            "-c:a", "aac", "-b:a", "192k", "-f", "matroska", preview]
                    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            text=True, errors="replace", creationflags=CREATE_NO_WINDOW)
                    self.download_proc = proc
                    self.signals.progress.emit(job, .70, "Kodowanie lokalne")
                    for raw in proc.stdout:
                        line = raw.strip()
                        if line.startswith("out_time_us="):
                            try:
                                value = int(line.split("=", 1)[1])
                                self.signals.progress.emit(job, min(.99, .70 + value / ((end_ms - start_ms) * 1000) * .29), "Kodowanie lokalne")
                            except ValueError:
                                pass
                        elif line:
                            errors.append(line)
                            errors = errors[-10:]
                    if proc.wait():
                        raise RuntimeError("\n".join(errors[-6:]))
                    if job != self.download_job:
                        return
                    Path(path).unlink(missing_ok=True)
                    path = preview
            probe = subprocess.run(
                [FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
                 "stream=avg_frame_rate,codec_type,codec_name", "-show_entries", "format=duration",
                 "-of", "json", path], capture_output=True, text=True,
                creationflags=CREATE_NO_WINDOW, timeout=30)
            if probe.returncode:
                raise RuntimeError(probe.stderr.strip())
            info = json.loads(probe.stdout)
            duration = round(float(info["format"]["duration"]) * 1000)
            if duration <= 1:
                raise RuntimeError("Nieprawidłowy czas trwania")
            fps = 30.0
            streams = info.get("streams", [])
            video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
            if video_stream:
                numerator, denominator = video_stream.get("avg_frame_rate", "30/1").split("/")
                if int(denominator):
                    fps = float(numerator) / float(denominator)
            if job != self.download_job:
                return
            self.signals.downloaded.emit(job, path, duration, fps if fps > 0 else 30, section or "", path)
        except InterruptedError:
            pass
        except Exception as error:
            if job == self.download_job:
                self.signals.failed.emit(job, "Pobieranie", str(error))
        finally:
            if job == self.download_job:
                self.download_proc = None
            else:
                try:
                    shutil.rmtree(directory)
                except OSError:
                    pass

    def on_progress(self, job, fraction, name):
        if job != (self.export_job if name == "Eksport" else self.download_job):
            return
        if name != "Eksport":
            if name != self.download_phase:
                self.download_phase = name
                self.download_has_percent = False
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

    def on_downloaded(self, job, path, duration, fps, section, preview):
        if job != self.download_job:
            return
        self.is_downloading = False
        self.crop_rect = None
        self.update_crop_status()
        self.media, self.preview_media, self.duration, self.end, self.fps = path, preview, duration, duration, fps
        self.position = self.anchor = self.start = 0
        self.player.setSource(path)
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

    def on_failed(self, job, kind, message):
        if kind == "Pobieranie":
            if job != self.download_job:
                return
            self.is_downloading = False
            self.download_button.setEnabled(True)
            self.cancel_button.setEnabled(False)
        else:
            if job != self.export_job:
                return
            self.exporting = False
            self.update_enabled()
        self.status.setText(f"Błąd {kind.lower()}: {message[-420:]}")
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setFormat("Błąd")

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

    def update_crop_status(self):
        if self.crop_rect:
            x, y, width, height = self.crop_rect
            self.crop_button.setToolTip(f"Kadr: {width} x {height} px | X={x} Y={y}")
        else:
            self.crop_button.setToolTip("Kadrowanie: wyłączone")
        editing = self.crop_panel is not None
        self.crop_button.setText("Zakończ kadrowanie" if editing else "Kadruj obraz")
        self.crop_reset.setVisible(editing)
        self.crop_cancel.setVisible(editing)

    def crop_changed(self, crop):
        self.crop_rect = crop
        self.update_crop_status()

    def reset_crop(self):
        self.crop_rect = None
        self.close_crop()

    def cancel_crop(self):
        self.crop_rect = self.crop_original
        self.close_crop()

    def close_crop(self):
        if self.crop_panel is not None:
            self.video_stack.setCurrentWidget(self.video)
            self.content_layout.removeWidget(self.crop_panel.toolbar)
            self.crop_panel.toolbar.deleteLater()
            self.video_stack.removeWidget(self.crop_panel)
            self.crop_panel.deleteLater()
            self.crop_panel = None
            self.crop_original = None
            self.player.pause()
            self.update_enabled()
        self.update_crop_status()

    def toggle_crop(self):
        if self.crop_panel is not None:
            self.close_crop()
        else:
            self.open_crop()

    def open_crop(self):
        if not self.media or self.crop_panel is not None:
            return
        self.player.pause()
        self.play_button.setText("▶  Odtwórz")
        image = str(Path(self.directory) / "crop-frame.png")
        position = min(self.position, max(0, self.duration - 80)) / 1000
        try:
            output = subprocess.run(
                [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                 "-ss", f"{position:.3f}", "-i", self.media,
                 "-frames:v", "1", "-vf",
                 "scale=trunc(iw*sar/2)*2:ih,setsar=1", image],
                capture_output=True, text=True, timeout=25,
                creationflags=CREATE_NO_WINDOW,
            )
            if output.returncode or not Path(image).is_file():
                raise RuntimeError(output.stderr[-350:] or "Brak klatki filmu")
            self.crop_original = self.crop_rect
            self.crop_panel = CropPanel(image, self.crop_rect, self.crop_changed)
            self.video_stack.addWidget(self.crop_panel)
            self.content_layout.insertWidget(self.crop_toolbar_index, self.crop_panel.toolbar)
            self.crop_panel.toolbar.show()
            self.video_stack.setCurrentWidget(self.crop_panel)
            self.update_crop_status()
            QTimer.singleShot(0, lambda: self.scroll.ensureWidgetVisible(self.video_stack, 0, 0))
            for widget in (self.play_button, self.back_button, self.next_button, self.restart_button):
                widget.setEnabled(False)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            self.status.setText(f"Błąd kadrowania: {exc}")

    def export(self):
        if not self.media or self.exporting:
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
        self.export_job += 1
        job = self.export_job
        temp = DOWNLOADS / f".cut{number}.{job}.{mode}.part"
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
            args += ["-map", "0:v:0", "-map", "0:a:0?"]
            if self.crop_rect:
                args += ["-vf", ffmpeg_crop(self.crop_rect)]
            args += ["-c:v", "libx264", "-preset", "fast", "-crf", "18",
                     "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                     "-movflags", "+faststart"]
        else:
            args += ["-map", "0:a:0", "-vn"]
            args += ["-c:a", "libmp3lame", "-q:a", "2"] if mode == "mp3" else ["-c:a", "pcm_s16le"]
        if effects:
            args += ["-af", ",".join(effects)]
        args += ["-f", mode, str(temp)]
        self.exporting = True
        self.update_enabled()
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
            if rc and job == self.export_job:
                raise RuntimeError("\n".join(errors[-5:]) or f"FFmpeg: {rc}")
            with self.export_lock:
                if job != self.export_job:
                    return
                temp.replace(target)
            self.signals.exported.emit(job, str(target))
        except Exception as error:
            if job == self.export_job:
                self.signals.failed.emit(job, "Eksport", str(error))
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            if job == self.export_job:
                self.export_proc = None

    def cancel_export(self):
        if not self.exporting:
            return
        with self.export_lock:
            self.export_job += 1
            self.exporting = False
        process = self.export_proc
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        self.update_enabled()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setFormat("Eksport anulowany")
        self.status.setText("Eksport anulowany. Nie zapisano pliku.")

    def on_exported(self, job, path):
        if job != self.export_job:
            return
        self.exporting = False
        self.update_enabled()
        self.progress.setValue(1000)
        self.progress.setFormat("Export complete 100%")
        self.status.setText(f"Zapisano: {path}")

    def closeEvent(self, event):
        self.download_job += 1
        self.export_job += 1
        self.stop_process(self.download_proc)
        self.stop_process(self.export_proc)
        self.player.stop()
        self.player.close()
        if self.directory:
            self.old_dirs.append(self.directory)
        self.cleanup()
        super().closeEvent(event)


def self_check():
    for path in (FFMPEG, FFPROBE, YTDLP, str(TOOLS / "node.exe"),
                 str(TOOLS / "vulkan-1.dll"), str(TOOLS / "libmpv-2.dll")):
        if not Path(path).is_file():
            raise RuntimeError(f"Brak narzędzia: {path}")
        if path.endswith(".dll"):
            import ctypes
            library = ctypes.WinDLL(path) if path.endswith("vulkan-1.dll") else ctypes.CDLL(path)
            if path.endswith("libmpv-2.dll"):
                library.mpv_client_api_version.restype = ctypes.c_ulong
                if not library.mpv_client_api_version():
                    raise RuntimeError("libmpv API unavailable")
            continue
        command = [path, "--version"] if path.endswith(("yt-dlp.exe", "node.exe")) else [path, "-version"]
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                creationflags=CREATE_NO_WINDOW, timeout=25)
        if result.returncode:
            raise RuntimeError(f"Nie działa narzędzie: {path}")


def media_check():
    log = Path.cwd() / "media-check.log"
    try:
        with tempfile.TemporaryDirectory(prefix="ytcut-check-") as directory:
            video = str(Path(directory) / "vp9-opus.mkv")
            converted = subprocess.run(
                [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=25",
                 "-f", "lavfi", "-i", "sine=frequency=440",
                 "-t", "2.0", "-c:v", "libvpx-vp9", "-deadline", "realtime",
                 "-cpu-used", "8", "-b:v", "0", "-crf", "38",
                 "-c:a", "libopus", video],
                capture_output=True, text=True, timeout=50, creationflags=CREATE_NO_WINDOW)
            if converted.returncode:
                raise RuntimeError(converted.stderr[-1200:])
            app = QApplication([])
            window = Window()
            window.resize(1000, 760)
            window.show()
            app.processEvents()
            window.player = MpvPlayer(window.video)
            deadline = time.monotonic() + 10
            while not window.player.ready and not window.player.error and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.025)
            if not window.player.ready:
                raise RuntimeError(f"mpv startup in actual Window: {window.player.error}")
            window.media = video
            window.duration = 2000
            window.end = 2000
            window.update_fields()
            window.update_enabled()
            window.player.setSource(video)
            window.player.play()
            deadline = time.monotonic() + 12
            while window.player.position() < 400 and not window.player.error and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.03)
            if window.player.position() < 400:
                raise RuntimeError(
                    f"Actual-window playback failed: position={window.player.position()}, "
                    f"state={window.player.state}, error={window.player.error}")
            position = window.player.position()
            window.player.pause()
            window.player.setPosition(750)
            window.directory = directory
            window.resize(900, 600)
            app.processEvents()
            window.open_crop()
            app.processEvents()
            if window.crop_panel is None:
                raise RuntimeError(f"Crop panel did not open: {window.status.text()}")
            if window.video_stack.height() < 290 or window.crop_panel.canvas.height() < 230:
                raise RuntimeError(
                    f"Crop preview too small: stack={window.video_stack.height()}, "
                    f"canvas={window.crop_panel.canvas.height()}")
            if window.crop_panel.toolbar.parentWidget() is not window.scroll.widget():
                raise RuntimeError("Crop controls must be outside video viewport")
            if window.scroll.verticalScrollBar().maximum() < 1:
                raise RuntimeError("Low-resolution window cannot scroll lower controls")
            window.crop_panel.set_crop((22, 36, 240, 420))
            if window.crop_rect != (22, 36, 240, 420):
                raise RuntimeError("Crop selection did not update export coordinates")
            window.toggle_crop()
            if window.crop_panel is not None or window.crop_rect != (22, 36, 240, 420):
                raise RuntimeError("Crop was not kept when exiting edit mode")
            window.open_crop()
            window.crop_panel.set_crop((36, 50, 200, 400))
            window.cancel_crop()
            if window.crop_panel is not None or window.crop_rect != (22, 36, 240, 420):
                raise RuntimeError("Cancel crop did not restore the previous crop")
            window.open_crop()
            window.reset_crop()
            if window.crop_rect is not None or window.crop_panel is not None:
                raise RuntimeError("Reset crop did not remove the crop and exit editing")
            window.open_crop()
            window.crop_panel.set_crop((36, 50, 200, 400))
            window.cancel_crop()
            if window.crop_rect is not None or window.crop_panel is not None:
                raise RuntimeError("Cancel after resetting did not discard the new crop")
            if window.crop_reset.isVisible() or window.crop_cancel.isVisible():
                raise RuntimeError("Crop action buttons remain visible after cancelling")
            if window.scroll.widget().palette().color(QPalette.Window).name() != "#161920":
                raise RuntimeError("The central scrollable panel has the wrong theme background")
            if "#242a34" not in window.format.view().styleSheet():
                raise RuntimeError("Dropdown list is missing dark theme styling")
            global DOWNLOADS
            DOWNLOADS = Path(directory) / "Downloads"
            window.export()
            if not window.exporting or not window.cancel_export_button.isVisible():
                raise RuntimeError("Export cancellation button not enabled")
            window.cancel_export()
            if window.exporting or window.cancel_export_button.isVisible():
                raise RuntimeError("Export cancellation did not restore controls")
            deadline = time.monotonic() + 6
            while any(DOWNLOADS.glob("*.part")) and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.05)
            if list(DOWNLOADS.glob("*.part")) or list(DOWNLOADS.glob("cut*.mp4")):
                raise RuntimeError("Cancelled export left an output or temporary file")
            window.crop_rect = (22, 36, 240, 420)
            window.export()
            deadline = time.monotonic() + 30
            while window.exporting and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.03)
            outputs = list(DOWNLOADS.glob("cut*.mp4"))
            if window.exporting or len(outputs) != 1:
                raise RuntimeError(f"Export after cancellation failed: {window.status.text()}")
            dimensions = subprocess.run(
                [FFPROBE, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "json", str(outputs[0])],
                capture_output=True, text=True, timeout=15, creationflags=CREATE_NO_WINDOW)
            if dimensions.returncode or json.loads(dimensions.stdout)["streams"][0] != {
                "width": 240, "height": 420}:
                raise RuntimeError("Exported crop dimensions do not match selection")
            window.close()
            log.write_text(
                f"PASS: embedded VP9/Opus playback={position} ms, "
                "900x600 crop, live editing, cancellable export, dark theme and valid 240x420 MP4\n",
                encoding="utf-8")
    except Exception:
        import traceback
        log.write_text(traceback.format_exc(), encoding="utf-8")
        raise SystemExit(1)


def download_check():
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    from types import SimpleNamespace

    log = Path.cwd() / "download-check.log"
    log.write_text("START: Windows download and export verification\n", encoding="utf-8")
    server = None

    class Sink:
        def __init__(self):
            self.values = []

        def emit(self, *values):
            self.values.append(values)

    try:
        with tempfile.TemporaryDirectory(prefix="ytcut-check-") as folder:
            root = Path(folder)
            source = root / "sample.mp4"
            created = subprocess.run(
                [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25",
                 "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                 "-t", "2.0", "-c:v", "libx264", "-preset", "ultrafast",
                 "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)],
                capture_output=True, text=True, timeout=25, creationflags=CREATE_NO_WINDOW,
            )
            if created.returncode:
                raise RuntimeError("Source creation: " + created.stderr[-1000:])

            class Handler(SimpleHTTPRequestHandler):
                def log_message(self, format, *args):
                    pass

            server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=folder))
            threading.Thread(target=server.serve_forever, daemon=True).start()

            signals = SimpleNamespace(
                progress=Sink(), downloaded=Sink(), failed=Sink(), exported=Sink()
            )
            state = SimpleNamespace(
                download_job=1, export_job=1, download_proc=None,
                export_proc=None, export_lock=threading.Lock(), signals=signals,
            )
            directory = str(root / "download")
            Path(directory).mkdir()
            url = f"http://127.0.0.1:{server.server_port}/sample.mp4"
            Window.download_worker(state, 1, url, directory, None)
            if signals.failed.values:
                raise RuntimeError(f"Download failed: {signals.failed.values[-1]}")
            if len(signals.downloaded.values) != 1:
                raise RuntimeError("Download worker returned no media")
            _, media_path, duration_ms, fps, _, preview_path = signals.downloaded.values[0]
            if not Path(preview_path).is_file():
                raise RuntimeError("Missing prepared preview")
            if not Path(media_path).is_file() or not 1500 <= duration_ms <= 2500:
                raise RuntimeError(f"Downloaded media invalid: {media_path}, {duration_ms} ms")

            for ext in ("mp4", "mp3", "wav"):
                target = root / f"cut.{ext}"
                temporary = root / f".cut.{ext}.part"
                command = [
                    FFMPEG, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
                    "-progress", "pipe:1", "-i", media_path, "-t", "1.0",
                ]
                if ext == "mp4":
                    command += ["-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264",
                                "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                                "-c:a", "aac", "-f", "mp4"]
                else:
                    command += ["-map", "0:a:0", "-vn"]
                    command += (["-c:a", "libmp3lame", "-f", "mp3"] if ext == "mp3"
                                else ["-c:a", "pcm_s16le", "-f", "wav"])
                command.append(str(temporary))
                Window.export_worker(state, 1, command, target, temporary, 1.0)
                if signals.failed.values:
                    raise RuntimeError(f"Export {ext} failed: {signals.failed.values[-1]}")
                if not target.is_file():
                    raise RuntimeError(f"Missing {ext} export")
                probe = subprocess.run(
                    [FFPROBE, "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", str(target)],
                    capture_output=True, text=True, timeout=15, creationflags=CREATE_NO_WINDOW,
                )
                if probe.returncode or not .8 <= float(probe.stdout.strip()) <= 1.2:
                    raise RuntimeError(f"Invalid {ext} export: {probe.stderr}")
            log.write_text(
                "PASS: packaged yt-dlp HTTP download, ffprobe JSON parsing, "
                "download_worker and export_worker, MP4/MP3/WAV outputs\n",
                encoding="utf-8",
            )
    except Exception:
        import traceback
        log.write_text(traceback.format_exc(), encoding="utf-8")
        raise SystemExit(1)
    finally:
        if server:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        self_check()
    elif "--media-check" in sys.argv:
        media_check()
    elif "--download-check" in sys.argv:
        download_check()
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
        window.player = MpvPlayer(window.video)
        sys.exit(app.exec())

