import ctypes
import os
import queue
import threading
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

BASE = Path(getattr(__import__("sys"), "_MEIPASS", Path(__file__).resolve().parent))
DLL = BASE / "tools" / "libmpv-2.dll"
if not DLL.is_file():
    DLL = BASE / "libmpv-2.dll"


class VideoSurface(QWidget):
    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WA_NativeWindow, True)
        self.setStyleSheet("background:#101216")
        self.setMinimumHeight(280)


class MpvPlayer:
    PlayingState = 1
    PausedState = 2

    def __init__(self, widget):
        self.hwnd = int(widget.winId()) & 0xFFFFFFFF
        self.actions = queue.Queue()
        self._position = 0
        self._playing = False
        self.error = ""
        self.ready = False
        self._closed = False
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    def setSource(self, filename):
        self._position = 0
        self._playing = False
        self.actions.put(("load", str(filename) if filename else None))

    def setPosition(self, value):
        self._position = max(0, int(value))
        self.actions.put(("seek", self._position))

    def position(self):
        return self._position

    def play(self):
        self._playing = True
        self.actions.put(("pause", False))

    def pause(self):
        self._playing = False
        self.actions.put(("pause", True))

    def stop(self):
        self._playing = False
        self.actions.put(("stop",))

    def playbackState(self):
        return self.PlayingState if self._playing else self.PausedState

    def close(self):
        self._closed = True
        self.actions.put(("quit",))

    def _run(self):
        try:
            if not DLL.is_file():
                raise RuntimeError(f"Missing libmpv: {DLL}")
            if os.name == "nt":
                os.add_dll_directory(str(DLL.parent))
            lib = ctypes.CDLL(str(DLL))
            lib.mpv_create.restype = ctypes.c_void_p
            lib.mpv_set_option_string.argtypes = (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p)
            lib.mpv_set_option_string.restype = ctypes.c_int
            lib.mpv_initialize.argtypes = (ctypes.c_void_p,)
            lib.mpv_initialize.restype = ctypes.c_int
            lib.mpv_command.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_char_p))
            lib.mpv_command.restype = ctypes.c_int
            lib.mpv_get_property.argtypes = (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p)
            lib.mpv_get_property.restype = ctypes.c_int
            lib.mpv_terminate_destroy.argtypes = (ctypes.c_void_p,)
            handle = lib.mpv_create()
            if not handle:
                raise RuntimeError("mpv_create failed")
            try:
                options = {
                    "config": "no",
                    "wid": str(self.hwnd),
                    "idle": "yes",
                    "force-window": "yes",
                    "keep-open": "yes",
                    "pause": "yes",
                    "osc": "no",
                    "input-default-bindings": "no",
                    "terminal": "no",
                    "hwdec": "auto-safe",
                }
                for key, value in options.items():
                    if lib.mpv_set_option_string(handle, key.encode(), value.encode()) < 0:
                        raise RuntimeError(f"mpv option rejected: {key}")
                if lib.mpv_initialize(handle) < 0:
                    raise RuntimeError("mpv_initialize failed")
                self.ready = True

                def command(*values):
                    arguments = (ctypes.c_char_p * (len(values) + 1))(
                        *(str(value).encode("utf-8") for value in values), None)
                    return lib.mpv_command(handle, arguments)

                while not self._closed:
                    try:
                        action = self.actions.get(timeout=.05)
                        while True:
                            name, *params = action
                            if name == "quit":
                                self._closed = True
                                break
                            if name == "load":
                                if params[0]:
                                    command("loadfile", params[0], "replace")
                                    command("set", "pause", "yes")
                                else:
                                    command("stop")
                            elif name == "pause":
                                command("set", "pause", "yes" if params[0] else "no")
                            elif name == "stop":
                                command("stop")
                            elif name == "seek":
                                command("seek", f"{params[0] / 1000:.3f}", "absolute", "exact")
                            try:
                                action = self.actions.get_nowait()
                            except queue.Empty:
                                break
                    except queue.Empty:
                        pass
                    if self._playing:
                        position = ctypes.c_double()
                        if lib.mpv_get_property(handle, b"time-pos", 5, ctypes.byref(position)) >= 0:
                            self._position = max(0, round(position.value * 1000))
            finally:
                lib.mpv_terminate_destroy(handle)
        except Exception as exc:
            self.error = str(exc)
            self.ready = False

