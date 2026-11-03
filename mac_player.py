from pathlib import Path

from PySide6.QtCore import QUrl, QRectF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtWidgets import QWidget


class VideoSurface(QWidget):
    def __init__(self):
        super().__init__()
        self.sink = QVideoSink(self)
        self.sink.videoFrameChanged.connect(self.update_frame)
        self.frame = QImage()
        self.setMinimumHeight(280)

    def update_frame(self, frame):
        image = frame.toImage()
        if not image.isNull():
            self.frame = image
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#101216"))
        if not self.frame.isNull():
            scale = min(self.width() / self.frame.width(),
                        self.height() / self.frame.height())
            width, height = self.frame.width() * scale, self.frame.height() * scale
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.drawImage(QRectF((self.width() - width) / 2,
                                     (self.height() - height) / 2, width, height), self.frame)
        painter.end()


class MpvPlayer:
    PlayingState = QMediaPlayer.PlayingState
    PausedState = QMediaPlayer.PausedState

    def __init__(self, widget):
        self.player = QMediaPlayer(widget)
        self.audio = QAudioOutput(widget)
        self.audio.setVolume(1.0)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoSink(widget.sink)
        self.player.errorOccurred.connect(self.on_error)
        self.ready = True
        self.error = ""
        self.state = {}
        self.log_path = Path.home() / "Library" / "Logs" / "YT-Cut.log"
        self.startup_log = self.log_path

    def on_error(self, error, message):
        self.error = message or self.player.errorString()

    def setSource(self, filename):
        self.error = ""
        self.player.setSource(QUrl.fromLocalFile(str(filename)) if filename else QUrl())

    def setPosition(self, value):
        self.player.setPosition(value)

    def position(self):
        return self.player.position()

    def play(self):
        self.player.play()

    def pause(self):
        self.player.pause()

    def stop(self):
        self.player.stop()

    def playbackState(self):
        return self.player.playbackState()

    def close(self):
        self.player.stop()
        self.player.setSource(QUrl())
