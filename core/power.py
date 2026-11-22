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
    with open(path, errors="replace") as f:
        return f.read(64).strip()


# Resting Li-ion voltage to charge, for when the gauge's own percentage is
# off (an uncalibrated BQ27220 can say 100% on a nearly flat battery, which
# once let a Cardputer brown out while showing a full battery).
_VOLT_CURVE = [(3.30, 0.0), (3.50, 0.05), (3.60, 0.10), (3.70, 0.35), (3.75, 0.50),
               (3.80, 0.60), (3.90, 0.75), (4.00, 0.85), (4.10, 0.95), (4.18, 1.0)]


def level_from_voltage(volts: float) -> float:
    pts = _VOLT_CURVE
    if volts <= pts[0][0]:
        return 0.0
    for (v0, l0), (v1, l1) in zip(pts, pts[1:]):
        if volts <= v1:
            return l0 + (l1 - l0) * (volts - v0) / (v1 - v0)
    return 1.0


def _num(d: str, name: str) -> float | None:
    try:
        return float(_read(os.path.join(d, name)))
    except (OSError, ValueError):
        return None


GAUGE = "/run/czwallet-battery"


def read_gauge(path: str | None = None, max_age: float = 60.0) -> tuple[float, bool] | None:
    """The BQ27220 read directly by cz-power (root): "soc mV mA status".
    On the Cardputer Zero, Linux drives the gauge as the wrong model, so its
    /sys/class/power_supply numbers are wrong; this is the real reading.
    Plugged in: current isn't going out of the battery (on battery it always
    drains), or it's charging."""
    path = path or GAUGE
    try:
        if time.time() - os.path.getmtime(path) > max_age:
            return None
        soc, mv, ma, *_ = (int(x) for x in _read(path).split())
    except (OSError, ValueError):
        return None
    if not (0 <= soc <= 100 and 2500 < mv < 4600):
        return None
    return soc / 100, ma > -20


def read(root: str = ROOT) -> tuple[float, bool]:
    got = read_gauge()
    if got is not None:
        return got
    return read_sysfs(root)


def read_sysfs(root: str = ROOT) -> tuple[float, bool]:
    """(level 0..1, plugged in).

    Level: the gauge's percentage, but never more than its voltage says
    while discharging (an uncalibrated gauge can claim a full battery).
    Plugged in: the gauge says Charging, or a USB/mains supply is online, or
    the battery isn't being drained (current not negative) while the gauge
    says Full / Not charging (on battery it always drains)."""
    level, charging, plugged = None, False, False
    for d in sorted(glob.glob(os.path.join(root, "*"))):
        try:
            kind = _read(os.path.join(d, "type"))
        except OSError:
            kind = ""
        if kind == "Battery" or (not kind and os.path.exists(os.path.join(d, "capacity"))):
            cap = _num(d, "capacity")
            try:
                status = _read(os.path.join(d, "status"))
            except OSError:
                status = ""
            current = _num(d, "current_now")            # µA, negative = draining
            if current is None:
                current = _num(d, "current_avg")
            volts = _num(d, "voltage_now")
            draining = current is not None and current < -20000
            lvl = None if cap is None else max(0, min(100, cap)) / 100
            if volts and 2.5e6 < volts < 5e6 and (draining or lvl is None):
                by_volts = level_from_voltage(volts / 1e6)
                lvl = by_volts if lvl is None else min(lvl, by_volts)
            if level is None and lvl is not None:
                level = lvl
            charging = charging or status == "Charging" or (
                status in ("Full", "Not charging") and current is not None and not draining)
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
