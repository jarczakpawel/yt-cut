#!/usr/bin/env python3
import os
os.environ["GDK_BACKEND"] = "x11"

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import Gtk, Gdk, GdkX11, GLib
import json
import fcntl
import math
import re
import shutil
import socket
import signal
import time
import subprocess
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse
from hls_clip import download as download_hls, FastUnavailable
from crop_geometry import ffmpeg_crop

HOME = Path.home()
DOWNLOADS = Path(os.environ.get("XDG_DOWNLOAD_DIR", str(HOME / "Pobrane"))).expanduser()
YTDLP = str(HOME / ".local/bin/yt-dlp") if (HOME / ".local/bin/yt-dlp").is_file() else "yt-dlp"
CSS = """
window { background: #15171d; color: #e8ebf1; }
label { color: #dbe0ea; }
entry, combobox button, spinbutton { background: #242934; color: #f4f6fa; border: 1px solid #404856; border-radius: 7px; padding: 8px; }
entry:focus { border-color: #f26963; }
button { background: #292e39; color: #f2f3f5; border: 1px solid #434a56; border-radius: 7px; padding: 9px 14px; }
button:hover { background: #383e4c; }
button:disabled { opacity: .48; }
button.primary { background: #ec4f4b; border: 1px solid #f06661; color: white; font-weight: bold; }
button.primary:hover { background: #ff6863; }
button.flat { padding: 7px 12px; }
label.title { font-size: 17px; font-weight: bold; }
label.muted { color: #9ca6b7; }
label.clock { font-family: monospace; font-weight: bold; font-size: 14px; }
frame { border: 1px solid #343b48; border-radius: 8px; }
checkbutton { padding: 4px; }
progressbar trough { background: #303641; min-height: 18px; border-radius: 5px; }
progressbar progress { background: #ee5652; border-radius: 3px; }
headerbar { background: #20242d; color: white; border: none; }
"""

