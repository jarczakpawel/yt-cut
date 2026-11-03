from PIL import Image, ImageDraw
from pathlib import Path

size = 256
image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((12, 12, 244, 244), radius=56, fill="#1b202c")
draw.rounded_rectangle((30, 50, 225, 205), radius=34, fill="#ec4f4b")
draw.polygon(((105, 87), (173, 127), (105, 168)), fill="white")
draw.line((47, 187, 215, 65), fill="white", width=15, joint="curve")
draw.ellipse((49, 182, 88, 221), outline="white", width=10)
draw.ellipse((192, 183, 232, 223), outline="white", width=10)
Path("assets").mkdir(exist_ok=True)
image.save("assets/yt-cut.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

image.save("assets/yt-cut.png")
