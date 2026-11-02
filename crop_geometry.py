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


def crop_hit(crop, px, py, tolerance):
    x, y, w, h = crop
    corners = (
        ("nw", x, y), ("ne", x + w, y),
        ("sw", x, y + h), ("se", x + w, y + h),
    )
    for name, cx, cy in corners:
        if abs(px - cx) <= tolerance and abs(py - cy) <= tolerance:
            return name
    if x - tolerance <= px <= x + w + tolerance:
        if abs(py - y) <= tolerance:
            return "n"
        if abs(py - (y + h)) <= tolerance:
            return "s"
    if y - tolerance <= py <= y + h + tolerance:
        if abs(px - x) <= tolerance:
            return "w"
        if abs(px - (x + w)) <= tolerance:
            return "e"
    if x < px < x + w and y < py < y + h:
        return "move"
    return "new"


def drag_crop_edge(crop, edge, px, py, source_width, source_height, aspect=None):
    x, y, w, h = crop
    right, bottom = x + w, y + h
    if edge == "w":
        x = max(0, min(right - 2, round(px)))
        w = right - x
    elif edge == "e":
        w = max(2, min(source_width, round(px)) - x)
    elif edge == "n":
        y = max(0, min(bottom - 2, round(py)))
        h = bottom - y
    elif edge == "s":
        h = max(2, min(source_height, round(py)) - y)
    if aspect:
        if edge in ("w", "e"):
            center = y + h / 2
            h = min(w / aspect, source_height, 2 * center, 2 * (source_height - center))
            w = h * aspect
            x = right - w if edge == "w" else x
            y = center - h / 2
        else:
            center = x + w / 2
            w = min(h * aspect, source_width, 2 * center, 2 * (source_width - center))
            h = w / aspect
            x = center - w / 2
            y = bottom - h if edge == "n" else y
    return clamp_crop(x, y, w, h, source_width, source_height)
