#!/usr/bin/env python3
"""
Root helper (via sudo, deploy/cz-backup): copy one card backup, or one
history export (CSV), from the app's folder to the card's FAT partition,
where a PC can read it. `cz-backup paper` moves paper wallets the signer
wrote into the device's paper folder, which only root and the SHARE FILES
service can read (never the card: its FAT side is readable by every
process, the app included), and deletes the signer's copies.
`cz-backup usbstick` copies what SAVE TO USB STICK staged (history,
PSBTs, signed transactions, labels: never keys) onto a FAT or exFAT USB
stick, mounted without exec, and never reads anything from it.

Takes only a file name, checks it, opens it without following symlinks
(the app owns the folder it comes from), checks it really is a sealed
backup, and keeps the five newest per seed. Reads nothing else.
"""
import os
import re
import stat
import sys

SRC = "/var/lib/czwallet/app/backups"
DST = "/boot/firmware/czwallet-backup"
NAME = re.compile(r"^czw-([0-9a-f]{12})-\d{8}-\d{6}\.bak$")
# history exports (core/history_export.py): plain CSV, no keys
EXPORT_SRC = "/var/lib/czwallet/app/exports"
EXPORT_DST = "/boot/firmware/InsertCoin-exports"
EXPORT = re.compile(r"^InsertCoin-history-([0-9a-z-]{1,40})-\d{8}-\d{6}\.csv$")
EXPORT_MAX = 4 * 1024 * 1024
EXPORT_HEAD = b"date,status,amount_sats,"
EXPORT_KEEP = 10
# paper wallets (core/paper.py), written by the signer
PAPER_SRC = "/var/lib/czwallet/signer/paper"
PAPER_DST = "/var/lib/czwallet/paper"       # root:czshare 2770, see install.sh
PAPER_OLD = "/boot/firmware/InsertCoin-paper"  # where 0.8.7-0.8.14 put them
PAPER_GROUP = "czshare"
PAPER = re.compile(r"^[a-zA-Z0-9]{26,90}\.(txt|pdf)$")
PAPER_MAX = 2 * 1024 * 1024
MAX = 4 * 1024 * 1024 + 64
MAGIC = b"CZWB1"
KEEP = 5


def _read(src: str, name: str, limit: int) -> bytes | None:
    d = os.open(src, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=d)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
        os.close(fd)
        return None
    with os.fdopen(fd, "rb") as f:
        data = f.read(limit + 1)
    return None if len(data) > limit else data


def _place(dst: str, name: str, data: bytes) -> None:
    os.makedirs(dst, exist_ok=True)
    tmp = os.path.join(dst, name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, os.path.join(dst, name))


def export(name: str) -> int:
    data = _read(EXPORT_SRC, name, EXPORT_MAX)
    if data is None or not data.startswith(EXPORT_HEAD):
        print("not a history export", file=sys.stderr)
        return 2
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        print("not a history export", file=sys.stderr)
        return 2
    _place(EXPORT_DST, name, data)
    prefix = f"InsertCoin-history-{EXPORT.match(name).group(1)}-"
    mine = sorted(n for n in os.listdir(EXPORT_DST) if EXPORT.match(n) and n.startswith(prefix))
    for old in mine[:-EXPORT_KEEP]:
        os.remove(os.path.join(EXPORT_DST, old))
    print(f"copied {name}")
    return 0


def _place_paper(name: str, data: bytes) -> None:
    """Into the paper folder, readable by root and the SHARE FILES service
    only: mode 0640, group czshare."""
    os.makedirs(PAPER_DST, mode=0o2770, exist_ok=True)
    tmp = os.path.join(PAPER_DST, name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o640)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    try:
        import grp
        os.chown(tmp, 0, grp.getgrnam(PAPER_GROUP).gr_gid)
    except (KeyError, PermissionError, ImportError):
        pass                                   # tests and PCs: no czshare group
    os.chmod(tmp, 0o640)
    os.replace(tmp, os.path.join(PAPER_DST, name))


def paper() -> int:
    """Move every paper wallet the signer wrote (<address>.txt / .pdf) into
    the paper folder and delete the signer's copies. Any left on the card by
    an older version are moved in too, so no key stays where the app can
    read it."""
    moved = 0
    for src in (PAPER_SRC, PAPER_OLD):
        try:
            names = os.listdir(src)
        except (FileNotFoundError, NotADirectoryError):
            continue
        for name in sorted(names):
            if not PAPER.match(name):
                continue
            data = _read(src, name, PAPER_MAX)
            if data is None:
                continue
            _place_paper(name, data)
            os.remove(os.path.join(src, name))
            moved += 1
        if src == PAPER_OLD:
            try:
                os.rmdir(src)                  # gone from the card if now empty
            except OSError:
                pass
    print(f"moved {moved} paper wallet files")
    return 0


def wipe() -> int:
    """WIPE DEVICE / wipe PIN: paper wallets (device folder, and any an older
    version left on the card) and history exports go. Encrypted card backups
    stay: they're useless without the seed and are how you restore."""
    gone = 0
    for folder, pattern in ((PAPER_DST, PAPER), (PAPER_OLD, PAPER), (EXPORT_DST, EXPORT)):
        try:
            names = os.listdir(folder)
        except (FileNotFoundError, NotADirectoryError):
            continue
        for name in names:
            path = os.path.join(folder, name)
            if not (pattern.match(name) or name.endswith(".tmp")) or os.path.islink(path):
                continue
            try:
                size = os.path.getsize(path)
                with open(path, "r+b") as f:
                    f.write(os.urandom(max(size, 1)))
                    f.flush()
                    os.fsync(f.fileno())
                os.remove(path)
                gone += 1
            except OSError:
                pass
    print(f"wiped {gone} files")
    return 0


