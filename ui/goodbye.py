"""The power-off countdown (wallet image).

cz-power starts this, as root, when the power switch goes off: the wallet and
signer have just been stopped, Linux is shutting down, and the switch's chip
cuts the power at the deadline. This counts down to it on the screen, and if
it's told to stop (the very end of shutdown) it leaves a last frame up.

  python -m ui.goodbye DEADLINE      (seconds since the epoch)
"""
from __future__ import annotations

import math
import signal
import sys
import time

from PIL import Image, ImageDraw

from . import theme as t


def _big_number(img: Image.Image, s: str, xy: tuple[int, int], scale: int = 3) -> None:
    """The countdown number, pixel-doubled (tripled) from the big font."""
    w = t.BIG.width(s) if hasattr(t.BIG, "width") else 40
    small = Image.new("RGBA", (w + 4, 24), (0, 0, 0, 0))
    sd = ImageDraw.Draw(small)
    sd.fontmode = "1"
    t.text(sd, (0, 0), s, t.BIG, "white")
    big = small.resize((small.width * scale, small.height * scale), Image.NEAREST)
    img.paste(big, xy, big)


def draw(left: int, done: bool = False) -> Image.Image:
    from .egg import sprite
    img = Image.new("RGB", (t.W, t.H), (8, 12, 40))
    d = ImageDraw.Draw(img)
    d.fontmode = "1"
    knight = sprite("knight", 4)
    img.paste(knight, (22, 40), knight)
    t.text(d, (104, 30), "POWERING OFF", t.BODY, "gold")
    if done:
        t.text(d, (104, 60), "ALL DONE", t.BIG, "white")
        t.text(d, (104, 104), "Your keys are locked away.", t.TINY, "dim")
        t.text(d, (104, 118), "The power goes off by itself.", t.TINY, "dim")
    else:
        _big_number(img, str(max(0, left)), (104, 46))
        t.text(d, (104, 104), "Saving and closing safely.", t.TINY, "dim")
        t.text(d, (104, 118), "Don't take the card out yet.", t.TINY, "dim")
    return img


def main(argv: list[str]) -> int:
    from .display import Display
    deadline = float(argv[1]) if len(argv) > 1 else time.time() + 8
    disp = Display("/run/czwallet-goodbye.png")
    stop = {"now": False}

    def on_term(*_):
        stop["now"] = True
    signal.signal(signal.SIGTERM, on_term)
    shown = None
    while not stop["now"]:
        left = math.ceil(deadline - time.time())
        if left != shown:
            disp.show(draw(left))
            shown = left
        if left <= 0:
            break
        time.sleep(0.1)
    disp.show(draw(0, done=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
