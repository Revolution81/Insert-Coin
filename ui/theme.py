"""
16-bit RPG theme for Insert Coin.

Everything draws onto a native 320x170 PIL image (the ST7789v3 panel size),
with antialiasing off so every pixel lands on the grid. Scale previews up with
NEAREST; never draw at a scaled size.

Fonts (SIL Open Font License, see fonts/LICENSE-*.txt):
  BODY  Press Start 2P 8px   menus, labels, most text
  BIG   Press Start 2P 16px  balances, titles
  TINY  Silkscreen 8px       status bar, hints, captions
"""
from __future__ import annotations

import os
import random
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

W, H = 320, 170
FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")

# --------------------------------------------------------------------------
# Palette: a SNES-era RPG look. Night-sky blues, classic blue menu windows,
# gold for money, Bitcoin orange for the coin.
# --------------------------------------------------------------------------
PAL = {
    "ink":        (8, 8, 26),
    "shadow":     (0, 0, 16),
    "white":      (248, 248, 248),
    "grey":       (152, 152, 184),
    "dim":        (88, 92, 132),
    "light":      (232, 232, 248),
    "gold":       (248, 200, 48),
    "gold_dk":    (176, 120, 16),
    "orange":     (247, 147, 26),
    "orange_lt":  (255, 200, 96),
    "orange_dk":  (184, 92, 0),
    "brown":      (88, 40, 0),
    "green":      (88, 216, 88),
    "green_dk":   (24, 112, 40),
    "red":        (232, 64, 64),
    "red_dk":     (120, 16, 32),
    "pink":       (255, 168, 176),
    "cyan":       (88, 200, 248),
    "cyan_dk":    (16, 96, 152),
    "purple":     (152, 96, 232),
    "purple_dk":  (64, 32, 128),
    "win_top":    (56, 88, 208),
    "win_bot":    (16, 28, 104),
    "bar_bg":     (24, 20, 48),
}

DEFAULT_BOX = PAL["win_top"]
_DEFAULT_BOT = PAL["win_bot"]
MAX_BOX_LUMA = 150          # brighter than this and white text gets hard to read


def set_box_color(rgb=None):
    """Color of every box (window) on the app's screens: rgb is the top of
    the gradient, the bottom is a deep shade of it. None = classic blue.
    Too-bright colors are scaled down so white text stays readable."""
    if rgb is None or tuple(rgb) == DEFAULT_BOX:
        PAL["win_top"], PAL["win_bot"] = DEFAULT_BOX, _DEFAULT_BOT
        return
    r, g, b = (max(0, min(255, int(c))) for c in rgb)
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    if luma > MAX_BOX_LUMA:
        k = MAX_BOX_LUMA / luma
        r, g, b = int(r * k), int(g * k), int(b * k)
    PAL["win_top"] = (r, g, b)
    PAL["win_bot"] = (int(r * 0.32) + 4, int(g * 0.32) + 4, int(b * 0.42) + 8)


class classic_boxes:
    """with classic_boxes(): draw with the classic blue (Sir Signer's screens),
    then put the app's chosen color back."""
    def __enter__(self):
        self.saved = PAL["win_top"], PAL["win_bot"]
        PAL["win_top"], PAL["win_bot"] = DEFAULT_BOX, _DEFAULT_BOT

    def __exit__(self, *exc):
        PAL["win_top"], PAL["win_bot"] = self.saved


SKY = [(14, 12, 38), (24, 20, 66), (40, 30, 92), (64, 38, 112), (96, 52, 120)]

# Mode badge colours: (fill, text)
MODES = {
    "VAULT":  (PAL["purple"], PAL["white"]),
    "ONLINE": (PAL["cyan"], PAL["ink"]),
    "WATCH":  (PAL["green"], PAL["ink"]),
    "SIGNER": (PAL["gold"], PAL["ink"]),
}

# The top-left corner of every frame belongs to the signer (see ui/console.py):
# it draws a gold shield there while it has the screen and blanks it otherwise,
# over whatever the app drew. The status bar starts to the right of it.
STAMP_W, STAMP_H = 14, 12


