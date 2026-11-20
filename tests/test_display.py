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


def test_backlight_turned_on_only_when_off(tmp_path):
    from ui.display import backlight_on
    (tmp_path / "max_brightness").write_text("100\n")
    (tmp_path / "brightness").write_text("0\n")
    assert backlight_on(str(tmp_path)) and (tmp_path / "brightness").read_text() == "100"
    (tmp_path / "brightness").write_text("40\n")
    assert backlight_on(str(tmp_path)) and (tmp_path / "brightness").read_text() == "40\n"
    assert not backlight_on(str(tmp_path / "missing"))


def test_screen_sleep_dims_and_the_waking_key_is_dropped(tmp_path):
    from ui.display import ScreenSleep
    (tmp_path / "max_brightness").write_text("100\n")
    (tmp_path / "brightness").write_text("80\n")
    t = [0.0]
    s = ScreenSleep(lambda: 120, path=str(tmp_path), clock=lambda: t[0])
    t[0] = 119
    assert not s.tick() and (tmp_path / "brightness").read_text() == "80\n"
    t[0] = 121
    assert s.tick() and (tmp_path / "brightness").read_text() == "0"
    assert s.keep("KEY_ENTER", 1) is False                  # wakes, not passed on
    assert (tmp_path / "brightness").read_text() == "80" and not s.asleep
    assert s.keep("KEY_ENTER", 2) is False and s.keep("KEY_ENTER", 0) is False
    assert s.keep("KEY_ENTER", 1) is True                   # next press is real
    t[0] = 200
    assert not s.tick()                                     # the key reset the timer


def test_screen_sleep_never(tmp_path):
    from ui.display import ScreenSleep
    t = [0.0]
    s = ScreenSleep(lambda: 0, path=str(tmp_path), clock=lambda: t[0])
    t[0] = 10_000
    assert not s.tick()


def test_signer_keeps_the_sleep_setting(tmp_path):
    import pytest
    from core.signer import Signer, SignerError
    s = Signer(str(tmp_path / "s"))
    os.makedirs(s.dir, exist_ok=True)
    assert s.screen_sleep() == 120
    assert s.set_screen_sleep(600) == 600 and s.screen_sleep() == 600
    assert s.set_screen_sleep(0) == 0 and s.screen_sleep() == 0
    for bad in (30, -1, True, "120", 10 ** 9):
        with pytest.raises(SignerError):
            s.set_screen_sleep(bad)
    assert s.status()["screen_sleep"] == 0


def test_signer_waits_for_screen_and_keyboard(monkeypatch):
    """At boot the panel/keyboard may appear after the signer starts: it
    waits, then gives a clear error instead of crashing straight away."""
    import pytest
    import ui.display as display
    from core import signer_service
    t = [0.0]
    calls = []
    monkeypatch.setattr(display, "_find_fb", lambda: None)
    with pytest.raises(SystemExit, match="screen or keyboard"):
        signer_service.wait_for_devices(timeout=5, clock=lambda: t[0],
                                        sleep=lambda s: (calls.append(s), t.__setitem__(0, t[0] + s)))
    assert len(calls) >= 10          # it kept trying for the whole timeout
