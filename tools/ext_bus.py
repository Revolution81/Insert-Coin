"""
What the EXT header's pins 1 and 3 are for (root, via `cz-backup extbus`).

  cap   the NFC Cap (default): pins 1 and 3 are its power switch and NFC
        interrupt, so no extra bus.
  chip  a secure chip on its own I2C bus: pin 3 = SDA, pin 1 = SCL
        (/dev/i2c-3), readable only by the signer (group i2c).
  nfc   the NFC Unit (U216) on that same bus, for the wallet app (group
        czapp), as install.sh --nfc-unit did.

Why a bus of its own for the chip: on I2C1 (EXT pins 8 and 10) it shares the
wires with the keyboard and the M5IOE1 power chip. Its power (EXT pin 6) is
only switched on once the wallet starts, and until then the unpowered chip
and its pull-ups held those wires down: the keyboard and power chip never
started and the Cardputer restarted in a loop (seen on the device).

Changes take effect at the next start. Only a block between our own marker
lines in config.txt is ever written or removed (plus the old --nfc-unit
lines), and config.txt is copied to config.txt.insertcoin-bak first.
"""
from __future__ import annotations

import os
import re
import subprocess

CONFIG = "/boot/firmware/config.txt"
RULE = "/etc/udev/rules.d/63-czwallet-nfc-unit.rules"
DROPIN_DIR = "/etc/systemd/system/czsigner.service.d"
BEGIN = "# Insert Coin: EXT I2C bus (begin)"
END = "# Insert Coin: EXT I2C bus (end)"
OVERLAY = ("[all]\n"                    # whatever section config.txt ended in
           "gpio=23,26=pu\n"
           "dtoverlay=i2c-gpio,bus=3,i2c_gpio_sda=23,i2c_gpio_scl=26,i2c_gpio_delay_us=10")
OLD_COMMENT = "# Insert Coin: Unit NFC on its own I2C bus (EXT pin 3 SDA, pin 1 SCL)"
MODES = ("cap", "chip", "nfc")
GROUP = {"chip": "i2c", "nfc": "czapp"}


def strip(text: str) -> str:
    """config.txt without our block or the old --nfc-unit lines."""
    text = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", "", text, flags=re.S)
    keep = [ln for ln in text.split("\n")
            if ln.strip() not in (OLD_COMMENT, "gpio=23,26=pu")
            and not ln.strip().startswith("dtoverlay=i2c-gpio,bus=3,")]
    out = "\n".join(keep)
    return re.sub(r"\n{3,}", "\n\n", out)


def render(text: str, mode: str) -> str:
    out = strip(text).rstrip("\n") + "\n"
    if mode != "cap":
        out += f"\n{BEGIN}\n{OVERLAY}\n{END}\n"
    return out


def current(text: str) -> str:
    if BEGIN not in text and "dtoverlay=i2c-gpio,bus=3," not in text:
        return "cap"
    return "chip" if _rule_group() == "i2c" else "nfc"


def _rule_group() -> str | None:
    try:
        m = re.search(r'GROUP="([a-z0-9_]+)"', open(RULE).read())
        return m.group(1) if m else None
    except OSError:
        return None


def _rule(mode: str) -> str:
    who = ("the signer (group i2c): a secure chip" if mode == "chip"
           else "the wallet app (group czapp): the NFC Unit")
    return (f"# EXT pins 3 and 1 as I2C bus 3 (dtoverlay i2c-gpio, GPIO23/26), set by\n"
            f"# SETUP > DEVICE > HARDWARE > EXT HEADER: only {who}.\n"
            f"# Never I2C1 (the keyboard's bus) for either.\n"
            f'SUBSYSTEM=="i2c-dev", KERNEL=="i2c-3", GROUP="{GROUP.get(mode, "czapp")}", MODE="0660"\n')


def _atomic(path: str, data: str, mode: int = 0o644):
    tmp = path + ".insertcoin-new"
    with open(tmp, "w") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.chmod(tmp, mode)
    except OSError:                     # FAT (the boot partition) has no modes
        pass
    os.replace(tmp, path)


def apply(mode: str, config: str = CONFIG) -> str:
    if mode not in MODES:
        raise ValueError("mode must be cap, chip or nfc")
    old = open(config).read()
    new = render(old, mode)
    if new != old:
        with open(config + ".insertcoin-bak", "w") as f:
            f.write(old)
        _atomic(config, new, 0o755 if config.startswith("/boot") else 0o644)
    if mode != "cap":
        _atomic(RULE, _rule(mode))
    os.makedirs(DROPIN_DIR, exist_ok=True)
    _atomic(os.path.join(DROPIN_DIR, "ext-i2c.conf"),
            "# the secure chip may sit on its own bus (tools/ext_bus.py)\n"
            "[Service]\nDeviceAllow=/dev/i2c-3 rw\n")
    subprocess.run(["systemctl", "daemon-reload"], check=False, timeout=60)
    return "changed" if new != old else "same"
