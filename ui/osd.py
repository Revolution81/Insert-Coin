"""The volume pop-up: a box in the middle of the screen for a moment after
Fn+D / Fn+S / Fn+A, like a console's volume bar."""
from __future__ import annotations

from PIL import Image, ImageDraw

from . import theme as t

SHOW_S = 1.5


def _speaker(d: ImageDraw.ImageDraw, x: int, y: int, muted: bool) -> None:
    col = t.PAL["dim"] if muted else t.PAL["white"]
    d.rectangle((x, y + 6, x + 5, y + 13), fill=col)
    d.polygon([(x + 5, y + 6), (x + 12, y), (x + 12, y + 19), (x + 5, y + 13)], fill=col)
    if muted:
        r = t.PAL["red"]
        d.line((x + 16, y + 5, x + 25, y + 14), fill=r, width=2)
        d.line((x + 16, y + 14, x + 25, y + 5), fill=r, width=2)
    else:
        d.arc((x + 8, y + 3, x + 20, y + 16), -50, 50, fill=col, width=2)
        d.arc((x + 10, y - 1, x + 26, y + 20), -50, 50, fill=col, width=2)


def draw(img: Image.Image, steps: int, muted: bool, total: int = 10) -> Image.Image:
    img = img.copy()
    d = ImageDraw.Draw(img)
    d.fontmode = "1"
    w, h = 184, 64
    x0, y0 = (t.W - w) // 2, (t.H - h) // 2
    d.rectangle((x0 + 3, y0 + 3, x0 + w + 3, y0 + h + 3), fill=(0, 0, 0))          # shadow
    d.rectangle((x0, y0, x0 + w, y0 + h), fill=(14, 22, 64), outline=t.PAL["white"], width=2)
    _speaker(d, x0 + 12, y0 + 10, muted)
    label = "SOUND OFF" if muted else f"VOLUME {steps}"
    t.text(d, (x0 + 46, y0 + 12), label, t.BODY, "dim" if muted else "white")
    bx, by, seg, gap = x0 + 12, y0 + 40, 14, 2
    for i in range(total):
        on = not muted and i < steps
        col = t.PAL["gold"] if on else (40, 52, 110)
        d.rectangle((bx + i * (seg + gap), by, bx + i * (seg + gap) + seg - 1, by + 12), fill=col)
    return img