# --------------------------------------------------------------------------
# Fonts
# --------------------------------------------------------------------------
@dataclass
class Font:
    face: ImageFont.FreeTypeFont
    dy: int        # shift so that y = top of capital letters
    cap: int       # capital letter height in pixels

    def width(self, s: str) -> int:
        return int(self.face.getlength(s))


def _load(name: str, size: int) -> Font:
    face = ImageFont.truetype(os.path.join(FONT_DIR, name), size)
    top = face.getbbox("A")[1]
    bot = face.getbbox("A")[3]
    return Font(face, -top, bot - top)


BODY = _load("PressStart2P.ttf", 8)
BIG = _load("PressStart2P.ttf", 16)
TINY = _load("Silkscreen.ttf", 8)
WORD = _load("Tiny5.ttf", 16)    # seed words: bigger than BODY, narrower than BIG


def new_canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), PAL["ink"])
    d = ImageDraw.Draw(img)
    d.fontmode = "1"  # no antialiasing: crisp pixels
    return img, d


def text(d, xy, s, font=BODY, color="white", shadow=True, align="left"):
    """Text with a 1px drop shadow, the classic 16-bit look."""
    x, y = xy
    if align == "center":
        x -= font.width(s) // 2
    elif align == "right":
        x -= font.width(s)
    fill = PAL.get(color, color) if isinstance(color, str) else color
    y += font.dy
    if shadow:
        d.text((x + 1, y + 1), s, font=font.face, fill=PAL["shadow"])
    d.text((x, y), s, font=font.face, fill=fill)
    return x + font.width(s)


# --------------------------------------------------------------------------
# Sprites: tiny pixel-art bitmaps. '.' is transparent; other characters map
# to palette entries through the dict passed in.
# --------------------------------------------------------------------------
CURSOR = [
    "kk.....",
    "kwk....",
    "kwwk...",
    "kwwwk..",
    "kwwk...",
    "kwk....",
    "kk.....",
]
HEART = [
    ".kk.kk.",
    "krpkrrk",
    "krrrrrk",
    "krrrrrk",
    ".krrrk.",
    "..krk..",
    "...k...",
]
GEM = [
    "...kk...",
    "..kwck..",
    ".kwccck.",
    "kwccccdk",
    "kcccccdk",
    ".kcccdk.",
    "..kcdk..",
    "...kk...",
]
LOCK = [
    "..kkkk..",
    ".kwkkwk.",
    ".kw..wk.",
    "kkkkkkkk",
    "kyyykyyk",
    "kyyk.kyk",
    "kyyykyyk",
    "kkkkkkkk",
]
WIFI = [
    ".wwwwww.",
    "w......w",
    "..wwww..",
    ".w....w.",
    "...ww...",
    "........",
    "...ww...",
    "...ww...",
]
SHIELD = [
    "kkkkkkk",
    "kwwwwwk",
    "kwwkwwk",
    "kwkkkwk",
    "kwwkwwk",
    ".kwwwk.",
    "..kwk..",
    "...k...",
]
FINGER = [
    "..wwww..",
    ".w....w.",
    "w..ww..w",
    "w.w..w.w",
    "w.w.w..w",
    "w.w.w.w.",
    ".w..w.w.",
    "...w..w.",
]
NFC = [
    "w.......",
    ".w..w...",
    "..w..w..",
    "..w..w.w",
    "..w..w.w",
    "..w..w..",
    ".w..w...",
    "w.......",
]
DOWN_ARROW = [
    "kkkkkkk",
    "kwwwwwk",
    ".kwwwk.",
    "..kwk..",
    "...k...",
]

SPRITE_COLORS = {
    "k": PAL["ink"], "w": PAL["white"], "r": PAL["red"], "p": PAL["pink"],
    "c": PAL["cyan"], "d": PAL["cyan_dk"], "y": PAL["gold"],
}


def sprite(img, rows, xy, colors=None, scale=1):
    colors = {**SPRITE_COLORS, **(colors or {})}
    px = img.load()
    x0, y0 = xy
    for j, row in enumerate(rows):
        for i, ch in enumerate(row):
            if ch == "." or ch not in colors:
                continue
            for sy in range(scale):
                for sx in range(scale):
                    x, y = x0 + i * scale + sx, y0 + j * scale + sy
                    if 0 <= x < W and 0 <= y < H:
                        px[x, y] = colors[ch]


