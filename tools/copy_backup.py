#!/usr/bin/env python3
"""
Root helper (via sudo, deploy/cz-backup): copy one card backup, or one
history export (CSV), from the app's folder to the card's FAT partition,
where a PC can read it. `cz-backup paper` moves paper wallets the signer
wrote into the device's paper folder, which only root and the SHARE FILES
service can read (never the card: its FAT side is readable by every
process, the app included), and deletes the signer's copies.

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


def main(argv) -> int:
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
