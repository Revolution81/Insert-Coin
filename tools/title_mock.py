"""
Mock of the INSERT COIN title screen, drawn with the wallet's own theme at
the device's 320x170. Writes a still PNG and an animated GIF (coin spins,
PRESS OK blinks), both scaled up 3x with crisp pixels.

    python3 tools/title_mock.py [out_dir]
"""
import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ui import theme as t  # noqa: E402

W, H = t.W, t.H
LOGO = t._load("PressStart2P.ttf", 32)
VERSION = "v0.8.1"

# arcade "chrome": light at the top of each letter, deep orange at the bottom
BANDS = [(255, 240, 160), t.PAL["gold"], t.PAL["gold"], t.PAL["orange"], t.PAL["orange_dk"]]


def logo_text(img, xy, s):
    """Big letters: red drop shadow, dark outline, banded gold fill."""
    x, y = xy
    mask = Image.new("L", (W, H), 0)
    md = ImageDraw.Draw(mask)
    md.fontmode = "1"
    md.text((x, y + LOGO.dy), s, font=LOGO.face, fill=255)
    box = mask.getbbox()
    if not box:
        return
    px, mp = img.load(), mask.load()

    def stamp(dx, dy, color_at):
        for yy in range(box[1], box[3]):
            for xx in range(box[0], box[2]):
                if mp[xx, yy]:
                    X, Y = xx + dx, yy + dy
                    if 0 <= X < W and 0 <= Y < H:
                        px[X, Y] = color_at(xx, yy)

    red = t.PAL["red_dk"]
    ink = t.PAL["shadow"]
    for d in range(1, 5):                                  # long red drop shadow
        stamp(d, d, lambda xx, yy: red)
    for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (-1, -1), (1, -1), (-1, 1), (1, 1),
                   (-2, -1), (-2, 1), (2, -1), (2, 1), (-1, -2), (1, -2), (-1, 2), (1, 2)):
        stamp(dx, dy, lambda xx, yy: ink)                   # thick outline
    top, bot = box[1], box[3]

    def band(xx, yy):
        f = (yy - top) / max(1, bot - top - 1) * (len(BANDS) - 1)
        i = min(int(f), len(BANDS) - 2)
        frac = f - i
        use_next = frac > 0.75 or (frac > 0.4 and (xx + yy) % 2 == 0)   # dither, like the sky
        return BANDS[i + 1] if use_next else BANDS[i]
    stamp(0, 0, band)


def spinning_coin(img, xy, width):
    """The bitcoin coin squashed horizontally: width 32 = facing us."""
    c = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    t.sprite_rgba = None
    p = c.load()
    for j, row in enumerate(t.COIN):
        for i, ch in enumerate(row):
            if ch in t.COIN_COLORS:
                p[i, j] = t.COIN_COLORS[ch] + (255,)
    big = c.resize((max(2, width), 32), Image.NEAREST)
    x, y = xy
    # shadow under the coin
    sh = Image.new("RGBA", big.size, (0, 0, 0, 0))
    sh.paste((0, 0, 16, 255), (0, 0) + big.size, big)
    img.paste(sh, (x + 16 - big.width // 2 + 3, y + 3), sh)
    img.paste(big, (x + 16 - big.width // 2, y), big)


def sparkle(img, xy, size):
    px = img.load()
    x0, y0 = xy
    for k in range(-size, size + 1):
        for (x, y) in ((x0 + k, y0), (x0, y0 + k)):
            if 0 <= x < W and 0 <= y < H:
                px[x, y] = t.PAL["white"] if abs(k) < size else t.PAL["gold"]


SPIN = [32, 30, 24, 14, 4, 14, 24, 30, 32, 32, 32, 32, 32, 32, 32, 32]


def frame(i):
    img, d = t.new_canvas()
    t.sky(img, d, top=0, seed=11)
    # line 1: INSERT
    w1 = LOGO.width("INSERT")
    logo_text(img, ((W - w1) // 2, 16), "INSERT")
    # line 2: C [coin] IN, the coin standing in for the O
    cw = LOGO.width("C")
    total = cw + 32 + LOGO.width("IN")
    x = (W - total) // 2
    logo_text(img, (x, 58), "C")
    spinning_coin(img, (x + cw + 2, 56), SPIN[i % len(SPIN)])
    logo_text(img, (x + cw + 34, 58), "IN")
    if i % len(SPIN) in (9, 10, 11):                       # glint when it faces us
        sparkle(img, (x + cw + 28, 60), (3, 4, 2)[i % len(SPIN) - 9])
    # prompt and tribute
    if (i // 6) % 2 == 0:
        t.text(d, (W // 2, 108), "PRESS OK", t.BODY, "white", align="center")
    t.text(d, (W // 2, 126), "RUNNING BITCOIN...", t.TINY, "grey", align="center")
    # footer, on the hills
    t.text(d, (8, 158), "BITCOIN ONLY", t.TINY, "dim")
    t.text(d, (W - 8, 158), VERSION, t.TINY, "dim", align="right")
    return img


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    big = [frame(i).resize((W * 3, H * 3), Image.NEAREST) for i in range(48)]
    big[0].save(os.path.join(out, "Insert-Coin-title.png"))
    big[0].save(os.path.join(out, "Insert-Coin-title.gif"), save_all=True,
                append_images=big[1:], duration=80, loop=0, disposal=1)
    print("wrote", out)


if __name__ == "__main__":
    main()
