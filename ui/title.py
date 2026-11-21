"""
The INSERT COIN title screen, shown at boot.

The bitcoin coin stands in for the O of COIN and spins like an arcade coin;
PRESS OK blinks. OK plays the fanfare (ui/sound.py; SETUP › SOUND OFF silences it) and carries on to
the welcome screen or the PIN. The static layers are drawn once and cached,
so each animation frame is just a copy plus the coin.
"""
from __future__ import annotations

import os
import time

from PIL import Image, ImageDraw

from core.version import VERSION

from . import sound
from . import theme as t
from .input import Kind
from .widgets import Screen

W, H = t.W, t.H
NAME = "INSERT COIN"
LOGO = t._load("PressStart2P.ttf", 32)
BANDS = [(255, 240, 160), t.PAL["gold"], t.PAL["gold"], t.PAL["orange"], t.PAL["orange_dk"]]
SPIN = [32, 30, 24, 14, 4, 14, 24, 30, 32, 32, 32, 32, 32, 32, 32, 32]
SPIN_S = 0.08                       # seconds per spin frame
LINE2_Y = 58


def edition() -> str:
    """"spending" (app mode, from M5Stack's launcher), "savings" (the wallet
    image or a dedicated install), or "" (simulator, development)."""
    e = os.environ.get("CZ_EDITION", "").lower()
    if e in ("spending", "savings"):
        return e
    return "spending" if os.environ.get("CZ_APP_MODE") == "1" else ""


EDITION_LABEL = {"spending": ("SPENDING WALLET", "orange"),
                 "savings": ("SAVINGS WALLET", "green"),
                 "": ("BITCOIN ONLY", "dim")}


def logo_text(img, xy, s):
    """Big letters: long red drop shadow, thick dark outline, banded gold."""
    x, y = xy
    mask = Image.new("L", (W, H), 0)
    md = ImageDraw.Draw(mask)
    md.fontmode = "1"
    md.text((x, y + LOGO.dy), s, font=LOGO.face, fill=255)
    box = mask.getbbox()
    if not box:
        return
    px, mp = img.load(), mask.load()
    pts = [(xx, yy) for yy in range(box[1], box[3]) for xx in range(box[0], box[2]) if mp[xx, yy]]

    def stamp(dx, dy, color_at):
        for xx, yy in pts:
            X, Y = xx + dx, yy + dy
            if 0 <= X < W and 0 <= Y < H:
                px[X, Y] = color_at(xx, yy)

    for d in range(1, 5):
        stamp(d, d, lambda xx, yy: t.PAL["red_dk"])
    for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (-1, -1), (1, -1), (-1, 1), (1, 1),
                   (-2, -1), (-2, 1), (2, -1), (2, 1), (-1, -2), (1, -2), (-1, 2), (1, 2)):
        stamp(dx, dy, lambda xx, yy: t.PAL["shadow"])
    top, bot = box[1], box[3]

    def band(xx, yy):
        f = (yy - top) / max(1, bot - top - 1) * (len(BANDS) - 1)
        i = min(int(f), len(BANDS) - 2)
        frac = f - i
        use_next = frac > 0.75 or (frac > 0.4 and (xx + yy) % 2 == 0)
        return BANDS[i + 1] if use_next else BANDS[i]
    stamp(0, 0, band)


def _coin_images() -> dict[int, tuple[Image.Image, Image.Image]]:
    c = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    p = c.load()
    for j, row in enumerate(t.COIN):
        for i, ch in enumerate(row):
            if ch in t.COIN_COLORS:
                p[i, j] = t.COIN_COLORS[ch] + (255,)
    out = {}
    for w in set(SPIN):
        big = c.resize((max(2, w), 32), Image.NEAREST)
        sh = Image.new("RGBA", big.size, (0, 0, 0, 0))
        sh.paste(t.PAL["shadow"] + (255,), (0, 0) + big.size, big)
        out[w] = (big, sh)
    return out


def _sparkle(img, xy, size):
    px = img.load()
    x0, y0 = xy
    for k in range(-size, size + 1):
        for x, y in ((x0 + k, y0), (x0, y0 + k)):
            if 0 <= x < W and 0 <= y < H:
                px[x, y] = t.PAL["white"] if abs(k) < size else t.PAL["gold"]


_cache: dict = {}


def _layout():
    if "base" not in _cache:
        img, d = t.new_canvas()
        t.sky(img, d, top=0, seed=11)
        logo_text(img, ((W - LOGO.width("INSERT")) // 2, 16), "INSERT")
        cw = LOGO.width("C")
        x = (W - (cw + 32 + LOGO.width("IN"))) // 2
        logo_text(img, (x, LINE2_Y), "C")
        logo_text(img, (x + cw + 34, LINE2_Y), "IN")
        t.text(d, (W // 2, 126), "RUNNING BITCOIN...", t.TINY, "grey", align="center")
        label, color = EDITION_LABEL[edition()]
        t.text(d, (8, 158), label, t.TINY, color)
        t.text(d, (W - 8, 158), f"v{VERSION}", t.TINY, "dim", align="right")
        _cache.update(base=img, coin_x=x + cw + 2, coin_y=LINE2_Y - 2, coins=_coin_images())
    return _cache


def draw(frame: int, now: float | None = None) -> Image.Image:
    c = _layout()
    img = c["base"].copy()
    step = int((time.monotonic() if now is None else now) / SPIN_S) % len(SPIN)
    big, sh = c["coins"][SPIN[step]]
    cx = c["coin_x"] + 16 - big.width // 2
    img.paste(sh, (cx + 3, c["coin_y"] + 3), sh)
    img.paste(big, (cx, c["coin_y"]), big)
    if step in (9, 10, 11):                          # glint when it faces us
        _sparkle(img, (c["coin_x"] + 26, c["coin_y"] + 4), (3, 4, 2)[step - 9])
    if frame == 0:
        d = ImageDraw.Draw(img)
        d.fontmode = "1"
        t.text(d, (W // 2, 108), "PRESS OK", t.BODY, "white", align="center")
    return img


class TitleScreen(Screen):
    title = NAME
    animated = True
    quiet = True            # OK plays the fanfare, not a menu click

    def __init__(self, then):
        from .egg import KonamiWatch
        self.then = then              # then(app): what OK leads to
        self.done = False
        self.konami = KonamiWatch()
        self.secret = False

    def on_show(self, app):
        self.konami.reset()
        self.secret = False
        if app.settings.sound:          # make the sound now, so OK plays it at once
            sound.prepare("fanfare")

    def handle(self, ev, app):
        if ev.kind is Kind.SELECT and self.secret and not self.done:
            from .egg import EggScreen          # UP UP DOWN DOWN LEFT RIGHT LEFT RIGHT B A, START
            self.secret = False
            self.konami.reset()
            sound.play("secret", app.settings.sound)
            app.push(EggScreen())
            return
        if ev.kind is not Kind.SELECT:
            self.secret = self.konami.feed(ev)
            if self.secret and app.settings.sound:
                sound.prepare("secret")
            return
        if ev.kind is Kind.SELECT and not self.done:
            self.done = True
            sound.play("fanfare", app.settings.sound)
            self.then(app)

    def render(self, app, frame):
        return draw(frame)
