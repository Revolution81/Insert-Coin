"""
A plain output pin, for the Cap's power switch and the NFC chip select.

Uses the kernel's GPIO character device (/dev/gpiochipN, group gpio), so a
pin the kernel already gave to something else (say the Unit NFC's i2c-gpio
bus on GPIO23/26) is refused with EBUSY instead of being taken over. Only
when that interface is missing does it fall back to `pinctrl`.
"""
from __future__ import annotations

import ctypes
import errno
import fcntl
import glob
import os
import subprocess


def _iowr(nr: int, size: int) -> int:
    return (3 << 30) | (size << 16) | (0xB4 << 8) | nr


def _ior(nr: int, size: int) -> int:
    return (2 << 30) | (size << 16) | (0xB4 << 8) | nr


class _ChipInfo(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char * 32), ("label", ctypes.c_char * 32), ("lines", ctypes.c_uint32)]


class _HandleRequest(ctypes.Structure):
    _fields_ = [("lineoffsets", ctypes.c_uint32 * 64), ("flags", ctypes.c_uint32),
                ("default_values", ctypes.c_uint8 * 64), ("consumer_label", ctypes.c_char * 32),
                ("lines", ctypes.c_uint32), ("fd", ctypes.c_int)]


class _HandleData(ctypes.Structure):
    _fields_ = [("values", ctypes.c_uint8 * 64)]


CHIPINFO = _ior(0x01, ctypes.sizeof(_ChipInfo))
LINEHANDLE = _iowr(0x03, ctypes.sizeof(_HandleRequest))
SET_VALUES = _iowr(0x09, ctypes.sizeof(_HandleData))
OUTPUT = 1 << 1


class GpioBusy(OSError):
    pass


def _main_chip() -> str | None:
    """The SoC's own pins (label pinctrl-bcm2835 / -bcm2711 / rp1)."""
    for path in sorted(glob.glob("/dev/gpiochip*")):
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            continue
        try:
            info = _ChipInfo()
            fcntl.ioctl(fd, CHIPINFO, info)
            label = info.label.decode(errors="replace")
            if label.startswith("pinctrl-") or label.startswith("rp1"):
                return path
        except OSError:
            pass
        finally:
            os.close(fd)
    return None


class OutputPin:
    def __init__(self, pin: int, value: int, label: str = "czwallet"):
        self.pin = pin
        self.fd = None
        self.value = None
        chip = os.environ.get("CZ_GPIOCHIP") or _main_chip()
        if chip:
            try:
                cfd = os.open(chip, os.O_RDONLY)
            except PermissionError:
                raise
            try:
                req = _HandleRequest()
                req.lineoffsets[0] = pin
                req.flags = OUTPUT
                req.default_values[0] = 1 if value else 0
                req.consumer_label = label.encode()[:31]
                req.lines = 1
                fcntl.ioctl(cfd, LINEHANDLE, req)
                self.fd = req.fd
                self.value = 1 if value else 0
                return
            except OSError as e:
                if e.errno == errno.EBUSY:
                    raise GpioBusy(errno.EBUSY, f"GPIO{pin} in use") from None
                if e.errno not in (errno.ENOTTY, errno.EINVAL):
                    raise
            finally:
                os.close(cfd)
        self._pinctrl(value)                            # no character device: last resort

    def _pinctrl(self, value: int):
        subprocess.run(["pinctrl", "set", str(self.pin), "op", "dh" if value else "dl"],
                       check=True, capture_output=True, timeout=5)
        self.value = 1 if value else 0

    def set(self, value: int):
        value = 1 if value else 0
        if value == self.value:
            return
        if self.fd is None:
            self._pinctrl(value)
            return
        d = _HandleData()
        d.values[0] = value
        fcntl.ioctl(self.fd, SET_VALUES, d)
        self.value = value

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
