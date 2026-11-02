import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QComboBox,
                               QHBoxLayout, QLabel, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from crop_geometry import clamp_crop, drag_crop, fit_aspect


class CropCanvas(QWidget):
    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        self.mode = None
        self.anchor = None
        self.original = None
        self.setMinimumSize(380, 190)
        self.setMouseTracking(True)

    def scale_info(self):
        d = self.dialog
        scale = min(self.width() / d.width, self.height() / d.height)
        return ((self.width() - d.width * scale) / 2,
                (self.height() - d.height * scale) / 2, scale)

    def coordinates(self, position):
        ox, oy, scale = self.scale_info()
        return (max(0, min(self.dialog.width, (position.x()-ox)/scale)),
                max(0, min(self.dialog.height, (position.y()-oy)/scale)))

    def paintEvent(self, event):
        d = self.dialog
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor("#11141a"))
        ox, oy, scale = self.scale_info()
        p.drawPixmap(QRectF(ox, oy, d.width*scale, d.height*scale),
                     d.image, QRectF(0, 0, d.width, d.height))
        x, y, w, h = d.crop
        def rect(a,b,c,e):
            return QRectF(ox+a*scale, oy+b*scale, c*scale, e*scale)
        p.fillRect(rect(0, 0, d.width, y), QColor(0, 0, 0, 155))
        p.fillRect(rect(0, y+h, d.width, d.height-y-h), QColor(0, 0, 0, 155))
        p.fillRect(rect(0, y, x, h), QColor(0, 0, 0, 155))
        p.fillRect(rect(x+w, y, d.width-x-w, h), QColor(0, 0, 0, 155))
        p.setPen(QPen(QColor("#ff635c"), 2))
        p.setBrush(Qt.NoBrush)
        p.drawRect(rect(x,y,w,h))
        p.setPen(QPen(QColor(255, 255, 255, 150), 1))
        for n in (1, 2):
            p.drawLine(QPointF(ox+(x+w*n/3)*scale, oy+y*scale),
                       QPointF(ox+(x+w*n/3)*scale, oy+(y+h)*scale))
            p.drawLine(QPointF(ox+x*scale, oy+(y+h*n/3)*scale),
                       QPointF(ox+(x+w)*scale, oy+(y+h*n/3)*scale))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ff635c"))
        for cx in (x, x+w):
            for cy in (y, y+h):
                p.drawEllipse(QRectF(ox+cx*scale-7, oy+cy*scale-7, 14, 14))
        p.end()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        d = self.dialog
        px, py = self.coordinates(event.position())
        x, y, w, h = d.crop
        _, _, scale = self.scale_info()
        corners = ((x, y, (x+w,y+h)), (x+w, y, (x,y+h)),
                   (x,y+h,(x+w,y)), (x+w,y+h,(x,y)))
        close = next((corner for corner in corners
                      if math.hypot(px-corner[0], py-corner[1]) < 14/scale), None)
        if close:
            self.mode = "resize"
            self.anchor = close[2]
        elif x < px < x+w and y < py < y+h:
            self.mode = "move"
            self.anchor = (px,py)
            self.original = d.crop
        else:
            self.mode = "new"
            self.anchor = (px,py)

    def mouseMoveEvent(self, event):
        if not self.mode:
            return
        d = self.dialog
        px, py = self.coordinates(event.position())
        if self.mode == "move":
            x, y, w, h = self.original
            x = max(0, min(d.width-w, x+px-self.anchor[0]))
            y = max(0, min(d.height-h, y+py-self.anchor[1]))
            d.set_crop((round(x), round(y), w, h))
        else:
            d.set_crop(drag_crop(self.anchor, (px,py), d.width, d.height, d.aspect()))

    def mouseReleaseEvent(self, event):
        if self.mode:
            self.mouseMoveEvent(event)
            self.mode = None


class CropPanel(QWidget):
    def __init__(self, filename, previous, applied, cancelled):
        super().__init__()
        self.applied = applied
        self.cancelled = cancelled
        self.image = QPixmap(str(filename))
        if self.image.isNull():
            raise ValueError("Nie można odczytać klatki filmu")
        self.width = self.image.width()
        self.height = self.image.height()
        self.crop = clamp_crop(*(previous or (0, 0, self.width, self.height)),
                               self.width, self.height)
        self.editing = False
        main = QVBoxLayout(self)
        main.setContentsMargins(5, 5, 5, 5)
        main.setSpacing(4)
        self.canvas = CropCanvas(self)
        main.addWidget(self.canvas, 1)
        row = QHBoxLayout()
        main.addLayout(row)
        row.addWidget(QLabel("Proporcje"))
        self.ratios = QComboBox()
        for label, ratio in (("Dowolne", None), ("16:9", 16 / 9), ("9:16", 9 / 16),
                             ("1:1", 1), ("4:3", 4 / 3), ("3:4", 3 / 4)):
            self.ratios.addItem(label, ratio)
        row.addWidget(self.ratios)
        self.fields = []
        for label, maximum in (("X", self.width), ("Y", self.height),
                               ("Szer.", self.width), ("Wys.", self.height)):
            row.addWidget(QLabel(label))
            spin = QSpinBox()
            spin.setRange(0, maximum)
            spin.setMinimumWidth(65)
            spin.valueChanged.connect(self.field_changed)
            row.addWidget(spin)
            self.fields.append(spin)
        self.ratios.currentIndexChanged.connect(self.ratio_changed)
        buttons = QHBoxLayout()
        main.addLayout(buttons)
        self.info = QLabel()
        buttons.addWidget(self.info)
        buttons.addStretch()
        cancel = QPushButton("Anuluj")
        cancel.clicked.connect(cancelled)
        buttons.addWidget(cancel)
        reset = QPushButton("Resetuj")
        reset.clicked.connect(lambda: self.applied(None))
        buttons.addWidget(reset)
        save = QPushButton("Zastosuj kadr")
        save.clicked.connect(lambda: self.applied(
            None if self.crop == (0, 0, self.width, self.height) else self.crop))
        buttons.addWidget(save)
        self.set_crop(self.crop)

    def aspect(self):
        return self.ratios.currentData()

    def ratio_changed(self, *_):
        ratio = self.aspect()
        if ratio:
            self.set_crop(fit_aspect(self.crop, self.width, self.height, ratio))

    def field_changed(self, *_):
        if not self.editing:
            self.set_crop(tuple(field.value() for field in self.fields))

    def set_crop(self, crop):
        self.crop = clamp_crop(*crop, self.width, self.height)
        self.editing = True
        for field, value in zip(self.fields, self.crop):
            field.setValue(value)
        self.editing = False
        self.info.setText(f"{self.crop[2]} x {self.crop[3]} px")
        self.canvas.update()
