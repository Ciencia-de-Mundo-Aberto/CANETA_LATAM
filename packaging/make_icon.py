"""Draw the CANETA icon (same design as web/static/caneta.svg) into a multi-size .ico."""

import sys

from PIL import Image, ImageDraw


def draw(size: int = 256) -> Image.Image:
    s = size / 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(14 * s), fill="#123c3a")
    pen = [(44.5, 9.5), (54.5, 19.5), (27, 47), (15, 51), (19, 39)]
    d.polygon([(x * s, y * s) for x, y in pen], fill="#f2c14e")
    d.polygon([(x * s, y * s) for x, y in [(19, 39), (27, 47), (15, 51)]], fill="#0b2422")
    d.line([(40.5 * s, 13.5 * s), (50.5 * s, 23.5 * s)], fill="#123c3a", width=max(1, int(3 * s)))
    d.arc([10 * s, 48 * s, 54 * s, 62 * s], start=200, end=340, fill="#7fd1b9", width=max(1, int(3 * s)))
    return img


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "caneta.ico"
    draw(256).save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(out)
