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

from PIL import Image

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
    return None


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
            data = img.convert("RGB").tobytes("raw", "BGR;16")  # RGB565 LE
        else:
            data = img.convert("RGBA").tobytes("raw", "BGRA")
        with open(self.fb, "wb") as f:
            f.write(data)
