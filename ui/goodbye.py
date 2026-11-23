"""The power-off screen (wallet image).

cz-power starts this, as root, when the power switch goes off: the wallet and
signer have just been stopped and Linux is shutting down. It shows SHUTTING
DOWN, and when it's told to stop (the very end of shutdown) or the power cut
is due, ALL DONE. No countdown: Linux finishes well before the chip cuts the
power, so a number never matched what happened.

  python -m ui.goodbye [DEADLINE]    (seconds since the epoch)
"""
from __future__ import annotations

import signal
import sys
import time

from PIL import Image, ImageDraw

from . import theme as t


def draw(done: bool = False) -> Image.Image:
    from .egg import sprite
    img = Image.new("RGB", (t.W, t.H), (8, 12, 40))
    d = ImageDraw.Draw(img)
    d.fontmode = "1"
    knight = sprite("knight", 4)
    img.paste(knight, (22, 40), knight)
    if done:
        t.text(d, (104, 60), "ALL DONE", t.BIG, "white")
        t.text(d, (104, 104), "Your keys are locked away.", t.TINY, "dim")
        t.text(d, (104, 118), "The power goes off by itself.", t.TINY, "dim")
    else:
        t.text(d, (104, 60), "SHUTTING", t.BIG, "white")
        t.text(d, (104, 80), "DOWN...", t.BIG, "white")
        t.text(d, (104, 104), "Saving and closing safely.", t.TINY, "dim")
        t.text(d, (104, 118), "Don't take the card out yet.", t.TINY, "dim")
    return img


def main(argv: list[str]) -> int:
    from .display import Display
    if len(argv) > 1 and argv[1] in ("--shutting", "--done"):
        # one frame and out: from czwallet-goodbye.service (SHUTTING DOWN,
        # as the signer stops) and the last shutdown hook (ALL DONE)
        disp = Display("/run/czwallet-goodbye.png")
        done = argv[1] == "--done"
        disp.show(draw(done=done))
        print(f"goodbye: {'ALL DONE' if done else 'SHUTTING DOWN'} shown on {disp.fb}", flush=True)
        return 0
    deadline = float(argv[1]) if len(argv) > 1 else time.time() + 8
    disp = Display("/run/czwallet-goodbye.png")
    stop = {"now": False}

    def on_term(*_):
        stop["now"] = True
    signal.signal(signal.SIGTERM, on_term)
    disp.show(draw())
    print(f"goodbye: SHUTTING DOWN shown on {disp.fb}", flush=True)
    while not stop["now"] and time.time() < deadline:
        time.sleep(0.1)
    disp.show(draw(done=True))
    print("goodbye: ALL DONE shown", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
