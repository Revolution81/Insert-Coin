"""
The side (Grove) port carries one thing at a time:

  UART  GPIO14/15: the fingerprint sensor (Unit Fingerprint2).
  I2C   I2C1, the keyboard's own bus: M5Stack's Unit ID (secure chip) or
        the NFC Unit.

GPIO4 picks which (low = UART, high = I2C). M5Stack's overlay holds it as an
"LED" named grove_fun, switched through /sys/class/leds/grove_fun/brightness
(group gpio, like the 5 V switches). It starts low (UART) at every power-on,
so an unpowered unit on the side port can't hold the keyboard's bus down
while the device boots.

The port goes to I2C only while something answers there: wires on the side
port in I2C mode are the keyboard's bus.
"""
from __future__ import annotations

import os
import time

LED = os.environ.get("CZ_GROVE_FUN", "/sys/class/leds/grove_fun/brightness")
_SIM = {"mode": "uart"}


def _sim() -> bool:
    return os.environ.get("CZ_GROVE", "") == "sim" or not os.path.exists(LED)


def mode() -> str:
    if _sim():
        return _SIM["mode"]
    try:
        with open(LED) as f:
            return "i2c" if f.read().strip() not in ("", "0") else "uart"
    except OSError:
        return "uart"


def set_mode(m: str) -> bool:
    """Switch the side port; True once it's in that mode."""
    if m not in ("uart", "i2c"):
        raise ValueError(m)
    if mode() == m:
        return True
    if _sim():
        _SIM["mode"] = m
        return True
    try:
        with open(LED, "w") as f:
            f.write("1" if m == "i2c" else "0")
    except OSError:
        return False
    time.sleep(0.05)                     # the switch, and the unit seeing its bus
    return mode() == m
