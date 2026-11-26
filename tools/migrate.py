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


STEPS = [step_gpio_for_app, step_ext_bus_rule]


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