def _bitcoin_coin_rows() -> list[str]:
    """16x16 shaded gold coin with a pixel B and its two ticks."""
    glyph = [
        ".w.w...",
        "wwwww..",
        ".w...w.",
        ".w...w.",
        ".wwww..",
        ".w...w.",
        ".w...w.",
        "wwwww..",
        ".w.w...",
    ]
    rows = []
    for y in range(16):
        row = ""
        for x in range(16):
            dx, dy = x - 7.5, y - 7.5
            r2 = dx * dx + dy * dy
            if r2 > 60:
                row += "."
            elif r2 > 44:
                row += "k"
            elif r2 > 30 and (dx + dy) < 0:
                row += "l"          # rim highlight, upper left
            elif r2 > 30:
                row += "n"          # rim shade, lower right
            else:
                row += "o"
        rows.append(row)
    rows = [list(r) for r in rows]
    gx, gy = 5, 4
    for j, g in enumerate(glyph):
        for i, ch in enumerate(g):
            if ch == "w":
                rows[gy + j][gx + i] = "w"
                if rows[gy + j + 1][gx + i + 1] == "o":
                    rows[gy + j + 1][gx + i + 1] = "n"   # engraved shadow
    return ["".join(r) for r in rows]


COIN = _bitcoin_coin_rows()
COIN_COLORS = {"o": PAL["orange"], "l": PAL["orange_lt"], "n": PAL["orange_dk"],
               "k": PAL["brown"], "w": PAL["white"]}


def coin(img, xy, scale=2):
    sprite(img, COIN, xy, COIN_COLORS, scale)


