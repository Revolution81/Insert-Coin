"""
System changes an update brings along (run as root).

An update only replaces the wallet's code; a few releases also need a small
change to the system around it (a group, a permission). Those live here as
a fixed list of steps, each safe to run any number of times. They ship
inside the signed release, so they're as trusted as the rest of the code.

Who runs them:
  - tools/apply_update.py, right after installing a release, with the NEW
    release's copy of this file;
  - the app at start, through `sudo cz-backup migrate`, when it sees a step
    is still missing (needed once: updates installed by an older helper
    didn't run this).

Takes no input. Prints "changed" if anything changed (the app's service is
then restarted so it picks up new groups), else "ok".
"""
from __future__ import annotations

import grp
import os
import pwd
import subprocess
import sys

APP_USER = "czapp"


def _in_group(user: str, group: str) -> bool:
    try:
        g = grp.getgrnam(group)
    except KeyError:
        return False
    try:
        if pwd.getpwnam(user).pw_gid == g.gr_gid:
            return True
    except KeyError:
        return False
    return user in g.gr_mem


def step_gpio_for_app() -> bool:
    """0.8.28: the NFC Cap's power switch and chip select are plain GPIOs,
    so the app needs the gpio group (/dev/gpiochip*)."""
    try:
        pwd.getpwnam(APP_USER)
    except KeyError:
        return False                                    # not a device install
    if _in_group(APP_USER, "gpio"):
        return False
    try:
        grp.getgrnam("gpio")
    except KeyError:
        subprocess.run(["groupadd", "--system", "gpio"], check=True)
    subprocess.run(["usermod", "-aG", "gpio", APP_USER], check=True)
    return True


EXT_RULE = "/etc/udev/rules.d/63-czwallet-nfc-unit.rules"


def step_ext_bus_rule() -> bool:
    """0.8.42: /dev/i2c-3 (EXT pins 3/1) is now only ever a secure chip's
    bus, so the signer's (group i2c), never the app's. No restart needed:
    returns False."""
    try:
        text = open(EXT_RULE).read()
    except OSError:
        return False
    if 'GROUP="czapp"' not in text:
        return False
    tmp = EXT_RULE + ".new"
    with open(tmp, "w") as f:
        f.write(text.replace('GROUP="czapp"', 'GROUP="i2c"'))
    os.replace(tmp, EXT_RULE)
    subprocess.run(["udevadm", "control", "--reload-rules"], check=False, timeout=30)
    print("ext bus rule: group i2c", file=sys.stderr)
    return False


CONFIG = "/boot/firmware/config.txt"
GROVE_BEGIN = "# Insert Coin: side port starts on UART (begin)"
GROVE_END = "# Insert Coin: side port starts on UART (end)"
GROVE_BLOCK = ("# GPIO4 picks the side (Grove) port's wires: low = UART, high = I2C1, the\n"
               "# keyboard's bus. Left alone it floats high (its default pull-up) until\n"
               "# Linux sets it low about 14 s in, so an unpowered unit on the side port\n"
               "# (M5's Unit ID) sat on the keyboard's bus while the power chip started.\n"
               "[all]\n"
               "gpio=4=op,dl")


def grove_block(text: str) -> str:
    """config.txt with our block at the end (replacing an older copy).
    Only a whole block is removed: a BEGIN line with its END line at most
    a dozen lines below and no other marker between. A damaged marker
    leaves that text alone (an extra copy of gpio=4=op,dl is harmless),
    so nothing of the owner's can ever be cut out."""
    lines = text.split("\n")
    out, i = [], 0
    while i < len(lines):
        if lines[i].strip() == GROVE_BEGIN:
            for j in range(i + 1, min(i + 13, len(lines))):
                if lines[j].strip() == GROVE_BEGIN:
                    break
                if lines[j].strip() == GROVE_END:
                    i = j + 1
                    break
            else:
                out.append(lines[i])
                i += 1
                continue
            if i <= j:                                 # hit another BEGIN: keep this line
                out.append(lines[i])
                i += 1
            continue
        out.append(lines[i])
        i += 1
    text = "\n".join(out)
    return text.rstrip("\n") + f"\n\n{GROVE_BEGIN}\n{GROVE_BLOCK}\n{GROVE_END}\n"


def step_grove_uart_at_boot(config: str | None = None) -> bool:
    """0.8.43: drive GPIO4 low from the first moment of power-on (the boot
    firmware's gpio= line), the same level Linux sets later anyway. Only our
    marked block is written; config.txt is copied to config.txt.insertcoin-bak
    first. Takes effect at the next start; no restart of the app needed."""
    config = config or CONFIG
    try:
        old = open(config).read()
    except OSError:
        return False                                    # not a Raspberry Pi boot partition
    new = grove_block(old)
    if new == old:
        return False
    with open(config + ".insertcoin-bak", "w") as f:
        f.write(old)
    tmp = config + ".insertcoin-new"
    with open(tmp, "w") as f:
        f.write(new)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, config)
    print("config.txt: side port starts on UART (from the next start)", file=sys.stderr)
    return False


STEPS = [step_gpio_for_app, step_ext_bus_rule, step_grove_uart_at_boot]


def needed() -> bool:
    """True if a step is still to do (read-only, any user)."""
    try:
        pwd.getpwnam(APP_USER)
    except KeyError:
        return False
    return not _in_group(APP_USER, "gpio")


def run() -> bool:
    changed = False
    for step in STEPS:
        try:
            changed |= bool(step())
        except Exception as e:  # noqa: BLE001 - one failed step mustn't stop the rest
            print(f"{step.__name__}: {e}", file=sys.stderr)
    return changed


def main() -> int:
    if os.geteuid() != 0:
        print("run as root", file=sys.stderr)
        return 2
    changed = run()
    print("changed" if changed else "ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
