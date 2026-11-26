"""
EXT header pins 3 (SDA) and 1 (SCL) as an I2C bus for a secure chip, made
and removed while running (root, via `cz-backup ext-on` / `ext-off`).

  ext-on      load the i2c-gpio overlay on GPIO23/26 as /dev/i2c-3, give
              it to the signer only (group i2c, and the signer's device
              list), and switch on the pins' pull-ups
  ext-off     remove that overlay again (the NFC Cap uses the same pins)
  ext-status  "on" or "off"

Nothing is written to config.txt: the bus only exists while the wallet
runs and has asked for it, so nothing here can stop the next start. (An
unpowered chip on I2C1, EXT pins 8/10, held the keyboard's bus down and the
device restarted in a loop: that's why the chip gets a bus of its own.)

The wallet asks for it when no Cap answers on the EXT header, looks for a
chip, and removes it again if there's none.
"""
from __future__ import annotations

import grp
import os
import subprocess
import time

BUS = "/dev/i2c-3"
OVERLAY = ["i2c-gpio", "bus=3", "i2c_gpio_sda=23", "i2c_gpio_scl=26", "i2c_gpio_delay_us=10"]
SIGNER_UNIT = "czsigner.service"


def _run(cmd, timeout=20) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def status() -> str:
    return "on" if os.path.exists(BUS) else "off"


def _loaded() -> list[str]:
    """Indexes of our overlay among the ones loaded while running."""
    r = _run(["dtoverlay", "-l"])
    out = []
    for line in r.stdout.splitlines():
        idx, _, rest = line.partition(":")
        if idx.strip().isdigit() and rest.split()[:1] == ["i2c-gpio"] and "bus=3" in rest:
            out.append(idx.strip())
    return out


def _hand_to_signer():
    _run(["udevadm", "settle", "--timeout=5"])
    try:
        os.chown(BUS, 0, grp.getgrnam("i2c").gr_gid)
    except KeyError:
        pass
    os.chmod(BUS, 0o660)
    # the signer runs with DevicePolicy=closed: add this bus, until the next start
    _run(["systemctl", "set-property", "--runtime", SIGNER_UNIT, f"DeviceAllow={BUS} rw"])


def on() -> str:
    if not os.path.exists(BUS):
        _run(["pinctrl", "set", "23,26", "ip", "pu"])
        r = _run(["dtoverlay", *OVERLAY])
        if r.returncode != 0:
            raise OSError(f"dtoverlay: {(r.stderr or r.stdout).strip()[:80]}")
        for _ in range(30):
            if os.path.exists(BUS):
                break
            time.sleep(0.1)
        else:
            raise OSError("the bus didn't appear")
    _hand_to_signer()
    return "on"


def off() -> str:
    for idx in reversed(_loaded()):
        _run(["dtoverlay", "-r", idx])
    for _ in range(20):
        if not os.path.exists(BUS):
            break
        time.sleep(0.1)
    if os.path.exists(BUS):
        raise OSError("bus still there (set in config.txt?)")
    _run(["pinctrl", "set", "23,26", "ip", "pn"])
    return "off"
