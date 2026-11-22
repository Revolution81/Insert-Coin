"""SETUP > DISPLAY & SOUND > TIME ZONE.

The clock comes from the Cardputer's RTC and, when Online, network time; the
time zone is a separate setting. M5Stack's OS ships with China time, so the
clock read hours off for everyone else. It's chosen here from a short list
(never worked out from the network: that would mean telling a server where
you are). Setting it goes through a tiny root helper, deploy/cz-time.
"""
from __future__ import annotations

import os
import subprocess
import time

HELPER = "/usr/local/sbin/cz-time"
ZONEINFO = "/usr/share/zoneinfo"

ZONES = [
    ("UTC", "UTC"),
    ("HONOLULU", "Pacific/Honolulu"),
    ("ANCHORAGE", "America/Anchorage"),
    ("LOS ANGELES", "America/Los_Angeles"),
    ("DENVER", "America/Denver"),
    ("PHOENIX", "America/Phoenix"),
    ("CHICAGO", "America/Chicago"),
    ("MEXICO CITY", "America/Mexico_City"),
    ("NEW YORK", "America/New_York"),
    ("HALIFAX", "America/Halifax"),
    ("SAO PAULO", "America/Sao_Paulo"),
    ("BUENOS AIRES", "America/Argentina/Buenos_Aires"),
    ("LONDON", "Europe/London"),
    ("LISBON", "Europe/Lisbon"),
    ("LAGOS", "Africa/Lagos"),
    ("PARIS", "Europe/Paris"),
    ("BERLIN", "Europe/Berlin"),
    ("JOHANNESBURG", "Africa/Johannesburg"),
    ("CAIRO", "Africa/Cairo"),
    ("ATHENS", "Europe/Athens"),
    ("ISTANBUL", "Europe/Istanbul"),
    ("MOSCOW", "Europe/Moscow"),
    ("DUBAI", "Asia/Dubai"),
    ("KARACHI", "Asia/Karachi"),
    ("KOLKATA", "Asia/Kolkata"),
    ("BANGKOK", "Asia/Bangkok"),
    ("SINGAPORE", "Asia/Singapore"),
    ("SHANGHAI", "Asia/Shanghai"),
    ("TOKYO", "Asia/Tokyo"),
    ("SEOUL", "Asia/Seoul"),
    ("SYDNEY", "Australia/Sydney"),
    ("AUCKLAND", "Pacific/Auckland"),
]


def current(localtime: str = "/etc/localtime") -> str:
    """The system's zone name, e.g. "America/New_York" ("UTC" if unknown)."""
    try:
        real = os.path.realpath(localtime)
    except OSError:
        return "UTC"
    marker = ZONEINFO.rstrip("/") + "/"
    if marker in real:
        name = real.split(marker, 1)[1]
        for prefix in ("posix/", "right/"):
            if name.startswith(prefix):
                name = name[len(prefix):]
        return name or "UTC"
    return "UTC"


def label(zone: str | None = None) -> str:
    zone = zone or current()
    for lbl, z in ZONES:
        if z == zone:
            return lbl
    if zone in ("Etc/UTC", "Etc/GMT", "UCT", "Universal", "Zulu"):
        return "UTC"
    return zone.rsplit("/", 1)[-1].replace("_", " ").upper()


def step(zone: str, by: int) -> str:
    """The next (by=1) or previous (by=-1) zone in the list."""
    names = [z for _, z in ZONES]
    i = names.index(zone) if zone in names else (names.index("UTC") if by > 0 else 1)
    return names[(i + by) % len(names)]


def set_zone(zone: str) -> bool:
    names = [z for _, z in ZONES]
    if zone not in names:
        return False
    if os.environ.get("CZ_SIM"):
        os.environ["TZ"] = zone
    elif os.path.exists(HELPER):
        r = subprocess.run(["sudo", "-n", HELPER, "zone", zone],
                           capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return False
    else:
        return False
    if not os.environ.get("CZ_SIM"):
        os.environ.pop("TZ", None)          # follow /etc/localtime
    time.tzset()
    return True
