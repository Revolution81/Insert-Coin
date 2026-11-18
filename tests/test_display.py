import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from ui.display import rgb565  # noqa: E402


def px(rgb, swap=False):
    return struct.unpack("<H", rgb565(Image.new("RGB", (1, 1), rgb), swap))[0]


def test_rgb565_matches_the_formula():
    for r, g, b in [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255), (0, 0, 0),
                    (18, 52, 86), (247, 200, 0), (123, 45, 210)]:
        assert px((r, g, b)) == ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
    assert px((255, 0, 0)) == 0xF800 and px((0, 0, 255)) == 0x001F
    assert px((255, 0, 0), swap=True) == 0x001F


def test_rgb565_full_frame_size_and_speed():
    import time
    img = Image.new("RGB", (320, 170), (30, 60, 90))
    t = time.monotonic()
    data = rgb565(img)
    assert len(data) == 320 * 170 * 2
    assert time.monotonic() - t < 0.5
