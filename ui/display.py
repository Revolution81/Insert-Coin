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
import time

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


BACKLIGHT = "/sys/class/backlight/backlight"


def backlight_on(path: str = BACKLIGHT) -> bool:
    """Turn the panel's backlight up if it's off. M5Stack's launcher dims it
    to 0 when it stops, which leaves a perfectly working screen black.
    Best effort: needs write access (deploy/61-czwallet-backlight.rules)."""
    try:
        with open(os.path.join(path, "brightness")) as f:
            if int(f.read().strip() or 0) > 0:
                return True
        with open(os.path.join(path, "max_brightness")) as f:
            mx = int(f.read().strip())
        with open(os.path.join(path, "brightness"), "w") as f:
            f.write(str(mx))
        return True
    except (OSError, ValueError):
        return False


def _bl_dim(path: str = BACKLIGHT) -> int | None:
    """Brightness to its lowest lit step; returns the level to go back to
    (full if it was off), or None if the light can't be set from here."""
    cur, mx = _bl_read("brightness", path), _bl_read("max_brightness", path)
    if mx is None or cur is None:
        return None
    if not _bl_write(1, path):
        return None
    return cur if cur > 1 else mx


def _bl_read(name: str, path: str = BACKLIGHT) -> int | None:
    try:
        with open(os.path.join(path, name)) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _bl_write(value: int, path: str = BACKLIGHT) -> bool:
    try:
        with open(os.path.join(path, "brightness"), "w") as f:
            f.write(str(value))
        return True
    except OSError as e:
        import sys
        print(f"screen: backlight {value} not set: {e}", file=sys.stderr, flush=True)
        return False


class ScreenSleep:
    """SETUP > SCREEN SLEEP: backlight off after `seconds()` with no key
    pressed; the next key only wakes the screen (it isn't passed on, so a
    stray press can't choose anything you couldn't see)."""

    def __init__(self, seconds_fn, path: str = BACKLIGHT, clock=time.monotonic):
        self.seconds_fn, self.path, self.clock = seconds_fn, path, clock
        self.last = clock()
        self.asleep = False
        self._swallow: set[str] = set()
        self._secs, self._read_at = 120, -99.0
        self._level: int | None = None

    def seconds(self) -> int:
        now = self.clock()
        if now - self._read_at > 5:                 # a file read: not every frame
            self._read_at = now
            try:
                self._secs = int(self.seconds_fn())
            except Exception:  # noqa: BLE001
                pass
        return self._secs

    def keep(self, name: str, value: int) -> bool:
        """Feed every key event; False means drop it (it woke the screen)."""
        if name in self._swallow:
            if value == 0:
                self._swallow.discard(name)
            return False
        self.last = self.clock()
        if self.asleep and value == 1:
            self.wake()
            self._swallow.add(name)
            return False
        return True

    def tick(self) -> bool:
        """Call each loop; True while asleep (skip drawing then)."""
        secs = self.seconds()
        if not self.asleep and secs and self.clock() - self.last > secs:
            self.sleep()
        return self.asleep

    def sleep(self) -> None:
        import sys
        print(f"screen: sleeping after {self._secs} s", file=sys.stderr, flush=True)
        cur = _bl_read("brightness", self.path)
        self._level = cur if cur else None
        _bl_write(0, self.path)
        _light_flag(True)
        self.asleep = True

    def wake(self) -> None:
        level = self._level or _bl_read("max_brightness", self.path) or 100
        _bl_write(level, self.path)
        _light_flag(False)
        self.asleep = False
        self.last = self.clock()


# Brightness 0 doesn't turn the Cardputer Zero's light off: the panel stays
# lit (logs showed brightness 0 with the screen still on). The light has a
# real on/off switch, line 9 of the M5IOE1 chip (as M5Stack's launcher has
# it); the root power service (deploy/cz-power) switches it when this flag
# file appears or goes away. The signer may only write its own run folder.
SCREEN_OFF_FLAG = "/run/czwallet/screen-off"


def _light_flag(off: bool, path: str | None = None) -> None:
    path = path or SCREEN_OFF_FLAG
    try:
        if off:
            if os.path.isdir(os.path.dirname(path)):
                open(path, "w").close()
        elif os.path.exists(path):
            os.remove(path)
    except OSError as e:
        import sys
        print(f"screen: light switch flag not set: {e}", file=sys.stderr, flush=True)


FBIOGET_VSCREENINFO, FBIOPUT_VSCREENINFO, FBIOBLANK = 0x4600, 0x4601, 0x4611
FB_ACTIVATE_FORCE = 128


def wake_panel(fb: str) -> bool:
    """Switch the panel on through its framebuffer: unblank it and force a
    mode set. On M5Stack's November 2026 OS the panel is a DRM device whose
    framebuffer shows nothing until something sets its mode, which their
    splash or the text console used to do; the wallet image turns both off,
    so the screen stayed black. Harmless where it's already on."""
    import fcntl
    try:
        fd = os.open(fb, os.O_RDWR)
    except OSError:
        return False
    try:
        try:
            fcntl.ioctl(fd, FBIOBLANK, 0)                    # FB_BLANK_UNBLANK
        except OSError:
            pass
        var = bytearray(160)                                 # fb_var_screeninfo
        fcntl.ioctl(fd, FBIOGET_VSCREENINFO, var)
        act = int.from_bytes(var[84:88], "little") | FB_ACTIVATE_FORCE
        var[84:88] = act.to_bytes(4, "little")               # .activate
        fcntl.ioctl(fd, FBIOPUT_VSCREENINFO, var)
        import sys
        print(f"screen: panel {fb} switched on", file=sys.stderr, flush=True)
        return True
    except OSError as e:
        import sys
        print(f"screen: couldn't switch the panel on: {e}", file=sys.stderr, flush=True)
        return False
    finally:
        os.close(fd)


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
            # Black first, then the mode set: the set pushes whatever the
            # framebuffer holds to the panel, and until the wallet's first
            # frame that was leftover memory, a white flicker down one side
            # between the M5 logo and the title. Black again after, in case
            # the mode set itself handed back fresh memory.
            # The light down to its lowest step while the panel is set up, and
            # back up only once the first real frame is on it: whatever the
            # mode set flashes then happens in the dark. (Not 0: on the
            # Cardputer Zero that hands the pin back to the I/O expander and
            # the light stays on.)
            self._light_after = _bl_dim()
            self.clear()
            wake_panel(self.fb)
            self.clear()
            if self._light_after is None:
                backlight_on()

    def clear(self) -> None:
        """The whole framebuffer to black (its real size, not just 320x170)."""
        try:
            size = os.path.getsize(self.fb)
            if not size:                                     # a char device: ask sysfs
                sysdir = "/sys/class/graphics/" + os.path.basename(self.fb)
                with open(os.path.join(sysdir, "virtual_size")) as f:
                    vw, vh = (int(v) for v in f.read().strip().split(","))
                size = vw * vh * self.bpp // 8
            with open(self.fb, "wb") as f:
                f.write(bytes(size))
            self._last = None
        except OSError:
            pass

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
        if data == getattr(self, "_last", None):
            return                       # the panel already shows it
        self._last = data
        with open(self.fb, "wb") as f:
            f.write(data)
        if getattr(self, "_light_after", None) is not None:
            level, self._light_after = self._light_after, None
            _bl_write(level)                 # the first frame is up: light on
