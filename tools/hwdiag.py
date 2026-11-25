"""
Read-only hardware report for the boot log (root). Run by `cz-backup
addons` after switching the add-on power, so insert-coin-bootlog.txt shows
what the kernel and the chips actually say instead of guesses:

  - who holds each GPIO line that matters (SoC: 4 Grove UART/I2C switch,
    7 CC1101 CS, 14/15 UART, 22 NFC CS, 23 NFC IRQ, 26 Cap power; m5ioe1:
    every line), with direction and value
  - pinctrl's view of the SoC pins (function: UART or plain GPIO)
  - the M5IOE1's own GPIO registers (read only)
  - the boot config lines about UART, SPI, I2C and clocks
  - which serial and SPI devices exist

Nothing here writes anything.
"""
from __future__ import annotations

import ctypes
import fcntl
import glob
import os
import subprocess

SOC_LINES = (4, 7, 14, 15, 22, 23, 26)


class _Attr(ctypes.Structure):
    _fields_ = [("id", ctypes.c_uint32), ("padding", ctypes.c_uint32), ("value", ctypes.c_uint64)]


class _LineInfo(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char * 32), ("consumer", ctypes.c_char * 32),
                ("offset", ctypes.c_uint32), ("num_attrs", ctypes.c_uint32),
                ("flags", ctypes.c_uint64), ("attrs", _Attr * 10), ("padding", ctypes.c_uint32 * 4)]


class _ChipInfo(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char * 32), ("label", ctypes.c_char * 32),
                ("lines", ctypes.c_uint32)]


def _iowr(nr, size):
    return (3 << 30) | (size << 16) | (0xB4 << 8) | nr


def _ior(nr, size):
    return (2 << 30) | (size << 16) | (0xB4 << 8) | nr


GET_CHIPINFO = _ior(0x01, ctypes.sizeof(_ChipInfo))
GET_LINEINFO = _iowr(0x05, ctypes.sizeof(_LineInfo))
USED, ACTIVE_LOW, INPUT, OUTPUT = 1, 2, 4, 8


def _chips():
    out = []
    for path in sorted(glob.glob("/dev/gpiochip*")):
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            continue
        try:
            info = _ChipInfo()
            fcntl.ioctl(fd, GET_CHIPINFO, info)
            out.append((path, info.label.decode(errors="replace"), info.lines))
        except OSError:
            pass
        finally:
            os.close(fd)
    return out


def line_info(chip: str, offset: int) -> str:
    fd = os.open(chip, os.O_RDONLY)
    try:
        li = _LineInfo()
        li.offset = offset
        fcntl.ioctl(fd, GET_LINEINFO, li)
    finally:
        os.close(fd)
    f = li.flags
    d = "out" if f & OUTPUT else "in" if f & INPUT else "?"
    used = f"USED by '{li.consumer.decode(errors='replace')}'" if f & USED else "free"
    return f"line {offset:2d} {li.name.decode(errors='replace') or '-':12s} {d:3s} {used}"


def _run(cmd) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return (r.stdout + r.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return f"{cmd[0]}: {e}"


def report() -> list[str]:
    out = ["== hwdiag"]
    for path, label, n in _chips():
        if label.startswith("pinctrl-"):
            out.append(f"{path} {label} ({n} lines)")
            for i in SOC_LINES:
                try:
                    out.append("  " + line_info(path, i))
                except OSError as e:
                    out.append(f"  line {i}: {e}")
        elif label == "m5ioe1":
            out.append(f"{path} {label} ({n} lines)")
            for i in range(n):
                try:
                    out.append("  " + line_info(path, i))
                except OSError as e:
                    out.append(f"  line {i}: {e}")
        else:
            out.append(f"{path} {label} ({n} lines)")
    out.append("pinctrl: " + _run(["pinctrl", "get", ",".join(map(str, SOC_LINES))]).replace("\n", " | "))
    regs = []
    for r in (0x03, 0x04, 0x05, 0x06, 0x07, 0x08):              # GPIO_M, GPIO_O, GPIO_I
        regs.append(f"{r:02x}={_run(['i2cget', '-f', '-y', '1', '0x4f', hex(r)])}")
    out.append("m5ioe1 regs (mode lo/hi, out lo/hi, in lo/hi): " + " ".join(regs))
    try:
        with open("/boot/firmware/config.txt") as f:
            keep = [ln.strip() for ln in f if any(k in ln for k in
                    ("uart", "spi", "i2c", "core_freq", "dtoverlay", "dtparam", "gpio="))]
        out.append("config.txt: " + " | ".join(keep))
    except OSError as e:
        out.append(f"config.txt: {e}")
    devs = sorted(glob.glob("/dev/serial*") + glob.glob("/dev/ttyS*") + glob.glob("/dev/ttyAMA*")
                  + glob.glob("/dev/spidev*") + glob.glob("/dev/i2c-*"))
    out.append("devices: " + " ".join(f"{d}->{os.path.realpath(d)}" if os.path.islink(d) else d
                                      for d in devs))
    return out


if __name__ == "__main__":
    print("\n".join(report()))