# SETUP › FILES › SAVE TO USB STICK (core/usbstick.py): what the app staged
STICK_SRC = "/var/lib/czwallet/app/usbstick"
STICK_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
STICK_DIR = "InsertCoin"
STICK_MNT = "/run/czwallet-usbstick"
STICK_FS = ("vfat", "exfat")
STICK_FILE_MAX, STICK_TOTAL_MAX, STICK_FILES_MAX = 8 * 1024 * 1024, 48 * 1024 * 1024, 200


def _usb_partitions() -> list[str]:
    """/dev/sdXN of USB drives (never the microSD card, mmcblk)."""
    out = []
    for dev in sorted(os.listdir("/sys/block")):
        if not dev.startswith("sd"):
            continue
        if "/usb" not in os.path.realpath(os.path.join("/sys/block", dev)):
            continue
        parts = sorted(p for p in os.listdir(os.path.join("/sys/block", dev))
                       if p.startswith(dev))
        out += [f"/dev/{p}" for p in parts] or [f"/dev/{dev}"]       # unpartitioned stick
    return out


def _mounted_at(dev: str) -> str | None:
    with open("/proc/mounts") as f:
        for line in f:
            parts = line.split()
            if parts[0] == dev:
                return parts[1].replace("\\040", " ")
    return None


def usbstick() -> int:
    """Copy the staged files into InsertCoin/ on a USB stick (FAT or exFAT
    only; mounted without exec, suid or device files; nothing on the stick
    is ever read or run), flush, and unmount it if this mounted it."""
    import subprocess
    files, total = [], 0
    try:
        names = sorted(os.listdir(STICK_SRC))
    except FileNotFoundError:
        names = []
    for name in names[:STICK_FILES_MAX]:
        if not STICK_NAME.match(name):
            continue
        try:
            data = _read(STICK_SRC, name, STICK_FILE_MAX)
        except OSError:
            continue
        if data is None or total + len(data) > STICK_TOTAL_MAX:
            continue
        total += len(data)
        files.append((name, data))
    target = None
    for part in _usb_partitions():
        r = subprocess.run(["blkid", "-o", "value", "-s", "TYPE", part],
                           capture_output=True, text=True)
        if r.stdout.strip() in STICK_FS:
            target = (part, r.stdout.strip())
            break
    if target is None:
        print("no-stick" if not _usb_partitions() else "no-fs")
        return 3
    part, fs = target
    where, ours = _mounted_at(part), False
    if where is None:
        os.makedirs(STICK_MNT, mode=0o700, exist_ok=True)
        r = subprocess.run(["mount", "-t", fs, "-o", "nosuid,nodev,noexec,noatime", part,
                            STICK_MNT], capture_output=True, text=True)
        if r.returncode != 0:
            print("mount-failed " + r.stderr.strip()[:80])
            return 4
        where, ours = STICK_MNT, True
    try:
        dst = os.path.join(where, STICK_DIR)
        if os.path.islink(dst):
            print("bad-stick")
            return 4
        for name, data in files:
            _place(dst, name, data)
        subprocess.run(["sync"], check=False)
    finally:
        if ours:
            subprocess.run(["umount", STICK_MNT], check=False)
    label = subprocess.run(["blkid", "-o", "value", "-s", "LABEL", part],
                           capture_output=True, text=True).stdout.strip()
    print(f"saved {len(files)} {label or part}")
    return 0


def migrate() -> int:
    """The system steps of tools/migrate.py, for a device whose last update
    was installed by an older helper that didn't run them. If anything
    changed, the app is restarted so it picks up its new groups."""
    import subprocess
    sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
    import migrate as m
    changed = m.run()
    if changed:
        subprocess.run(["systemd-run", "--on-active=3", "systemctl", "restart", "czwallet"],
                       check=False)
    print("changed" if changed else "ok")
    return 0


def addons() -> int:
    """5 V on for the Grove port and the EXT header (tools/addon_power.py)."""
    sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
    import addon_power
    import hwdiag
    results = addon_power.request_each()
    for line in results:
        print(f"add-on power: {line}")
    try:
        for line in hwdiag.report():                   # read-only, for the boot log
            print(line)
    except Exception as e:  # noqa: BLE001
        print(f"hwdiag: {e}")
    return 0 if all(r.endswith(": on") for r in results) else 1


def main(argv) -> int:
    if len(argv) == 2 and argv[1] == "addons":
        return addons()
    if len(argv) == 2 and argv[1] == "migrate":
        return migrate()
    if len(argv) == 2 and argv[1] == "usbstick":
        return usbstick()
    if len(argv) == 2 and argv[1] == "paper":
        return paper()
    if len(argv) == 2 and argv[1] == "wipe":
        return wipe()
    if len(argv) == 2 and EXPORT.match(argv[1]):
        return export(argv[1])
    if len(argv) != 2 or not NAME.match(argv[1]):
        print("bad name", file=sys.stderr)
        return 2
    name = argv[1]
    d = os.open(SRC, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=d)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_size > MAX:
        print("not a backup file", file=sys.stderr)
        return 2
    with os.fdopen(fd, "rb") as f:
        data = f.read(MAX + 1)
    if len(data) > MAX or not data.startswith(MAGIC):
        print("not a backup file", file=sys.stderr)
        return 2
    os.makedirs(DST, exist_ok=True)
    tmp = os.path.join(DST, name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, os.path.join(DST, name))
    tag = NAME.match(name).group(1)
    mine = sorted(n for n in os.listdir(DST) if NAME.match(n) and n.startswith(f"czw-{tag}-"))
    for old in mine[:-KEEP]:
        os.remove(os.path.join(DST, old))
    print(f"copied {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
