import math

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gtk

from crop_geometry import clamp_crop, drag_crop, drag_crop_edge, crop_hit, fit_aspect


class CropCanvas(Gtk.DrawingArea):
    def __init__(self, dialog):
        super().__init__()
        self.dialog = dialog
        self.mode = None
        self.anchor = None
        self.original = None
        self.set_size_request(380, 190)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                        Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect("draw", self.draw_crop)
        self.connect("button-press-event", self.press)
        self.connect("motion-notify-event", self.motion)
        self.connect("button-release-event", self.release)

    def scale_info(self):
        d = self.dialog
        scale = min(self.get_allocated_width() / d.width, self.get_allocated_height() / d.height)
        return ((self.get_allocated_width() - d.width * scale) / 2,
                (self.get_allocated_height() - d.height * scale) / 2, scale)

    def coordinates(self, x, y):
        ox, oy, scale = self.scale_info()
        return (max(0, min(self.dialog.width, (x - ox) / scale)),
                max(0, min(self.dialog.height, (y - oy) / scale)))

    def draw_crop(self, widget, ctx):
        d = self.dialog
        ox, oy, scale = self.scale_info()
        ctx.set_source_rgb(.07, .08, .1)
        ctx.paint()
        ctx.save()
        ctx.translate(ox, oy)
        ctx.scale(scale, scale)
        Gdk.cairo_set_source_pixbuf(ctx, d.image, 0, 0)
        ctx.paint()
        x, y, w, h = d.crop
        ctx.set_source_rgba(0, 0, 0, .62)
        for rx, ry, rw, rh in ((0, 0, d.width, y), (0, y + h, d.width, d.height - y - h),
                               (0, y, x, h), (x + w, y, d.width - x - w, h)):
            ctx.rectangle(rx, ry, rw, rh)
        ctx.fill()
        ctx.set_source_rgba(1, .35, .32, 1)
        ctx.set_line_width(2 / scale)
        ctx.rectangle(x, y, w, h)
        ctx.stroke()
        ctx.set_source_rgba(1, 1, 1, .6)
        ctx.set_line_width(1 / scale)
        for col in (1, 2):
            ctx.move_to(x + w * col / 3, y)
            ctx.line_to(x + w * col / 3, y + h)
            ctx.move_to(x, y + h * col / 3)
            ctx.line_to(x + w, y + h * col / 3)
        ctx.stroke()
        ctx.set_source_rgb(1, .35, .32)
        handle = 6 / scale
        for cx in (x, x + w):
            for cy in (y, y + h):
                ctx.arc(cx, cy, handle, 0, 2 * math.pi)
        ctx.fill()
        ctx.restore()
        return False

    def interaction(self, x, y):
        px, py = self.coordinates(x, y)
        _, _, scale = self.scale_info()
        hit = crop_hit(self.dialog.crop, px, py, 10 / scale)
        if hit == "move" and self.dialog.crop == (0, 0, self.dialog.width, self.dialog.height):
            return "new"
        return hit

    def update_cursor(self, x, y):
        window = self.get_window()
        if not window:
            return
        name = {
            "nw": "nwse-resize", "se": "nwse-resize",
            "ne": "nesw-resize", "sw": "nesw-resize",
            "w": "ew-resize", "e": "ew-resize",
            "n": "ns-resize", "s": "ns-resize",
            "move": "move", "new": "crosshair",
        }[self.interaction(x, y)]
        window.set_cursor(Gdk.Cursor.new_from_name(window.get_display(), name))

    def press(self, widget, event):
        if event.button != 1:
            return False
        d = self.dialog
        px, py = self.coordinates(event.x, event.y)
        self.mode = self.interaction(event.x, event.y)
        x, y, w, h = d.crop
        opposite = {
            "nw": (x + w, y + h), "ne": (x, y + h),
            "sw": (x + w, y), "se": (x, y),
        }
        self.original = d.crop
        self.anchor = opposite.get(self.mode, (px, py))
        return True

    def motion(self, widget, event):
        if not self.mode:
            self.update_cursor(event.x, event.y)
            return False
        d = self.dialog
        px, py = self.coordinates(event.x, event.y)
        if self.mode == "move":
            x, y, w, h = self.original
            x = max(0, min(d.width - w, x + px - self.anchor[0]))
            y = max(0, min(d.height - h, y + py - self.anchor[1]))
            d.set_crop((round(x), round(y), w, h))
        elif self.mode in ("w", "e", "n", "s"):
            d.set_crop(drag_crop_edge(self.original, self.mode, px, py,
                                      d.width, d.height, d.aspect()))
        else:
            d.set_crop(drag_crop(self.anchor, (px, py), d.width, d.height, d.aspect()))
        return True

    def release(self, widget, event):
        if self.mode:
            self.motion(widget, event)
            self.mode = None
            self.update_cursor(event.x, event.y)
        return True