# --------------------------------------------------------------------------
# Backgrounds
# --------------------------------------------------------------------------
def sky(img, d, top=12, stars=True, hills=True, seed=7):
    """Banded night sky with checkerboard dithering between bands."""
    px = img.load()
    span = H - top
    n = len(SKY)
    for y in range(top, H):
        t = (y - top) / span * (n - 1)
        i = min(int(t), n - 2)
        frac = t - i
        for x in range(W):
            use_next = frac > 0.8 or (frac > 0.6 and (x + y) % 2 == 0)
            px[x, y] = SKY[i + 1] if use_next else SKY[i]
    rnd = random.Random(seed)
    if stars:
        for _ in range(40):
            x, y = rnd.randrange(W), rnd.randrange(top + 2, top + span // 2)
            px[x, y] = PAL["white"] if rnd.random() < 0.3 else PAL["grey"]
    if hills:
        h = 0.0
        for x in range(W):
            h += rnd.uniform(-0.9, 0.9)
            h = max(-6, min(6, h))
            ytop = int(H - 18 + h)
            for y in range(ytop, H):
                px[x, y] = (20, 16, 44) if y > ytop else (44, 34, 84)


# --------------------------------------------------------------------------
# Windows, menus and bars
# --------------------------------------------------------------------------
def _rounded(d, x0, y0, x1, y1, color, r):
    if r <= 0:
        d.rectangle((x0, y0, x1, y1), fill=color)
        return
    d.rectangle((x0 + r, y0, x1 - r, y1), fill=color)
    d.rectangle((x0, y0 + r, x1, y1 - r), fill=color)
    if r >= 2:
        d.rectangle((x0 + 1, y0 + 1, x1 - 1, y1 - 1), fill=color)


def window(d, box, title=None, border="light", fill=None):
    """Classic RPG menu window: dark outline, bright bevel, blue gradient.

    border: "light" (normal), "gold" (selected), "red" (danger), "cyan",
    "orange" (caution).
    """
    x0, y0, x1, y1 = box
    edge = {"light": (PAL["light"], PAL["grey"]),
            "gold": (PAL["gold"], PAL["gold_dk"]),
            "red": (PAL["red"], PAL["red_dk"]),
            "cyan": (PAL["cyan"], PAL["cyan_dk"]),
            "orange": (PAL["orange"], PAL["orange_dk"])}[border]
    _rounded(d, x0, y0, x1, y1, PAL["ink"], 3)
    _rounded(d, x0 + 1, y0 + 1, x1 - 1, y1 - 1, edge[0], 2)
    _rounded(d, x0 + 2, y0 + 2, x1 - 2, y1 - 2, edge[1], 1)
    top, bot = fill or (PAL["win_top"], PAL["win_bot"])
    iy0, iy1 = y0 + 3, y1 - 3
    rows = max(1, iy1 - iy0)
    for y in range(iy0, iy1 + 1):
        t = round((y - iy0) / rows * 7) / 7   # 8 visible bands
        c = tuple(int(top[k] + (bot[k] - top[k]) * t) for k in range(3))
        d.line((x0 + 3, y, x1 - 3, y), fill=c)
    if title:
        tw = BODY.width(title) + 8
        tx = x0 + 8
        _rounded(d, tx, y0 - 5, tx + tw, y0 + 6, PAL["ink"], 2)
        d.rectangle((tx + 1, y0 - 4, tx + tw - 1, y0 + 5), fill=edge[1])
        text(d, (tx + 4, y0 - 3), title, BODY, "white", shadow=True)


def cursor(img, xy, frame=0):
    """Pointer that bobs one pixel every other frame."""
    x, y = xy
    sprite(img, CURSOR, (x + (frame % 2), y))


def menu(img, d, box, items, selected=0, cols=1, title=None, frame=0,
         border="light", row_h=14):
    window(d, box, title, border)
    x0, y0, x1, _ = box
    col_w = (x1 - x0 - 16) // cols
    for n, label in enumerate(items):
        cx = x0 + 18 + (n % cols) * col_w
        cy = y0 + 10 + (n // cols) * row_h
        color = "white" if n == selected else "grey"
        text(d, (cx, cy), label, BODY, color)
        if n == selected:
            cursor(img, (cx - 11, cy), frame)


def bar(d, box, frac, color="green", segments=True):
    """HP-style bar: outline, dark track, fill with a highlight row."""
    x0, y0, x1, y1 = box
    d.rectangle(box, fill=PAL["ink"])
    d.rectangle((x0 + 1, y0 + 1, x1 - 1, y1 - 1), fill=PAL["bar_bg"])
    fill_c = PAL[color]
    dark = PAL.get(color + "_dk", PAL["dim"])
    fx = x0 + 1 + int((x1 - x0 - 2) * max(0.0, min(1.0, frac)))
    if fx > x0 + 1:
        d.rectangle((x0 + 1, y0 + 1, fx - 1, y1 - 1), fill=fill_c)
        d.line((x0 + 1, y1 - 1, fx - 1, y1 - 1), fill=dark)
        d.line((x0 + 1, y0 + 1, fx - 1, y0 + 1), fill=PAL["white"])
        if segments:
            for sx in range(x0 + 6, fx - 1, 6):
                d.line((sx, y0 + 2, sx, y1 - 2), fill=dark)


# A lightning bolt drawn over the battery while it's charging
BOLT = [(9, 0), (10, 0), (8, 1), (9, 1), (7, 2), (8, 2), (9, 2), (10, 2), (11, 2),
        (8, 3), (9, 3), (7, 4), (8, 4), (6, 5), (7, 5), (6, 6)]


def battery(d, xy, frac, charging=False):
    x, y = xy
    d.rectangle((x, y, x + 15, y + 6), fill=PAL["ink"])
    d.rectangle((x + 1, y + 1, x + 14, y + 5), fill=PAL["light"])
    d.rectangle((x + 2, y + 2, x + 13, y + 4), fill=PAL["ink"])
    d.rectangle((x + 16, y + 2, x + 17, y + 4), fill=PAL["light"])
    color = PAL["green"] if frac > 0.3 or charging else PAL["red"]
    for s in range(4):
        if frac > s / 4 + 0.05:
            d.rectangle((x + 2 + s * 3, y + 2, x + 3 + s * 3, y + 4), fill=color)
    if charging:
        for bx, by in BOLT:                     # ink outline, then the gold bolt
            for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                if (bx + ox, by + oy) not in BOLT:
                    d.point((x + bx + ox, y + by + oy), fill=PAL["ink"])
        for bx, by in BOLT:
            d.point((x + bx, y + by), fill=PAL["gold"])


def status_bar(img, d, mode="VAULT", battery_frac=0.8, clock="14:43", online=False,
               typing=None, icons=(), charging=False, own_server=False):
    """typing: None in menu mode, or the translator's indicators dict
    ({"shift": bool, "fn": bool, "sym": bool}) in text mode, which shows an
    ABC badge plus whichever modifiers are active."""
    d.rectangle((0, 0, W - 1, 11), fill=PAL["ink"])
    if typing is not None:
        x = 140
        _rounded(d, x, 1, x + 30, 10, PAL["gold"], 2)
        label = "ABC" if typing.get("shift") else "abc"
        if typing.get("sym"):
            label = "#+="
        text(d, (x + 4, 2), label, BODY, PAL["ink"], shadow=False)
        if typing.get("fn"):
            _rounded(d, x + 34, 1, x + 56, 10, PAL["orange"], 2)
            text(d, (x + 37, 2), "FN", BODY, PAL["ink"], shadow=False)
    d.line((0, 11, W - 1, 11), fill=PAL["dim"])
    fill_c, text_c = MODES[mode]
    bw = BODY.width(mode) + 8
    x0 = STAMP_W + 2
    _rounded(d, x0, 1, x0 + bw, 10, fill_c, 2)
    text(d, (x0 + 4, 2), mode, BODY, text_c, shadow=False)
    ix = x0 + 6 + bw
    if mode == "SIGNER":
        sprite(img, SHIELD, (ix, 2), {"k": PAL["gold_dk"], "w": PAL["gold"]})
    elif mode == "VAULT":
        sprite(img, LOCK, (ix, 2), {"k": PAL["purple_dk"], "w": PAL["white"],
                                    "y": PAL["purple"]})
    else:
        # connected: green = your own server, blue = the public default
        col = (PAL["green"] if own_server else PAL["cyan"]) if online else PAL["dim"]
        sprite(img, WIFI, (ix, 2), {"w": col})
        if not online:
            d.line((ix, 9, ix + 7, 2), fill=PAL["red"])
    clock_x = W - 26
    text(d, (clock_x, 3), clock, TINY, "grey", shadow=False, align="right")
    battery(d, (W - 21, 2), battery_frac, charging)
    # add-on icons (secure chip, fingerprint) left of the clock
    x = clock_x - TINY.width(clock) - 12
    for rows, color in icons:
        col = PAL.get(color, color) if isinstance(color, str) else color
        sprite(img, rows, (x, 2), {"w": col, "k": PAL["ink"]})
        x -= 11


def stamp(img, secure: bool):
    """The signer's corner mark. Only the signer process calls this, on the
    final frame, so nothing the app draws can survive there."""
    d = ImageDraw.Draw(img)
    if secure:
        d.rectangle((0, 0, STAMP_W - 1, STAMP_H - 1), fill=PAL["gold"])
        sprite(img, SHIELD, (3, 2), {"k": PAL["ink"], "w": PAL["white"]})
    else:
        d.rectangle((0, 0, STAMP_W - 1, STAMP_H - 1), fill=PAL["ink"])


def dialog(img, d, lines, box=None, frame=0, more=False):
    """Bottom text box, like an RPG conversation."""
    box = box or (4, H - 40, W - 5, H - 4)
    window(d, box)
    x0, y0, x1, y1 = box
    for i, ln in enumerate(lines):
        text(d, (x0 + 10, y0 + 9 + i * 12), ln, BODY)
    if more and frame % 2 == 0:
        sprite(img, DOWN_ARROW, (x1 - 14, y1 - 10))


def text_field(d, box, value, pos, frame=0, masked=False, peek=False):
    """Single-line input: dark inset well, 8px monospace text, block cursor.

    masked: show dots instead of characters. peek: with masked, reveal the
    last character typed (helps on a tiny keyboard without exposing the rest).
    Scrolls sideways so the cursor is always visible.
    """
    x0, y0, x1, y1 = box
    d.rectangle(box, fill=PAL["ink"])
    d.rectangle((x0 + 1, y0 + 1, x1 - 1, y1 - 1), fill=PAL["dim"])
    d.rectangle((x0 + 2, y0 + 2, x1 - 1, y1 - 1), fill=PAL["bar_bg"])
    shown = value
    if masked:
        shown = "*" * len(value)
        if peek and value and pos == len(value):
            shown = shown[:-1] + value[-1]
    cols = (x1 - x0 - 10) // 8
    start = max(0, pos - cols + 1)
    visible = shown[start:start + cols]
    tx, ty = x0 + 5, y0 + (y1 - y0 - 7) // 2
    text(d, (tx, ty), visible, BODY, "white", shadow=False)
    if frame % 2 == 0:
        cx = tx + (pos - start) * 8
        d.rectangle((cx, ty - 1, cx + 7, ty + 8), fill=PAL["gold"])
        if pos < len(shown):
            text(d, (cx, ty), shown[pos], BODY, PAL["ink"], shadow=False)
    if start > 0:
        d.rectangle((x0 + 2, y0 + 2, x0 + 3, y1 - 1), fill=PAL["gold_dk"])


def list_view(img, d, box, rows, selected, frame=0, title=None, row_h=14,
              empty="Nothing here yet"):
    """Scrolling list in a window. rows: [(left, right, color)] where color
    applies to the right-hand text. Draws a scroll bar when it overflows."""
    window(d, box, title)
    x0, y0, x1, y1 = box
    top_pad = 12 if title else 8
    visible = max(1, (y1 - y0 - top_pad - 6) // row_h)
    if not rows:
        text(d, ((x0 + x1) // 2, y0 + top_pad + 8), empty, BODY, "grey", align="center")
        return
    first = min(max(0, selected - visible + 1), max(0, len(rows) - visible))
    first = min(first, selected)
    for n in range(first, min(len(rows), first + visible)):
        left, right, color = rows[n]
        y = y0 + top_pad + (n - first) * row_h
        sel = n == selected
        if sel:
            d.rectangle((x0 + 4, y - 3, x1 - 8, y + 9), fill=PAL["win_bot"])
            cursor(img, (x0 + 6, y), frame)
        text(d, (x0 + 18, y), left, BODY, "white" if sel else "light")
        if right:
            text(d, (x1 - 14, y), right, BODY, color or "grey", align="right")
    if len(rows) > visible:
        track = (x1 - 6, y0 + 6, x1 - 5, y1 - 6)
        d.rectangle(track, fill=PAL["ink"])
        span = track[3] - track[1]
        h = max(6, span * visible // len(rows))
        top = track[1] + (span - h) * first // max(1, len(rows) - visible)
        d.rectangle((track[0], top, track[2], top + h), fill=PAL["gold"])


def hearts(img, xy, total, left):
    x, y = xy
    for i in range(total):
        colors = None if i < left else {"r": PAL["dim"], "p": PAL["grey"]}
        sprite(img, HEART, (x + i * 9, y), colors)


def gems(img, xy, total, filled, scale=2, gap=4):
    x, y = xy
    empty = {"w": PAL["grey"], "c": PAL["dim"], "d": PAL["bar_bg"]}
    step = 8 * scale + gap
    for i in range(total):
        sprite(img, GEM, (x + i * step, y), None if i < filled else empty, scale=scale)


def qr(img, data, xy, module=3, border=2):
    """Black-on-white QR drawn directly onto the pixel grid."""
    import qrcode
    q = qrcode.QRCode(border=0, error_correction=qrcode.constants.ERROR_CORRECT_L)
    q.add_data(data)
    q.make(fit=True)
    m = q.get_matrix()
    n = len(m)
    x0, y0 = xy
    size = (n + border * 2) * module
    d = ImageDraw.Draw(img)
    d.rectangle((x0, y0, x0 + size - 1, y0 + size - 1), fill=PAL["white"])
    for j, row in enumerate(m):
        for i, on in enumerate(row):
            if on:
                px = x0 + (i + border) * module
                py = y0 + (j + border) * module
                d.rectangle((px, py, px + module - 1, py + module - 1), fill=PAL["ink"])
    return size


def fmt_btc(sats: int) -> str:
    return f"{sats / 1e8:.8f}"


def fmt_sats(sats: int) -> str:
    return f"{sats:,}"
