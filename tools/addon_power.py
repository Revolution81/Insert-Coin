"""
Switch on the 5 V supply of the Grove port and the EXT header (root).

On the Cardputer Zero both are switched by the M5IOE1 I/O expander, which
the kernel exposes as the GPIO chip "m5ioe1" (line = IO number - 1; see
github.com/prokrypt/CardputerZero-Open-IOE1, its pin table):
  line 3  (IO4,  PA3): Grove 5 V  (M5's PYG4_GROVE_EN)
  line 12 (IO13, PA2): EXT 5 V out (M5's PYG13_HAT_EN)
M5Stack's launcher switches them on; the wallet image turns that launcher
off, so nothing did, and the fingerprint sensor and the NFC Cap got no
power. Only these two lines are ever touched: line 13 holds the system
power, line 11 is the LCD reset.

Same method as deploy/cz-light: request the line as an output with the
value through the v2 GPIO uAPI, then release it, which keeps the value.
"""
from __future__ import annotations

import ctypes
import fcntl
import glob
import os

LABEL = "m5ioe1"
GROVE_5V, EXT_5V = 3, 12
LINES = (GROVE_5V, EXT_5V)


class _Attr(ctypes.Structure):
    _fields_ = [("id", ctypes.c_uint32), ("padding", ctypes.c_uint32), ("value", ctypes.c_uint64)]


class _ConfigAttr(ctypes.Structure):
    _fields_ = [("attr", _Attr), ("mask", ctypes.c_uint64)]


class _LineConfig(ctypes.Structure):
    _fields_ = [("flags", ctypes.c_uint64), ("num_attrs", ctypes.c_uint32),
                ("padding", ctypes.c_uint32 * 5), ("attrs", _ConfigAttr * 10)]


class _LineRequest(ctypes.Structure):
    _fields_ = [("offsets", ctypes.c_uint32 * 64), ("consumer", ctypes.c_char * 32),
                ("config", _LineConfig), ("num_lines", ctypes.c_uint32),
                ("event_buffer_size", ctypes.c_uint32), ("padding", ctypes.c_uint32 * 5),
                ("fd", ctypes.c_int32)]


class _ChipInfo(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char * 32), ("label", ctypes.c_char * 32),
                ("lines", ctypes.c_uint32)]


def _iowr(nr, size):
    return (3 << 30) | (size << 16) | (0xB4 << 8) | nr


def _ior(nr, size):
    return (2 << 30) | (size << 16) | (0xB4 << 8) | nr


GET_CHIPINFO = _ior(0x01, ctypes.sizeof(_ChipInfo))
GET_LINE = _iowr(0x07, ctypes.sizeof(_LineRequest))
FLAG_OUTPUT = 1 << 3
ATTR_OUTPUT_VALUES = 2


def find_chip() -> str | None:
    for path in sorted(glob.glob("/dev/gpiochip*")):
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            continue
        try:
            info = _ChipInfo()
            fcntl.ioctl(fd, GET_CHIPINFO, info)
            if info.label.decode(errors="replace") == LABEL:
                return path
        except OSError:
            pass
        finally:
            os.close(fd)
    return None


def request(lines=LINES) -> dict:
    """Both 5 V outputs on. Returns {"chip", "lines"} for a log line."""
    assert all(n in LINES for n in lines)              # never anything else
    chip = find_chip()
    if not chip:
        raise OSError("m5ioe1 GPIO chip not found")
    req = _LineRequest()
    for i, n in enumerate(lines):
        req.offsets[i] = n
    req.num_lines = len(lines)
    req.consumer = b"insert-coin-addons"
    req.config.flags = FLAG_OUTPUT
    req.config.num_attrs = 1
    req.config.attrs[0].attr.id = ATTR_OUTPUT_VALUES
    req.config.attrs[0].attr.value = (1 << len(lines)) - 1
    req.config.attrs[0].mask = (1 << len(lines)) - 1
    fd = os.open(chip, os.O_RDONLY)
    try:
        fcntl.ioctl(fd, GET_LINE, req)
    finally:
        os.close(fd)
    os.close(req.fd)                                   # released: the value stays
    return {"chip": chip, "lines": list(lines)}
