"""A secret screen. On the UNLOCK screen, type the Konami code with the
Cardputer's arrow keys: UP UP DOWN DOWN LEFT RIGHT LEFT RIGHT B A, then OK
(START). On the PIN screen the arrows type their letters, so the code
arrives as the text "ffxxzczcba". It's checked before the PIN is, costs no
try, opens nothing, and can't be chosen as a real PIN."""
from __future__ import annotations

from PIL import Image

from . import theme as t
from .input import Kind
from .widgets import Screen

KONAMI = "ffxxzczcba"


def is_konami(text: str) -> bool:
    return text.strip().lower() == KONAMI


# The heroes from the instruction booklet, 16x16 each: Sir Signer, Sats, Cardy.
_SPRITES = {
    "knight": ({"a": "#111111", "b": "#bdbdbd", "c": "#e0e0e0", "d": "#5fe0ff", "e": "#2f56d8",
                "f": "#7fa6ff", "g": "#f8c800", "h": "#c88a00"}, [
        ".....aaaaaa.....", "....abbbbbba....", "...abcbbbbbba...", "...abaaaaaaba...",
        "...abadaadaba...", "...abbbbbbbba...", "....aaaaaaaa....", "..aaeeeeeaaaaaa.",
        ".aeefeeeagggggga", ".aefeeeeaghhhhga", ".aeeeeeeaghgghga", "..aeeeeeaghhhhga",
        "..aeeeeeagghhgga", "..aeeaeea.agga..", "..abbabba..aa...", "..aaaaaaa......."]),
    "sats": ({"a": "#111111", "b": "#f8c800", "c": "#f4f4f4", "d": "#c88a00", "e": "#ff8fa8"}, [
        ".....aaaaaa.....", "...aabbbbbbaa...", "..abbccbbbbbba..", ".abccbbbbbbbbda.",
        ".abcbbabbbabbda.", "abbbbbabbbabbbda", "abbbbbabbbabbbda", "abbebbbbbbbbebda",
        "abbbbabbbbbabbda", "abbbbbaaaaabbbda", ".abbbbbbbbbbbda.", ".abbbbbbbbbbdda.",
        "..adbbbbbbbdda..", "...aaddddddaa...", "....aa....aa....", "...aaa....aaa..."]),
    "cardy": ({"a": "#111111", "b": "#f08a24", "c": "#1b2a6b", "d": "#7cf07a", "e": "#f4f4f4"}, [
        "................", ".aaaaaaaaaaaaaa.", ".abbbbbbbbbbbba.", ".abccccccccccba.",
        ".abccdccccdccba.", ".abccdccccdccba.", ".abccccccccccba.", ".abccdccccdccba.",
        ".abcccddddcccba.", ".abbbbbbbbbbbba.", ".abeaeaeaeaeaba.", ".abbbbbbbbbbbba.",
        ".abeaeaeaeaeaba.", ".aaaaaaaaaaaaaa.", "...aa......aa...", "..aaa......aaa.."]),
}


def sprite(name: str, scale: int) -> Image.Image:
    pal, rows = _SPRITES[name]
    img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    px = img.load()
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch != ".":
                c = pal[ch]
                px[x, y] = (int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16), 255)
    return img.resize((16 * scale, 16 * scale), Image.NEAREST)


class EggScreen(Screen):
    """ALL YOUR KEYS ARE BELONG TO YOU. Any key goes back to the PIN."""
    title = "???"
    animated = True

    def handle(self, ev, c):
        if ev.kind is not Kind.CURSOR:
            c.pop()

    def render(self, c, frame):
        W, H = t.W, t.H
        img = Image.new("RGB", (W, H), (0, 0, 0))
        d = t.ImageDraw.Draw(img)
        # an orange cut-scene panel with scanlines
        d.rectangle((14, 8, W - 15, 104), fill=(222, 96, 8))
        for y in range(12, 104, 8):
            d.line((14, y, W - 15, y), fill=(196, 74, 4))
        hop = (frame // 6) % 2                       # the heroes bob in turn
        for i, (name, x) in enumerate((("sats", 46), ("knight", 128), ("cardy", 210))):
            s = sprite(name, 4)
            img.paste(s, (x, 30 - (4 if (i + hop) % 2 else 0)), s)
        t.text(d, (22, 116), "SIR SIGNER:", t.BODY, "gold")
        t.text(d, (22, 132), "ALL YOUR KEYS ARE BELONG", t.BODY, "white")
        t.text(d, (22, 146), "TO YOU.", t.BODY, "white")
        if (frame // 15) % 2 == 0:
            t.text(d, (W - 22, 156), "PRESS ANY KEY", t.TINY, "grey", align="right")
        return img
