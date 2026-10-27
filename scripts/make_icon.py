"""Regenerate assets/tidy.png and assets/tidy.ico (run: python scripts/make_icon.py)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
SIZE = 1024


def draw() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Rounded tile with a vertical gradient (indigo -> violet)
    top, bottom = (124, 147, 255), (98, 92, 230)
    tile = Image.new("RGBA", (SIZE, SIZE))
    td = ImageDraw.Draw(tile)
    for y in range(SIZE):
        t = y / SIZE
        colour = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom, strict=True))
        td.line([(0, y), (SIZE, y)], fill=(*colour, 255))
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([40, 40, SIZE - 40, SIZE - 40], radius=220, fill=255)
    img.paste(tile, (0, 0), mask)
    # Three "sorted" bars of decreasing length, and a check mark
    white = (255, 255, 255, 255)
    for i, width in enumerate((560, 440, 290)):
        y = 300 + i * 150
        d.rounded_rectangle([232, y, 232 + width, y + 84], radius=42, fill=white)
    d.line([(660, 640), (745, 725), (860, 570)], fill=white, width=84, joint="curve")
    for x, y in ((660, 640), (745, 725), (860, 570)):
        d.ellipse([x - 42, y - 42, x + 42, y + 42], fill=white)
    return img


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    img = draw()
    img.resize((512, 512), Image.LANCZOS).save(ASSETS / "tidy.png")
    img.save(ASSETS / "tidy.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print("wrote", ASSETS / "tidy.png", ASSETS / "tidy.ico")


if __name__ == "__main__":
    main()
