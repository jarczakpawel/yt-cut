def clamp_crop(x, y, width, height, source_width, source_height):
    source_width, source_height = int(source_width), int(source_height)
    if source_width < 2 or source_height < 2:
        raise ValueError("Video resolution is too small")
    x = max(0, min(int(round(x)), source_width - 2))
    y = max(0, min(int(round(y)), source_height - 2))
    width = max(2, min(source_width - x, int(round(width))))
    height = max(2, min(source_height - y, int(round(height))))
    width -= width % 2
    height -= height % 2
    return x, y, width, height


def drag_crop(anchor, current, source_width, source_height, aspect=None):
    ax, ay = anchor
    cx, cy = current
    x = max(0, min(int(round(min(ax, cx))), source_width - 2))
    y = max(0, min(int(round(min(ay, cy))), source_height - 2))
    width = abs(cx - ax)
    height = abs(cy - ay)
    if aspect:
        width = min(width, height * aspect)
        height = width / aspect
    return clamp_crop(x, y, width, height, source_width, source_height)


def fit_aspect(crop, source_width, source_height, aspect):
    if not aspect:
        return clamp_crop(*crop, source_width, source_height)
    x, y, w, h = crop
    width = min(w, h * aspect)
    height = width / aspect
    return clamp_crop(x + (w - width) / 2, y + (h - height) / 2,
                      width, height, source_width, source_height)


def ffmpeg_crop(crop):
    x, y, width, height = crop
    return f"scale=trunc(iw*sar/2)*2:ih,setsar=1,crop={width}:{height}:{x}:{y}:exact=1"
