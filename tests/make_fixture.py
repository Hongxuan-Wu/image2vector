"""Build a synthetic figure that exercises the hard cases.

The fixture deliberately contains a compound shape with a real hole, a rounded
rectangle, a thin connector, a semi-transparent panel and live lettering, so the
acceptance checks cover contour holes, corner handling, stroke-like geometry and
text restoration in one image.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
]


def _font(size: int):
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default()


def build(output: str | Path, width: int = 1200, height: int = 800) -> dict:
    image = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image, "RGBA")

    # compound shape with a hole: a blue annulus
    draw.ellipse([90, 120, 390, 420], fill=(38, 96, 178, 255))
    draw.ellipse([170, 200, 310, 340], fill=(255, 255, 255, 255))

    # rounded panel, semi-transparent
    draw.rounded_rectangle([460, 140, 780, 400], radius=28, fill=(232, 160, 42, 210),
                           outline=(120, 74, 12, 255), width=4)

    # thin connector with an arrow head
    draw.line([(400, 260), (455, 260)], fill=(30, 30, 30, 255), width=5)
    draw.polygon([(455, 250), (475, 260), (455, 270)], fill=(30, 30, 30, 255))

    # small repeated motifs
    for index in range(3):
        left = 900 + index * 80
        draw.rectangle([left, 480, left + 50, 560], fill=(56, 142, 60, 255))

    # lettering
    font = _font(38)
    small = _font(28)
    draw.text((120, 470), "Sample A", font=font, fill=(17, 17, 17))
    draw.text((470, 440), "Treatment 24 h", font=small, fill=(17, 17, 17))
    draw.text((900, 600), "n = 3", font=small, fill=(17, 17, 17))

    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target)
    return {"image": str(target.resolve()), "width": width, "height": height}


def build_light_gray(output: str | Path) -> Path:
    """Both foreground and background fall above the black-and-white threshold."""
    image = Image.new("RGB", (256, 192), "white")
    ImageDraw.Draw(image).rectangle((32, 32, 223, 159), fill=(210, 210, 210))
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target)
    return target


def build_palette64(output: str | Path, width: int = 2048, height: int = 1536) -> Path:
    """Equal-area swatches exceed the old 3 MP cutoff and the photo color budget."""
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for index in range(64):
        row, column = divmod(index, 8)
        color = tuple(32 + ((index >> shift) & 3) * 64 for shift in (4, 2, 0))
        draw.rectangle((column * width // 8, row * height // 8,
                        (column + 1) * width // 8 - 1, (row + 1) * height // 8 - 1),
                       fill=color)
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target)
    return target


if __name__ == "__main__":
    destination = sys.argv[1] if len(sys.argv) > 1 else "fixture.png"
    print(build(destination))
