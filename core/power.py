"""
Battery level and charging, from the Linux power-supply class
(/sys/class/power_supply). On the Cardputer Zero that's the BQ27220 fuel
gauge, whose "status" says Charging, Discharging, Full or Not charging;
M5Stack's launcher shows a charging symbol on "Charging" too.
"""
from __future__ import annotations

import glob
import os
import time

ROOT = "/sys/class/power_supply"
_cache: tuple[float, float, bool] | None = None


def _read(path: str) -> str:
    with open(path) as f:
        return f.read().strip()


def read(root: str = ROOT) -> tuple[float, bool]:
    """(level 0..1, plugged in and charging or full)."""
    level, charging, plugged = None, False, False
    for d in sorted(glob.glob(os.path.join(root, "*"))):
        try:
            kind = _read(os.path.join(d, "type"))
        except OSError:
            kind = ""
        if kind == "Battery" or (not kind and os.path.exists(os.path.join(d, "capacity"))):
            try:
                if level is None:
                    level = max(0, min(100, int(_read(os.path.join(d, "capacity"))))) / 100
            except (OSError, ValueError):
                pass
            try:
                charging = charging or _read(os.path.join(d, "status")) in ("Charging", "Full")
            except OSError:
                pass
        elif kind in ("USB", "Mains"):
            try:
                plugged = plugged or _read(os.path.join(d, "online")) == "1"
            except OSError:
                pass
    return (1.0 if level is None else level), (charging or plugged)


def battery(max_age: float = 2.0) -> tuple[float, bool]:
    """read(), at most every max_age seconds (the status bar redraws often)."""
    global _cache
    now = time.monotonic()
    if _cache is None or now - _cache[0] > max_age:
        _cache = (now, *read())
    return _cache[1], _cache[2]
