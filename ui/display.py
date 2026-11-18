"""
Push a 320x170 PIL image to the Cardputer Zero's ST7789v3 panel.

M5Stack hasn't documented yet whether the panel appears as a framebuffer
(/dev/fbN) or a DRM device, so this writes to a framebuffer when one exists
and falls back to saving a PNG. Check `ls /sys/class/graphics/` on the device
and set CZ_FB=/dev/fbN if auto-detection picks the wrong one.
"""
from __future__ import annotations

import glob
import os

from PIL import Image, ImageChops

from .theme import W, H


def _find_fb() -> str | None:
    want = os.environ.get("CZ_FB")
    if want:
        return want
    for sysdir in sorted(glob.glob("/sys/class/graphics/fb*")):
        try:
            with open(os.path.join(sysdir, "virtual_size")) as f:
                w, h = (int(v) for v in f.read().strip().split(","))
        except (OSError, ValueError):
            continue
        if {w, h} == {W, H}:
            return "/dev/" + os.path.basename(sysdir)
    # The Cardputer Zero image names its panel /dev/fb_lcd
    if os.path.exists("/dev/fb_lcd"):
        return os.path.realpath("/dev/fb_lcd")
    return None


_HI_R = [v & 0xF8 for v in range(256)]          # rrrrr000
_HI_G = [v >> 5 for v in range(256)]            # 00000ggg
_LO_G = [(v & 0x1C) << 3 for v in range(256)]   # ggg00000
_LO_B = [v >> 3 for v in range(256)]            # 000bbbbb


def rgb565(img: Image.Image, swap: bool = False) -> bytes:
    """Little-endian RGB565, the panel's format. Built from Pillow's own
    per-channel lookups (fast, no numpy): Pillow 12 removed the "BGR;16"
    raw packer this used to use. swap=True puts blue in the top bits for
    panels wired BGR (CZ_FB_BGR=1)."""
    r, g, b = img.convert("RGB").split()
    if swap:
        r, b = b, r
    hi = ImageChops.add(r.point(_HI_R), g.point(_HI_G))
    lo = ImageChops.add(g.point(_LO_G), b.point(_LO_B))
    return Image.merge("LA", (lo, hi)).tobytes()


class Display:
    def __init__(self, preview_path: str = "frame.png"):
        self.fb = _find_fb()
        self.preview_path = preview_path
        self.bpp = 16
        self.rotate = False
        if self.fb:
            sysdir = "/sys/class/graphics/" + os.path.basename(self.fb)
            with open(os.path.join(sysdir, "bits_per_pixel")) as f:
                self.bpp = int(f.read())
            with open(os.path.join(sysdir, "virtual_size")) as f:
                w, _ = (int(v) for v in f.read().strip().split(","))
            self.rotate = w != W   # panel mounted portrait

    def show(self, img: Image.Image) -> None:
        if not self.fb:
            img.save(self.preview_path)
            return
        if self.rotate:
            img = img.transpose(Image.Transpose.ROTATE_90)
        if self.bpp == 16:
            data = rgb565(img, swap=os.environ.get("CZ_FB_BGR") == "1")
        else:
            data = img.convert("RGBA").tobytes("raw", "BGRA")
        with open(self.fb, "wb") as f:
            f.write(data)