class CropPanel(Gtk.Box):
    def __init__(self, filename, previous, applied, cancelled):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.applied = applied
        self.cancelled = cancelled
        self.image = GdkPixbuf.Pixbuf.new_from_file(str(filename))
        self.width = self.image.get_width()
        self.height = self.image.get_height()
        self.crop = clamp_crop(*(previous or (0, 0, self.width, self.height)),
                               self.width, self.height)
        self.editing = False
        self.canvas = CropCanvas(self)
        self.pack_start(self.canvas, True, True, 0)
        controls = Gtk.Box(spacing=6)
        self.pack_start(controls, False, False, 2)
        controls.pack_start(Gtk.Label(label="Proporcje"), False, False, 0)
        self.ratios = Gtk.ComboBoxText()
        for key in ("Dowolne", "16:9", "9:16", "1:1", "4:3", "3:4"):
            self.ratios.append(key, key)
        self.ratios.set_active_id("Dowolne")
        self.ratios.connect("changed", self.ratio_changed)
        controls.pack_start(self.ratios, False, False, 0)
        self.fields = []
        for label, maximum in (("X", self.width), ("Y", self.height),
                               ("Szer.", self.width), ("Wys.", self.height)):
            controls.pack_start(Gtk.Label(label=label), False, False, 0)
            spin = Gtk.SpinButton.new_with_range(0, maximum, 1)
            spin.set_numeric(True)
            spin.set_width_chars(4)
            spin.connect("value-changed", self.field_changed)
            controls.pack_start(spin, False, False, 0)
            self.fields.append(spin)
        buttons = Gtk.Box(spacing=8)
        self.pack_start(buttons, False, False, 0)
        self.info = Gtk.Label(label="")
        buttons.pack_start(self.info, True, True, 0)
        cancel = Gtk.Button(label="Anuluj")
        cancel.connect("clicked", lambda *_: self.cancelled())
        buttons.pack_start(cancel, False, False, 0)
        reset = Gtk.Button(label="Resetuj")
        reset.connect("clicked", lambda *_: self.applied(None))
        buttons.pack_start(reset, False, False, 0)
        save = Gtk.Button(label="Zastosuj kadr")
        save.connect("clicked", lambda *_: self.applied(
            None if self.crop == (0, 0, self.width, self.height) else self.crop))
        buttons.pack_start(save, False, False, 0)
        self.set_crop(self.crop)

    def aspect(self):
        key = self.ratios.get_active_id()
        if key == "Dowolne":
            return None
        a, b = key.split(":")
        return int(a) / int(b)

    def ratio_changed(self, *_):
        ratio = self.aspect()
        if ratio:
            self.set_crop(fit_aspect(self.crop, self.width, self.height, ratio))

    def field_changed(self, *_):
        if not self.editing:
            self.set_crop([field.get_value_as_int() for field in self.fields])

    def set_crop(self, crop):
        self.crop = clamp_crop(*crop, self.width, self.height)
        self.editing = True
        for field, value in zip(self.fields, self.crop):
            field.set_value(value)
        self.editing = False
        self.info.set_text(f"{self.crop[2]} x {self.crop[3]} px")
        self.canvas.queue_draw()
