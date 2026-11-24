#!/usr/bin/env python3
"""
Root helper (via sudo, deploy/cz-usbdisk): SETUP › FILES › USB DISK.

Makes the device show up on a PC as a small READ-ONLY USB stick holding
what the app staged in its own folder (history CSVs, PSBTs, signed
transactions, labels, watch-only descriptors: never keys), using Linux's
USB gadget (configfs mass storage). Needs the Cardputer Zero's USB switch
on SLAVE, the PC on the right-hand USB-C, and the dwc2 driver in OTG mode
(install.sh --usb-disk).

    cz-usbdisk start | stop | status

start copies only plain files (no links, nothing hidden) with checked
names and sizes from the app's staging folder into a fresh FAT image in
/run, then attaches it read-only, so the PC can't write anything back.
"""
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

SRC = "/var/lib/czwallet/app/usbdisk"
IMG = "/run/czwallet-usbdisk.img"
GADGET = "/sys/kernel/config/usb_gadget/insertcoin"
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
MAX_FILES, MAX_FILE, MAX_TOTAL = 200, 8 * 1024 * 1024, 48 * 1024 * 1024


def udc() -> str | None:
    try:
        names = sorted(os.listdir("/sys/class/udc"))
    except OSError:
        return None
    return names[0] if names else None


def _w(path: str, value: str) -> None:
    with open(path, "w") as f:
        f.write(value)


def staged() -> list[tuple[str, bytes]]:
    """The staging folder's files, opened without following links."""
    out, total = [], 0
    for name in sorted(os.listdir(SRC))[:MAX_FILES + 1]:
        if not NAME.match(name):
            continue
        try:
            fd = os.open(os.path.join(SRC, name), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:                                # a link, or gone: skip it
            continue
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE:
                continue
            data = os.read(fd, MAX_FILE + 1)
        finally:
            os.close(fd)
        total += len(data)
        if total > MAX_TOTAL or len(out) >= MAX_FILES:
            break
        out.append((name, data))
    return out


def make_image(files: list[tuple[str, bytes]]) -> None:
    size = max(4, (sum(len(d) for _, d in files) * 5 // 4) // (1024 * 1024) + 4)   # MiB
    tmp = IMG + ".new"
    with open(tmp, "wb") as f:
        f.truncate(size * 1024 * 1024)
    subprocess.run(["mkfs.vfat", "-n", "INSERTCOIN", tmp], check=True, capture_output=True)
    mnt = tempfile.mkdtemp(prefix="czw-usbdisk-")
    try:
        subprocess.run(["mount", "-o", "loop,nosuid,nodev,noexec", tmp, mnt], check=True,
                       capture_output=True)
        try:
            for name, data in files:
                with open(os.path.join(mnt, name), "wb") as f:
                    f.write(data)
        finally:
            subprocess.run(["umount", mnt], check=True, capture_output=True)
    finally:
        os.rmdir(mnt)
    os.chmod(tmp, 0o600)
    os.replace(tmp, IMG)


def stop() -> None:
    if os.path.isdir(GADGET):
        try:
            _w(f"{GADGET}/UDC", "")
        except OSError:
            pass
        for p in (f"{GADGET}/configs/c.1/mass_storage.usb0",):
            if os.path.islink(p):
                os.remove(p)
        for d in (f"{GADGET}/configs/c.1/strings/0x409", f"{GADGET}/configs/c.1",
                  f"{GADGET}/functions/mass_storage.usb0", f"{GADGET}/strings/0x409", GADGET):
            try:
                os.rmdir(d)
            except OSError:
                pass
    if os.path.exists(IMG):
        os.remove(IMG)


def start() -> None:
    port = udc()
    if port is None:
        print("no-udc")
        sys.exit(3)
    stop()
    make_image(staged())
    subprocess.run(["modprobe", "libcomposite"], capture_output=True)
    os.makedirs(f"{GADGET}/strings/0x409")
    _w(f"{GADGET}/idVendor", "0x1d6b")             # Linux Foundation
    _w(f"{GADGET}/idProduct", "0x0104")            # multifunction composite gadget
    _w(f"{GADGET}/strings/0x409/manufacturer", "Insert Coin")
    _w(f"{GADGET}/strings/0x409/product", "Insert Coin files (read only)")
    _w(f"{GADGET}/strings/0x409/serialnumber", "0")
    os.makedirs(f"{GADGET}/configs/c.1/strings/0x409")
    _w(f"{GADGET}/configs/c.1/strings/0x409/configuration", "read-only disk")
    lun = f"{GADGET}/functions/mass_storage.usb0/lun.0"
    os.makedirs(f"{GADGET}/functions/mass_storage.usb0", exist_ok=True)
    _w(f"{lun}/ro", "1")
    _w(f"{lun}/removable", "1")
    _w(f"{lun}/cdrom", "0")
    _w(f"{lun}/file", IMG)
    os.symlink(f"{GADGET}/functions/mass_storage.usb0", f"{GADGET}/configs/c.1/mass_storage.usb0")
    _w(f"{GADGET}/UDC", port)
    print("on")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) == 2 else ""
    if cmd == "start":
        if not shutil.which("mkfs.vfat"):
            print("no-mkfs")
            sys.exit(4)
        try:
            start()
        except Exception as e:  # noqa: BLE001 - undo, then report
            stop()
            print(f"failed: {e}"[:200])
            sys.exit(1)
    elif cmd == "stop":
        stop()
        print("off")
    elif cmd == "status":
        print("on" if os.path.isdir(GADGET) and os.path.exists(IMG) else
              ("off" if udc() else "no-udc"))
    else:
        print("usage: cz-usbdisk start|stop|status", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