def clock(ms):
    ms = max(0, int(ms))
    hours, remain = divmod(ms, 3600000)
    minutes, remain = divmod(remain, 60000)
    seconds, milli = divmod(remain, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{milli:03}"

def parse_clock(value):
    parts = value.strip().replace(",", ".").split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError("Użyj GG:MM:SS.mmm")
    total = 0.0
    for i, part in enumerate(reversed(parts)):
        number = float(part)
        if not math.isfinite(number) or number < 0 or (i < 2 and len(parts) > i + 1 and number >= 60):
            raise ValueError("Nieprawidłowy czas")
        total += number * (60 ** i)
    return int(round(total * 1000))

def human_time(ms):
    h, remainder = divmod(max(0, int(ms)), 3600000)
    m, remainder = divmod(remainder, 60000)
    sec, millis = divmod(remainder, 1000)
    return f"{h} h {m} min {sec}{f'.{millis:03d}' if millis else ''} s"


class Timeline(Gtk.DrawingArea):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.drag = None
        self.set_size_request(-1, 78)
        self.set_tooltip_text("Przeciągaj czerwone znaczniki START/KONIEC. Kliknij pasek, żeby przewinąć. Shift+klik = START, Ctrl+klik = KONIEC.")
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect("draw", self.draw_line)
        self.connect("button-press-event", self.press)
        self.connect("motion-notify-event", self.motion)
        self.connect("button-release-event", self.release)

    def geometry(self):
        width = self.get_allocated_width()
        return 22, max(23, width - 44)

    def position(self, ms):
        left, width = self.geometry()
        return left + width * max(0, min(1, ms / max(1, self.app.duration)))

    def time_at(self, x):
        left, width = self.geometry()
        return round(max(0, min(1, (x - left) / width)) * self.app.duration)

    def draw_line(self, widget, ctx):
        a = self.app
        left, width = self.geometry()
        right = left + width
        y = 31
        ctx.set_source_rgb(.18, .21, .26)
        ctx.rectangle(left, y - 7, width, 14)
        ctx.fill()
        if not a.duration:
            ctx.set_source_rgb(.53, .56, .64)
            ctx.select_font_face("Sans", 0, 0)
            ctx.set_font_size(12)
            ctx.move_to(left + 8, y + 30)
            ctx.show_text("Wklej URL u góry i kliknij Pobierz")
            return False
        start = self.position(a.start)
        end = self.position(a.end)
        ctx.set_source_rgb(.94, .33, .31)
        ctx.rectangle(start, y - 7, max(2, end - start), 14)
        ctx.fill()
        step = next((s for s in (.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600, 7200) if width * s / (a.duration / 1000) >= 90), 14400)
        ctx.set_font_size(10)
        ctx.select_font_face("Sans", 0, 0)
        tick = 0.0
        while tick <= a.duration / 1000 + 0.0001:
            x = self.position(tick * 1000)
            ctx.set_source_rgb(.53, .56, .63)
            ctx.set_line_width(1)
            ctx.move_to(x, 48)
            ctx.line_to(x, 54)
            ctx.stroke()
            t = clock(round(tick * 1000))
            short = t[:-4] if step >= 1 else t
            ctx.move_to(min(x, max(left, right - 65)), 67)
            ctx.show_text(short)
            tick += step
        for x in (start, end):
            ctx.set_source_rgb(1, .57, .51)
            ctx.set_line_width(2)
            ctx.move_to(x, 12)
            ctx.line_to(x, 48)
            ctx.stroke()
            ctx.move_to(x - 6, 12)
            ctx.line_to(x + 6, 12)
            ctx.line_to(x, 21)
            ctx.close_path()
            ctx.fill()
        marker = self.position(a.anchor_ms)
        ctx.set_source_rgb(1, .72, .19)
        ctx.set_line_width(2)
        ctx.move_to(marker, 2)
        ctx.line_to(marker, 47)
        ctx.stroke()
        ctx.set_font_size(11)
        ctx.move_to(min(marker + 5, right - 45), 10)
        ctx.show_text("PUNKT")
        x = self.position(a.position_ms)
        ctx.set_source_rgb(1, 1, 1)
        ctx.set_line_width(1.5)
        ctx.move_to(x, 18)
        ctx.line_to(x, 46)
        ctx.stroke()
        ctx.move_to(x - 5, 8)
        ctx.line_to(x + 5, 8)
        ctx.line_to(x, 16)
        ctx.close_path()
        ctx.fill()
        return False

    def press(self, widget, event):
        if not self.app.duration or event.button != 1:
            return False
        start = self.position(self.app.start)
        end = self.position(self.app.end)
        if event.state & Gdk.ModifierType.SHIFT_MASK:
            self.drag = "start"
        elif event.state & Gdk.ModifierType.CONTROL_MASK:
            self.drag = "end"
        elif min(abs(event.x - start), abs(event.x - end)) < 16:
            self.drag = "start" if abs(event.x - start) <= abs(event.x - end) else "end"
        else:
            self.drag = "cursor"
        self.apply(event.x)
        return True

    def motion(self, widget, event):
        if self.drag:
            self.apply(event.x)
            return True
        return False

    def release(self, widget, event):
        if self.drag:
            self.apply(event.x)
            self.drag = None
            self.app.apply_loop()
            return True
        return False

    def apply(self, x):
        a = self.app
        ms = self.time_at(x)
        if self.drag == "start":
            a.start = min(ms, a.end - 1)
            a.set_time_entries()
            a.set_seek(a.start)
        elif self.drag == "end":
            a.end = max(ms, a.start + 1)
            a.set_time_entries()
            a.set_seek(max(a.start, a.end - 40))
        else:
            a.anchor_ms = ms
            a.set_seek(ms)
        self.queue_draw()

class YTCut(Gtk.Window):
    def __init__(self):
        super().__init__(title="YT Cut")
        self.set_default_size(1060, 790)
        self.set_size_request(740, 610)
        self.set_border_width(0)
        self.connect("delete-event", self.shutdown)
        self.duration = 0
        self.start = 0
        self.end = 0
        self.position_ms = 0
        self.anchor_ms = 0
        self.frame_pending = False
        self.paused = True
        self.media = None
        self.crop_rect = None
        self.workdir = None
        self.job_id = 0
        self.export_id = 0
        self.download_proc = None
        self.export_proc = None
        self.mpv = None
        self.sock = None
        self.sock_buf = b""
        self.ipc_dir = tempfile.mkdtemp(prefix="ytcut-ipc-")
        self.ipc_path = str(Path(self.ipc_dir) / "mpv.sock")
        self.mpv_log = str(Path(self.ipc_dir) / "mpv.log")
        self.mpv_ready = False
        self.last_download_error = ""
        self.updating_entries = False
        self.last_pulse = 0
        self.is_downloading = False
        self.download_has_percent = False
        self.download_started_at = 0.0
        self.download_phase = "Pobieranie"
        self.progress_first_at = 0.0
        self.progress_last_at = 0.0
        self.progress_first_value = 0.0
        self.progress_value = 0.0
        self.progress_display_at = -1

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.props.title = "YT Cut"
        header.props.subtitle = "Precyzyjne wycinanie audio i wideo"
        self.set_titlebar(header)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=13)
        outer.set_border_width(18)
        self.add(outer)

        urlrow = Gtk.Box(spacing=9)
        outer.pack_start(urlrow, False, False, 0)
        self.url = Gtk.Entry()
        self.url.set_placeholder_text("Wklej link YouTube, np. https://www.youtube.com/watch?v=...")
        self.url.connect("activate", self.start_download)
        urlrow.pack_start(self.url, True, True, 0)
        self.download_btn = self.button("Pobierz", self.start_download, "primary")
        urlrow.pack_start(self.download_btn, False, False, 0)
        self.cancel_btn = self.button("Anuluj", self.cancel_download)
        self.cancel_btn.set_sensitive(False)
        urlrow.pack_start(self.cancel_btn, False, False, 0)

        section_row = Gtk.Box(spacing=12)
        outer.pack_start(section_row, False, False, 0)
        self.only_fragment = Gtk.CheckButton(label="Pobierz tylko fragment")
        section_row.pack_start(self.only_fragment, False, False, 0)
        section_row.pack_start(Gtk.Label(label="OD:"), False, False, 0)
        self.fragment_from = Gtk.Entry()
        self.fragment_from.set_width_chars(15)
        self.fragment_from.set_text("00:00:00.000")
        section_row.pack_start(self.fragment_from, False, False, 0)
        section_row.pack_start(Gtk.Label(label="DO:"), False, False, 0)
        self.fragment_to = Gtk.Entry()
        self.fragment_to.set_width_chars(15)
        self.fragment_to.set_text("00:01:00.000")
        section_row.pack_start(self.fragment_to, False, False, 0)
        for field in (self.fragment_from, self.fragment_to):
            field.set_sensitive(False)
        self.only_fragment.connect("toggled", self.fragment_changed)
        for field in (self.fragment_from, self.fragment_to):
            field.connect("changed", self.fragment_changed)
        self.fragment_duration_label = Gtk.Label(label="Długość: 00:01:00.000")
        self.fragment_duration_label.set_xalign(0)
        self.fragment_duration_label.get_style_context().add_class("clock")
        section_row.pack_start(self.fragment_duration_label, False, False, 0)
        self.fragment_changed()

        self.progress = Gtk.ProgressBar()
        self.progress.set_show_text(True)
        self.progress.set_text("Gotowy")
        outer.pack_start(self.progress, False, False, 0)

        self.title_label = Gtk.Label(label="Brak wczytanego filmu")
        self.title_label.set_xalign(0)
        self.title_label.get_style_context().add_class("muted")
        self.title_label.set_ellipsize(3)
        outer.pack_start(self.title_label, False, False, 0)

        video_box = Gtk.Frame()
        outer.pack_start(video_box, True, True, 0)
        self.video = Gtk.DrawingArea()
        self.video.set_size_request(640, 260)
        self.video.connect("draw", self.video_background)
        self.video.connect("realize", self.video_realized)
        self.video_stack = Gtk.Stack()
        self.video_stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.video_stack.add_named(self.video, "video")
        self.video_stack.set_visible_child_name("video")
        self.crop_panel = None
        video_box.add(self.video_stack)

        controls = Gtk.Box(spacing=9)
        outer.pack_start(controls, False, False, 0)
        self.play_btn = self.button("▶  Odtwórz", self.toggle_play, "primary")
        self.play_btn.set_sensitive(False)
        controls.pack_start(self.play_btn, False, False, 0)
        back = self.button("◀ Klatka", lambda *_: self.frame_step(False))
        back.set_sensitive(False)
        controls.pack_start(back, False, False, 0)
        self.frame_back_btn = back
        forward = self.button("Klatka ▶", lambda *_: self.frame_step(True))
        forward.set_sensitive(False)
        controls.pack_start(forward, False, False, 0)
        self.frame_forward_btn = forward
        jump = self.button("↺ Od START", self.jump_start)
        jump.set_sensitive(False)
        controls.pack_start(jump, False, False, 0)
        self.jump_btn = jump
        self.crop_btn = self.button("Kadruj obraz", self.open_crop)
        self.crop_btn.set_sensitive(False)
        controls.pack_start(self.crop_btn, False, False, 0)
        self.crop_reset = self.button("Resetuj kadr", self.reset_crop)
        self.crop_reset.set_no_show_all(True)
        self.crop_reset.hide()
        controls.pack_start(self.crop_reset, False, False, 0)
        controls.pack_start(Gtk.Label(label=""), True, True, 0)
        self.position_label = Gtk.Label(label="00:00:00.000 / 00:00:00.000")
        self.position_label.get_style_context().add_class("clock")
        controls.pack_start(self.position_label, False, False, 0)

        self.timeline = Timeline(self)
        outer.pack_start(self.timeline, False, False, 0)

        times = Gtk.Box(spacing=12)
        outer.pack_start(times, False, False, 0)
        for name, setter in (("START", "start"), ("KONIEC", "end")):
            block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            block.pack_start(Gtk.Label(label=name, xalign=0), False, False, 0)
            field = Gtk.Entry()
            field.set_width_chars(16)
            field.set_text("00:00:00.000")
            field.connect("activate", self.update_time, setter)
            field.connect("focus-out-event", self.time_focus_out, setter)
            block.pack_start(field, False, False, 0)
            if setter == "start":
                self.start_entry = field
            else:
                self.end_entry = field
            times.pack_start(block, False, False, 0)
        mark_start = self.button("Ustaw START z pozycji", lambda *_: self.mark("start"))
        mark_end = self.button("Ustaw KONIEC z pozycji", lambda *_: self.mark("end"))
        marks = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        marks.pack_start(mark_start, False, False, 0)
        marks.pack_start(mark_end, False, False, 0)
        times.pack_start(marks, False, False, 0)
        self.mark_start_btn = mark_start
        self.mark_end_btn = mark_end
        times.pack_start(Gtk.Label(label=""), True, True, 0)
        self.selection_toggle = Gtk.CheckButton(label="Odtwarzaj zaznaczenie")
        self.selection_toggle.set_active(True)
        self.selection_toggle.connect("toggled", self.apply_loop)
        times.pack_start(self.selection_toggle, False, False, 0)
        self.loop_toggle = Gtk.CheckButton(label="Pętla")
        self.loop_toggle.connect("toggled", self.apply_loop)
        times.pack_start(self.loop_toggle, False, False, 0)

        self.sep = Gtk.Separator()
        outer.pack_start(self.sep, False, False, 0)
        settings = Gtk.Box(spacing=12)
        outer.pack_start(settings, False, False, 0)
        settings.pack_start(Gtk.Label(label="Zapisz jako:"), False, False, 0)
        self.format = Gtk.ComboBoxText()
        self.format.append("mp3", "Audio MP3")
        self.format.append("wav", "Audio WAV")
        self.format.append("mp4", "Wideo MP4 (domyślnie)")
        self.format.set_active_id("mp4")
        settings.pack_start(self.format, False, False, 0)
        self.fade_in = Gtk.CheckButton(label="Płynne podgłaśnianie")
        settings.pack_start(self.fade_in, False, False, 0)
        self.fade_out = Gtk.CheckButton(label="Płynne ściszanie")
        settings.pack_start(self.fade_out, False, False, 0)
        settings.pack_start(Gtk.Label(label="Czas:"), False, False, 0)
        self.fade_seconds = Gtk.SpinButton.new_with_range(.1, 10, .1)
        self.fade_seconds.set_value(1)
        self.fade_seconds.set_digits(1)
        settings.pack_start(self.fade_seconds, False, False, 0)
        settings.pack_start(Gtk.Label(label="s"), False, False, 0)

        bottom = Gtk.Box(spacing=12)
        outer.pack_start(bottom, False, False, 0)
        self.status = Gtk.Label(label="Gotowy. Wklej URL, a następnie pobierz film.")
        self.status.set_xalign(0)
        self.status.set_line_wrap(True)
        self.status.set_max_width_chars(95)
        bottom.pack_start(self.status, True, True, 0)
        self.export_btn = self.button("Wytnij i zapisz do Pobrane", self.start_export, "primary")
        self.export_btn.set_sensitive(False)
        bottom.pack_start(self.export_btn, False, False, 0)
        self.progress.set_fraction(0)
        self.show_all()
        GLib.timeout_add(30, self.tick)

    def fragment_changed(self, *_):
        enabled = self.only_fragment.get_active()
        for field in (self.fragment_from, self.fragment_to):
            field.set_sensitive(enabled)
        if not enabled:
            self.fragment_duration_label.set_text("Cały film")
            return
        try:
            begin = parse_clock(self.fragment_from.get_text())
            end = parse_clock(self.fragment_to.get_text())
            if end <= begin:
                raise ValueError("Nieprawidłowy zakres")
            self.fragment_duration_label.set_text(f"Długość: {human_time(end - begin)}")
        except ValueError:
            self.fragment_duration_label.set_text("Nieprawidłowy zakres")

    def button(self, text, handler, style=None):
        b = Gtk.Button(label=text)
        if style:
            b.get_style_context().add_class(style)
        b.connect("clicked", handler)
        return b

    def video_background(self, widget, ctx):
        ctx.set_source_rgb(.065, .075, .09)
        ctx.paint()
        return False

    def video_realized(self, widget):
        xid = widget.get_window().get_xid()
        cmd = [
            "mpv", f"--wid={xid}", "--no-config", "--no-terminal", "--idle=yes",
            "--force-window=yes", "--keep-open=yes", "--pause=yes", "--osc=no",
            "--vo=gpu", "--gpu-context=x11egl", "--osd-level=0",
            "--input-default-bindings=no", "--hwdec=auto-safe",
            f"--input-ipc-server={self.ipc_path}", "--msg-level=all=warn",
        ]
        env = os.environ.copy()
        env.pop("WAYLAND_DISPLAY", None)
        env["DISPLAY"] = Gdk.Display.get_default().get_name()
        self.mpv_log_file = open(self.mpv_log, "w")
        try:
            self.mpv = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL,
                                        stdout=self.mpv_log_file, stderr=subprocess.STDOUT)
        except OSError as exc:
            self.status.set_text(f"Nie można uruchomić odtwarzacza: {exc}")
            self.mpv_log_file.close()
            return
        GLib.timeout_add(80, self.connect_ipc)

    def connect_ipc(self):
        if not self.mpv:
            return False
        if self.mpv.poll() is not None:
            self.status.set_text(f"Błąd uruchamiania odtwarzacza mpv (kod {self.mpv.returncode}). Szczegóły: {self.mpv_log}")
            return False
        sock = None
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(self.ipc_path)
            sock.setblocking(False)
        except OSError:
            if sock:
                sock.close()
            return True
        self.sock = sock
        self.mpv_ready = True
        GLib.io_add_watch(sock.fileno(), GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self.read_ipc)
        for i, name in enumerate(("time-pos", "pause", "duration", "idle-active"), start=1):
            self.command("observe_property", i, name)
        if self.media:
            self.command("loadfile", self.media, "replace")
            self.command("set_property", "pause", True)
            self.apply_loop()
        return False

    def command(self, *args):
        if not self.sock:
            return
        try:
            self.sock.sendall((json.dumps({"command": list(args)}) + "\n").encode())
        except OSError:
            pass

    def read_ipc(self, fd, condition):
        if condition & (GLib.IO_HUP | GLib.IO_ERR):
            self.sock = None
            self.mpv_ready = False
            self.status.set_text(f"Utracono połączenie z mpv. Log: {self.mpv_log}")
            return False
        try:
            data = self.sock.recv(65536)
        except BlockingIOError:
            return True
        except OSError:
            return False
        if not data:
            self.sock = None
            self.mpv_ready = False
            self.status.set_text(f"mpv zakończył pracę. Log: {self.mpv_log}")
            return False
        self.sock_buf += data
        while b"\n" in self.sock_buf:
            line, self.sock_buf = self.sock_buf.split(b"\n", 1)
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if event.get("event") != "property-change":
                continue
            name = event.get("name")
            value = event.get("data")
            if name == "time-pos" and isinstance(value, (int, float)):
                if not self.timeline.drag:
                    self.position_ms = min(self.duration, max(0, round(value * 1000)))
                    if self.frame_pending:
                        self.anchor_ms = self.position_ms
                        self.frame_pending = False
            elif name == "pause" and isinstance(value, bool):
                self.paused = value
                self.play_btn.set_label("▶  Odtwórz" if value else "⏸  Pauza")
        return True

    def set_seek(self, ms):
        self.position_ms = max(0, min(self.duration, int(ms)))
        self.command("seek", self.position_ms / 1000, "absolute+exact")
        self.position_label.set_text(f"{clock(self.position_ms)} / {clock(self.duration)}")
        self.timeline.queue_draw()

    def tick(self):
        if self.is_downloading and not self.download_has_percent:
            self.last_pulse += 1
            if self.last_pulse >= 3:
                self.progress.pulse()
                self.last_pulse = 0
            elapsed = round(time.monotonic() - self.download_started_at)
            self.progress.set_text(f"Pobieranie fragmentu... {elapsed} s" if self.only_fragment.get_active()
                                   else f"Łączenie... {elapsed} s")
        if self.is_downloading and self.download_has_percent and int(time.monotonic()) != self.progress_display_at:
            now = time.monotonic()
            elapsed = now - self.progress_first_at
            delta = (self.progress_value - self.progress_first_value) / 100
            goal = 70 if self.download_phase == "Pobieranie segmentów" else 99 if self.download_phase == "Kodowanie lokalne" else 100
            eta = elapsed * (goal - self.progress_value) / 100 / delta if delta > .02 and elapsed >= 3 and now - self.progress_last_at < 10 else None
            remaining = f"~{human_time(round(eta) * 1000)}" if eta is not None else "obliczanie..."
            if self.progress_value >= 99:
                remaining = "finalizacja..."
            display = f"{self.download_phase}: {self.progress_value:.1f}%   |   czas: {human_time(int(elapsed) * 1000)}   |   ETA etapu: {remaining}"
            self.progress.set_text(display)
            self.status.set_text(display)
            self.progress_display_at = int(now)
        if self.duration:
            self.position_label.set_text(f"{clock(self.position_ms)} / {clock(self.duration)}")
            self.timeline.queue_draw()
            if self.selection_toggle.get_active() and not self.paused and self.position_ms >= self.end:
                if self.loop_toggle.get_active():
                    self.set_seek(self.start)
                else:
                    self.command("set_property", "pause", True)
                    self.set_seek(self.anchor_ms)
        return True

    def toggle_play(self, *_):
        if not self.media:
            return
        if self.paused:
            self.set_seek(self.anchor_ms if not self.selection_toggle.get_active() or self.start <= self.anchor_ms < self.end else self.start)
            self.command("set_property", "pause", False)
        else:
            self.command("set_property", "pause", True)
            self.set_seek(self.anchor_ms)

    def jump_start(self, *_):
        if not self.media:
            return
        self.anchor_ms = self.start if self.selection_toggle.get_active() else 0
        self.set_seek(self.anchor_ms)
        self.command("set_property", "pause", False)

    def frame_step(self, forward):
        if not self.media:
            return
        self.command("set_property", "pause", True)
        self.frame_pending = True
        self.command("frame-step" if forward else "frame-back-step")

    def apply_loop(self, *_):
        if not self.sock:
            return
        if self.media and self.selection_toggle.get_active() and self.loop_toggle.get_active():
            self.command("set_property", "ab-loop-a", self.start / 1000)
            self.command("set_property", "ab-loop-b", self.end / 1000)
            self.command("set_property", "ab-loop-count", "inf")
            self.command("set_property", "loop-file", "no")
        else:
            self.command("set_property", "ab-loop-a", "no")
            self.command("set_property", "ab-loop-b", "no")
            self.command("set_property", "loop-file", "inf" if self.media and self.loop_toggle.get_active() and not self.selection_toggle.get_active() else "no")

    def set_time_entries(self):
        self.updating_entries = True
        self.start_entry.set_text(clock(self.start))
        self.end_entry.set_text(clock(self.end))
        self.updating_entries = False

    def time_focus_out(self, widget, event, name):
        self.update_time(widget, name)
        return False

    def update_time(self, widget, name):
        if not self.media or self.updating_entries:
            return
        try:
            value = parse_clock(widget.get_text())
            if name == "start":
                if not 0 <= value < self.end:
                    raise ValueError("START musi być wcześniejszy niż KONIEC")
                self.start = value
            else:
                if not self.start < value <= self.duration:
                    raise ValueError("KONIEC musi być późniejszy niż START i nie przekraczać długości filmu")
                self.end = value
            self.set_time_entries()
            self.apply_loop()
            self.timeline.queue_draw()
        except ValueError as exc:
            self.status.set_text(str(exc))
            widget.set_text(clock(self.start if name == "start" else self.end))

    def mark(self, name):
        if not self.media:
            return
        value = self.anchor_ms
        if name == "start":
            if value >= self.end:
                self.status.set_text("START musi być wcześniejszy niż KONIEC")
                return
            self.start = value
        else:
            if value <= self.start:
                self.status.set_text("KONIEC musi być późniejszy niż START")
                return
            self.end = value
        self.set_time_entries()
        self.apply_loop()
        self.timeline.queue_draw()

    def clear_video(self):
        self.command("stop")
        self.close_crop()
        self.crop_rect = None
        self.crop_btn.set_sensitive(False)
        self.update_crop_status()
        self.media = None
        self.duration = 0
        self.position_ms = 0
        self.anchor_ms = 0
        self.start = 0
        self.end = 0
        self.paused = True
        self.title_label.set_text("Brak wczytanego filmu")
        self.position_label.set_text("00:00:00.000 / 00:00:00.000")
        self.set_time_entries()
        self.play_btn.set_label("▶  Odtwórz")
        for widget in (self.play_btn, self.frame_back_btn, self.frame_forward_btn, self.jump_btn, self.export_btn, self.mark_start_btn, self.mark_end_btn):
            widget.set_sensitive(False)
        self.timeline.queue_draw()

    def stop_process(self, proc):
        if proc and proc.poll() is None:
            try:
                if os.getpgid(proc.pid) == proc.pid:
                    os.killpg(proc.pid, signal.SIGTERM)
                else:
                    proc.terminate()
            except OSError:
                pass

    def start_download(self, *_):
        url = self.url.get_text().strip()
        u = urlparse(url)
        if u.scheme not in ("http", "https") or not u.netloc:
            self.status.set_text("Wklej poprawny URL filmu.")
            return
        section = None
        if self.only_fragment.get_active():
            try:
                start_ms = parse_clock(self.fragment_from.get_text())
                end_ms = parse_clock(self.fragment_to.get_text())
                if end_ms <= start_ms:
                    raise ValueError("DO musi być późniejsze niż OD")
                section = f"*{clock(start_ms)}-{clock(end_ms)}"
            except ValueError as exc:
                self.status.set_text(f"Nieprawidłowy zakres pobierania: {exc}")
                return
        self.job_id += 1
        self.export_id += 1
        self.stop_process(self.download_proc)
        self.stop_process(self.export_proc)
        old = self.workdir
        old_is_downloading = self.download_proc is not None and self.download_proc.poll() is None
        self.workdir = None
        self.clear_video()
        if old and not old_is_downloading:
            shutil.rmtree(old, ignore_errors=True)
        self.workdir = tempfile.mkdtemp(prefix="yt-cut-")
        self.download_btn.set_sensitive(False)
        self.cancel_btn.set_sensitive(True)
        self.export_btn.set_sensitive(False)
        self.progress.set_fraction(0)
        self.progress.set_pulse_step(0.03)
        self.progress.set_text("Pobieranie fragmentu..." if section else "Pobieranie filmu...")
        self.status.set_text("Pobieranie fragmentu..." if section else "Pobieranie filmu...")
        self.is_downloading = True
        self.download_has_percent = False
        self.download_started_at = time.monotonic()
        self.download_phase = "Pobieranie"
        self.progress_first_at = 0.0
        self.progress_display_at = -1
        self.last_download_error = ""
        job = self.job_id
        threading.Thread(target=self.download_worker, args=(job, url, self.workdir, section), daemon=True).start()

    def download_worker(self, job, url, directory, section):
        filepath = None
        errors = []
        start_ms = end_ms = None
        if section:
            a, b = section[1:].split("-", 1)
            start_ms, end_ms = parse_clock(a), parse_clock(b)
        try:
            if job != self.job_id:
                return
            if section:
                try:
                    filepath = download_hls(
                        url, start_ms, end_ms, directory, YTDLP, "ffmpeg",
                        lambda percent, phase: GLib.idle_add(self.download_progress, job, percent, phase),
                        lambda: job != self.job_id,
                        lambda proc: setattr(self, "download_proc", proc) if job == self.job_id else self.stop_process(proc),
                    )
                    GLib.idle_add(self.download_progress, job, 99, "Finalizacja")
                except FastUnavailable:
                    GLib.idle_add(self.download_fallback, job)
            if filepath is None:
                cmd = [YTDLP, "--no-playlist", "--no-mtime", "--newline", "--no-colors",
                       "--progress", "--js-runtimes", "node", "--no-warnings",
                       "-f", "bv*+ba/b", "--merge-output-format", "mkv",
                       "-o", str(Path(directory) / "source.%(ext)s"),
                       "--print", "after_move:YT_CUT_FILE:%(filepath)s"]
                if section:
                    cmd += ["--download-sections", section, "-S", "proto:m3u8,vcodec:h264",
                            "--downloader-args", "ffmpeg_o:-progress pipe:2 -nostats"]
                cmd.append(url)
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, errors="replace", bufsize=1, start_new_session=True)
                self.download_proc = proc
                for line in proc.stdout:
                    line = line.strip()
                    if line.startswith("YT_CUT_FILE:"):
                        filepath = line.split(":", 1)[1].strip()
                    if line:
                        errors.append(line)
                        errors = errors[-15:]
                    if section and line.startswith("out_time_us="):
                        try:
                            value = int(line.split("=", 1)[1])
                            GLib.idle_add(self.download_progress, job, min(70, value / ((end_ms - start_ms) * 1000) * 70), "Pobieranie")
                        except ValueError:
                            pass
                    elif not section:
                        found = re.search(r"\[download\]\s+(\d+(?:\.\d+)?)%", line)
                        if found:
                            GLib.idle_add(self.download_progress, job, float(found.group(1)), "Pobieranie")
                if proc.wait():
                    raise RuntimeError("\n".join(errors[-6:]))
                if job != self.job_id:
                    return
                if not filepath or not Path(filepath).is_file():
                    candidates = [path for path in Path(directory).glob("source.*")
                                  if path.is_file() and path.suffix not in (".part", ".ytdl")]
                    if not candidates:
                        raise RuntimeError("Nie znaleziono pobranego pliku.")
                    filepath = str(max(candidates, key=lambda path: path.stat().st_size))
                if section:
                    preview = str(Path(directory) / "preview.mkv")
                    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
                           "-progress", "pipe:1", "-i", filepath,
                           "-t", f"{(end_ms - start_ms) / 1000:.3f}",
                           "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264",
                           "-preset", "ultrafast", "-crf", "19", "-pix_fmt", "yuv420p",
                           "-c:a", "aac", "-b:a", "192k", "-f", "matroska", preview]
                    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            text=True, errors="replace", bufsize=1, start_new_session=True)
                    self.download_proc = proc
                    GLib.idle_add(self.download_progress, job, 70, "Kodowanie lokalne")
                    for line in proc.stdout:
                        line = line.strip()
                        if line.startswith("out_time_us="):
                            try:
                                value = int(line.split("=", 1)[1])
                                GLib.idle_add(self.download_progress, job, min(99, 70 + value / ((end_ms - start_ms) * 1000) * 29), "Kodowanie lokalne")
                            except ValueError:
                                pass
                        elif line:
                            errors.append(line)
                            errors = errors[-10:]
                    if proc.wait():
                        raise RuntimeError("\n".join(errors[-6:]))
                    if job != self.job_id:
                        return
                    Path(filepath).unlink(missing_ok=True)
                    filepath = preview
            info = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                   "-of", "default=noprint_wrappers=1:nokey=1", filepath],
                                  capture_output=True, text=True, timeout=20)
            if info.returncode:
                raise RuntimeError(info.stderr.strip())
            duration = round(float(info.stdout.strip()) * 1000)
            if duration <= 1:
                raise RuntimeError("Nie można odczytać długości filmu.")
            GLib.idle_add(self.download_done, job, filepath, duration, section)
        except InterruptedError:
            pass
        except Exception as exc:
            if job == self.job_id:
                GLib.idle_add(self.download_failed, job, str(exc))
        finally:
            if job != self.job_id:
                shutil.rmtree(directory, ignore_errors=True)
            elif self.download_proc and self.download_proc.poll() is not None:
                self.download_proc = None

    def download_fallback(self, job):
        if job == self.job_id:
            self.status.set_text("Szybkie HLS niedostępne, używam pobierania FFmpeg.")
        return False

    def download_progress(self, job, percent, phase="Pobieranie"):
        if job != self.job_id:
            return False
        if phase != self.download_phase:
            self.download_phase = phase
            self.download_has_percent = False
        now = time.monotonic()
        if not self.download_has_percent or percent + 10 < self.progress_value:
            self.progress_first_at = now
            self.progress_first_value = percent
        self.download_has_percent = True
        self.progress_last_at = now
        self.progress_value = percent
        self.progress_display_at = -1
        self.progress.set_fraction(min(1, max(0, percent / 100)))
        return False

    def download_done(self, job, path, duration, section):
        if job != self.job_id:
            return False
        self.is_downloading = False
        self.crop_rect = None
        self.update_crop_status()
        self.crop_btn.set_sensitive(True)
        self.media = path
        self.duration = duration
        self.start = 0
        self.end = duration
        self.position_ms = 0
        self.anchor_ms = 0
        self.set_time_entries()
        self.command("loadfile", path, "replace")
        self.command("set_property", "pause", True)
        self.apply_loop()
        self.title_label.set_text(f"Wczytano {'fragment '+section[1:]+' • ' if section else 'film • '}{clock(duration)}")
        self.status.set_text("Gotowe. Ustaw START/KONIEC, opcjonalnie efekty i kliknij Wytnij.")
        self.download_btn.set_sensitive(True)
        self.cancel_btn.set_sensitive(False)
        self.progress.set_fraction(1)
        self.progress.set_text("Pobrano 100%")
        for widget in (self.play_btn, self.frame_back_btn, self.frame_forward_btn, self.jump_btn, self.export_btn, self.mark_start_btn, self.mark_end_btn):
            widget.set_sensitive(True)
        self.timeline.queue_draw()
        return False

    def download_failed(self, job, message):
        if job != self.job_id:
            return False
        self.is_downloading = False
        self.download_btn.set_sensitive(True)
        self.cancel_btn.set_sensitive(False)
        self.progress.set_fraction(0)
        self.progress.set_text("Błąd pobierania")
        self.status.set_text("Błąd pobierania: " + message[-470:])
        return False

    def cancel_download(self, *_):
        self.is_downloading = False
        self.job_id += 1
        self.stop_process(self.download_proc)
        old = self.workdir
        active = self.download_proc is not None and self.download_proc.poll() is None
        self.workdir = None
        self.clear_video()
        if old and not active:
            shutil.rmtree(old, ignore_errors=True)
        self.download_btn.set_sensitive(True)
        self.cancel_btn.set_sensitive(False)
        self.progress.set_fraction(0)
        self.progress.set_text("Anulowano")
        self.status.set_text("Anulowano pobieranie.")

    def update_crop_status(self):
        self.crop_reset.set_visible(self.crop_rect is not None or self.crop_panel is not None)
        if self.crop_rect:
            x, y, width, height = self.crop_rect
            self.crop_btn.set_tooltip_text(f"Kadr: {width} x {height} px | X={x} Y={y}")
        else:
            self.crop_btn.set_tooltip_text("Kadrowanie: wyłączone")

    def reset_crop(self, *_):
        self.crop_rect = None
        if self.crop_panel is not None:
            self.crop_panel.set_crop((0, 0, self.crop_panel.width, self.crop_panel.height))
        self.update_crop_status()

    def close_crop(self):
        if self.crop_panel:
            self.video_stack.set_visible_child(self.video)
            self.video_stack.remove(self.crop_panel)
            self.crop_panel = None
            self.command("set_property", "pause", True)
            self.crop_btn.set_sensitive(bool(self.media))
            for widget in (self.play_btn, self.frame_back_btn, self.frame_forward_btn, self.jump_btn):
                widget.set_sensitive(bool(self.media))

    def finish_crop(self, crop):
        self.crop_rect = crop
        self.close_crop()
        self.update_crop_status()

    def open_crop(self, *_):
        if not self.media or self.crop_panel:
            return
        self.command("set_property", "pause", True)
        image = str(Path(self.workdir) / "crop-frame.png")
        position = min(self.position_ms, max(0, self.duration - 80)) / 1000
        try:
            output = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-ss", f"{position:.3f}", "-i", self.media, "-frames:v", "1",
                 "-vf", "scale=trunc(iw*sar/2)*2:ih,setsar=1", image],
                capture_output=True, text=True, timeout=25,
            )
            if output.returncode or not Path(image).is_file():
                raise RuntimeError(output.stderr[-350:] or "Brak klatki filmu")
            from crop_editor_gtk import CropPanel
            self.crop_panel = CropPanel(image, self.crop_rect, self.finish_crop, self.close_crop)
            self.video_stack.add_named(self.crop_panel, "crop")
            self.crop_panel.show_all()
            self.video_stack.set_visible_child(self.crop_panel)
            self.crop_btn.set_sensitive(False)
            self.crop_reset.show()
            for widget in (self.play_btn, self.frame_back_btn, self.frame_forward_btn, self.jump_btn):
                widget.set_sensitive(False)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            self.status.set_text(f"Błąd kadrowania: {exc}")

    def start_export(self, *_):
        if not self.media or self.export_proc is not None and self.export_proc.poll() is None:
            return
        self.update_time(self.start_entry, "start")
        self.update_time(self.end_entry, "end")
        if not self.start < self.end <= self.duration:
            self.status.set_text("Nieprawidłowe granice fragmentu.")
            return
        kind = self.format.get_active_id()
        if kind not in ("mp3", "wav", "mp4"):
            return
        DOWNLOADS.mkdir(parents=True, exist_ok=True)
        existing = [int(m.group(1)) for p in DOWNLOADS.iterdir() if (m := re.fullmatch(r"cut(\d+)\.(mp3|wav|mp4)", p.name, re.I))]
        number = max(existing, default=0) + 1
        target = DOWNLOADS / f"cut{number}.{kind}"
        temp_target = DOWNLOADS / f".cut{number}.{kind}.part"
        start = self.start / 1000
        length = (self.end - self.start) / 1000
        fades = []
        requested = self.fade_seconds.get_value()
        if self.fade_in.get_active():
            fades.append(f"afade=t=in:st=0:d={min(requested, length / (2 if self.fade_out.get_active() else 1)):.3f}")
        if self.fade_out.get_active():
            fade_length = min(requested, length / (2 if self.fade_in.get_active() else 1))
            fades.append(f"afade=t=out:st={max(0, length - fade_length):.3f}:d={fade_length:.3f}")
        cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
               "-progress", "pipe:1", "-ss", f"{start:.3f}", "-i", self.media,
               "-t", f"{length:.3f}", "-sn", "-dn"]
        if kind == "mp4":
            cmd += ["-map", "0:v:0", "-map", "0:a:0?"]
            if self.crop_rect:
                cmd += ["-vf", ffmpeg_crop(self.crop_rect)]
            cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "18",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart"]
        else:
            cmd += ["-map", "0:a:0", "-vn", "-c:a", "libmp3lame", "-q:a", "2"] if kind == "mp3" else ["-map", "0:a:0", "-vn", "-c:a", "pcm_s16le"]
        if fades:
            cmd += ["-af", ",".join(fades)]
        cmd += ["-f", {"mp4": "mp4", "mp3": "mp3", "wav": "wav"}[kind], str(temp_target)]
        self.export_id += 1
        job = self.export_id
        self.export_btn.set_sensitive(False)
        self.progress.set_fraction(0)
        self.progress.set_text("Eksport 0%")
        self.status.set_text("Eksport...")
        threading.Thread(target=self.export_worker, args=(job, cmd, temp_target, target, length), daemon=True).start()

    def export_worker(self, job, cmd, temp_target, target, length):
        errors = []
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1)
            if job == self.export_id:
                self.export_proc = proc
            for line in proc.stdout:
                line = line.strip()
                if line.startswith(("out_time_us=", "out_time_ms=")):
                    try:
                        fraction = min(1.0, max(0.0, int(line.split("=", 1)[1]) / 1_000_000 / length))
                        GLib.idle_add(self.export_progress, job, fraction)
                    except ValueError:
                        pass
                elif line and not line.startswith(("progress=", "frame=", "fps=", "stream_", "bitrate=", "total_size=", "out_time=", "dup_frames=", "drop_frames=", "speed=", "quality=")):
                    errors.append(line)
                    if len(errors) > 12:
                        errors.pop(0)
            code = proc.wait()
            if code or job != self.export_id:
                raise RuntimeError("\n".join(errors[-6:]) or f"FFmpeg zakończył się kodem {code}")
            temp_target.replace(target)
            GLib.idle_add(self.export_done, job, str(target))
        except Exception as exc:
            if job == self.export_id:
                GLib.idle_add(self.export_failed, job, str(exc))
        finally:
            try:
                temp_target.unlink(missing_ok=True)
            except OSError:
                pass
            if job == self.export_id:
                self.export_proc = None

    def export_progress(self, job, fraction):
        if job != self.export_id:
            return False
        self.progress.set_fraction(fraction)
        self.progress.set_text(f"Eksport {fraction * 100:.1f}%")
        self.status.set_text(f"Eksport: {fraction * 100:.1f}%")
        return False

    def export_done(self, job, target):
        if job != self.export_id:
            return False
        self.export_btn.set_sensitive(True)
        self.progress.set_fraction(1)
        self.progress.set_text("Eksport zakończony")
        self.status.set_text(f"Zapisano: {target}")
        return False

    def export_failed(self, job, error):
        if job != self.export_id:
            return False
        self.export_btn.set_sensitive(bool(self.media))
        self.progress.set_fraction(0)
        self.progress.set_text("Błąd eksportu")
        self.status.set_text("Błąd eksportu: " + error[-450:])
        return False

    def shutdown(self, *_):
        self.job_id += 1
        self.export_id += 1
        for proc in (self.download_proc, self.export_proc, self.mpv):
            self.stop_process(proc)
        if self.sock:
            self.sock.close()
        if hasattr(self, "mpv_log_file"):
            self.mpv_log_file.close()
        for proc in (self.download_proc, self.export_proc, self.mpv):
            if proc:
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if self.workdir:
            shutil.rmtree(self.workdir, ignore_errors=True)
        shutil.rmtree(self.ipc_dir, ignore_errors=True)
        Gtk.main_quit()
        return False

if __name__ == "__main__":
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
    lock = open(runtime / f"yt-cut-{os.getuid()}.lock", "w")
    socket_path = str(runtime / f"yt-cut-{os.getuid()}.sock")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(1)
                client.connect(socket_path)
                client.sendall(b"present")
        except OSError:
            pass
        raise SystemExit(0)
    try:
        Path(socket_path).unlink(missing_ok=True)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(socket_path)
        server.listen(3)
        server.setblocking(False)
        app = YTCut()

        def show_existing(fd, condition):
            try:
                client, _ = server.accept()
                with client:
                    if client.recv(64) == b"present":
                        app.present_with_time(Gdk.CURRENT_TIME)
            except OSError:
                pass
            return True

        GLib.io_add_watch(server.fileno(), GLib.IO_IN, show_existing)
        Gtk.main()
    finally:
        if "server" in locals():
            server.close()
        Path(socket_path).unlink(missing_ok=True)
