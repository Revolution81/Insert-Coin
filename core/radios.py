"""
Radio and network control for Vault / Online modes.

Vault mode = Wi-Fi and Bluetooth blocked with rfkill, Ethernet link down.
The device boots into Vault mode (deploy/czwallet-vault.service runs
`python3 -m core.radios vault` before anything else starts).

Wi-Fi joining uses NetworkManager (nmcli), the default on Raspberry Pi OS
Bookworm. These calls need permission: the install script adds a polkit rule
so the app user may run them.
"""
from __future__ import annotations

import os
import time
import subprocess
import sys

ETH = "eth0"


def _run(*cmd, check=True, timeout=30) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        if check:
            raise RuntimeError(f"{cmd[0]} is not installed")
        return ""
    if check and r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip() or f"{cmd[0]} failed")
    return r.stdout


HELPER = "/usr/local/sbin/cz-radio"     # root-owned, allowed via sudoers


SIM = bool(os.environ.get("CZ_SIM"))     # desktop simulator: never touch the PC's network


def _as_root(mode: str) -> None:
    """The app runs unprivileged; flipping radios goes through one tiny
    root helper that accepts only 'vault' or 'online'."""
    if SIM:
        os.environ["CZ_FORCE_ONLINE"] = "1" if mode == "online" else "0"
        return
    if os.geteuid() == 0:
        _apply(mode)
    elif os.path.exists(HELPER):
        _run("sudo", "-n", HELPER, mode)
    else:
        _apply(mode)   # dev machine: best effort, failures ignored


def _apply(mode: str) -> None:
    if mode == "vault":
        _run("nmcli", "networking", "off", check=False)
        _run("rfkill", "block", "wifi", check=False)
        _run("rfkill", "block", "bluetooth", check=False)
        _run("ip", "link", "set", ETH, "down", check=False)
    else:
        _run("rfkill", "unblock", "wifi", check=False)
        _run("ip", "link", "set", ETH, "up", check=False)
        _run("nmcli", "networking", "on", check=False)
        _run("nmcli", "radio", "wifi", "on", check=False)


def vault_mode() -> None:
    _as_root("vault")


def online_mode() -> None:
    _as_root("online")


def wifi_scan() -> list[dict]:
    """[{ssid, signal 0-100, secure}] strongest first, de-duplicated."""
    if SIM:
        return [{"ssid": "Simulated Wi-Fi", "signal": 88, "secure": True},
                {"ssid": "Coffee Shop", "signal": 41, "secure": False}]
    # --rescan yes waits for a fresh scan (a plain "rescan" then "list" right
    # after going Online came back empty: the radio had only just woken)
    out = ""
    for attempt in range(3):           # the radio may still be waking up
        out = _run("nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list",
                   "--rescan", "yes", check=False)
        if out.strip():
            break
        time.sleep(2)
    nets: dict[str, dict] = {}
    for line in out.splitlines():
        parts = line.replace("\\:", "\x00").split(":")
        if len(parts) < 3 or not parts[0]:
            continue
        ssid = parts[0].replace("\x00", ":")
        sig = int(parts[1] or 0)
        if ssid not in nets or nets[ssid]["signal"] < sig:
            nets[ssid] = {"ssid": ssid, "signal": sig, "secure": parts[2] not in ("", "--")}
    return sorted(nets.values(), key=lambda n: -n["signal"])


def _saved_wifi(ssid: str) -> str | None:
    """Name of a saved NetworkManager profile for this network, if any (the
    imager's setup, or one the wallet made earlier)."""
    out = _run("nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", check=False)
    for line in out.splitlines():
        name, _, kind = line.replace("\\:", "\x00").rpartition(":")
        name = name.replace("\x00", ":")
        if kind != "802-11-wireless" or not name:
            continue
        if _run("nmcli", "-g", "802-11-wireless.ssid", "connection", "show", name,
                check=False).strip() == ssid:
            return name
    return None


def wifi_connect(ssid: str, password: str | None) -> None:
    """Join a network. "nmcli device wifi connect" fails on some
    NetworkManager versions with "key-mgmt: property is missing" (and when a
    saved profile exists), so profiles are made and started explicitly."""
    if SIM:
        return
    if wifi_current() == ssid:
        return
    name = _saved_wifi(ssid)
    if name is None:
        name = ssid
        _run("nmcli", "connection", "add", "type", "wifi", "con-name", name,
             "ssid", ssid, "connection.autoconnect", "yes")
        if not password:
            _run("nmcli", "connection", "modify", name, "remove", "802-11-wireless-security")
    if password:
        _run("nmcli", "connection", "modify", name,
             "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password)
    _run("nmcli", "connection", "up", name, timeout=45)


def wifi_current() -> str | None:
    if SIM:
        return "Simulated Wi-Fi"
    out = _run("nmcli", "-t", "-f", "ACTIVE,SSID", "device", "wifi", check=False)
    for line in out.splitlines():
        if line.startswith("yes:"):
            return line[4:]
    return None


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("vault", "online"):
        sys.exit("usage: radios.py vault|online")
    _apply(sys.argv[1])


def wired_link(root: str = "/sys/class/net") -> str | None:
    """A wired network that's plugged in and up (the Cardputer Zero's
    SR9900A USB Ethernet, or a USB network adapter), else None. Read from
    sysfs only. CZ_SIM_LAN names one in the simulator."""
    sim = os.environ.get("CZ_SIM_LAN")
    if sim is not None:
        return sim or None
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return None
    for name in names:
        base = os.path.join(root, name)
        if name == "lo" or os.path.exists(os.path.join(base, "wireless")) or \
                not os.path.exists(os.path.join(base, "device")):       # real hardware only
            continue
        try:
            with open(os.path.join(base, "type")) as f:
                if f.read().strip() != "1":                          # ARPHRD_ETHER
                    continue
            with open(os.path.join(base, "carrier")) as f:
                if f.read().strip() != "1":
                    continue
            with open(os.path.join(base, "operstate")) as f:
                if f.read().strip() not in ("up", "unknown"):
                    continue
        except OSError:                                              # down: carrier unreadable
            continue
        return name
    return None
