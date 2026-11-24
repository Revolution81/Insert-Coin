"""
SETUP › FILES › SAVE TO USB STICK: the wallet's files onto a USB stick in
the USB-A port (HOST mode, the normal one: nothing to switch or install).

The app stages files in its own folder (APP_DIR/usbstick): history CSVs,
the open wallet's PSBT waiting for co-signers, the last PSBT signed here,
signed transactions in the outbox, BIP329 labels and the watch-only
descriptor. Public data only: no seed, no keys, no paper wallets. The root
helper (cz-backup usbstick, tools/copy_backup.py) mounts the stick
(FAT or exFAT, no exec), writes them into InsertCoin/, flushes and
unmounts. Nothing on the stick is read or run. In the simulator the files
stay in simdata/app/usbstick.
"""
from __future__ import annotations

import base64
import glob
import os
import re
import shutil
import subprocess

from core import config

HELPER = "/usr/local/sbin/cz-backup"
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
README = """Insert Coin: files from your wallet (read only)

history-*.csv      every transaction: date, amount, fee, TXID, label, link
*.psbt             PSBTs: open in Sparrow (File > Open Transaction)
signed-*.txn       signed transactions waiting to be sent (hex): broadcast
                   from any wallet or a block explorer's "broadcast" page
labels-*.jsonl     your labels (BIP329), for Sparrow and other wallets
watch-only-*.txt   the wallet's descriptor: watch it, can't spend

Nothing here can spend your coins. It does show your history and
balances: treat it like a bank statement.
"""


class USBStickError(Exception):
    pass


def stage_dir() -> str:
    return os.path.join(config.APP_DIR, "usbstick")


def _tag(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z-]+", "-", text).strip("-")[:24] or "wallet"


def prepare(app) -> list[tuple[str, int]]:
    """Fill the staging folder (emptied first); returns (name, size) of each file."""
    d = stage_dir()
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, mode=0o750)
    out: dict[str, bytes] = {"README.txt": README.encode()}
    from core import history_export as hx
    for p in sorted(glob.glob(os.path.join(hx.local_dir(), "InsertCoin-history-*.csv"))):
        name = os.path.basename(p).replace("InsertCoin-history-", "history-")
        with open(p, "rb") as f:
            out[name] = f.read()
    w = getattr(app, "wallet", None)
    tag = _tag(f"{getattr(app, 'fingerprint', '') or ''}-{getattr(app, 'wallet_name', '')}")
    if w is not None:
        pending = (getattr(w, "state", None) or {}).get("pending_psbt")
        if pending:
            out[f"needs-signatures-{tag}.psbt"] = base64.b64decode(pending)
        try:
            labels = w.export_labels()
        except Exception:  # noqa: BLE001 - no labels then
            labels = ""
        if labels:
            out[f"labels-{tag}.jsonl"] = labels.encode()
        try:
            if getattr(w, "kind", "") == "single":
                from ui.flow import watch_only_texts
                desc, _ = watch_only_texts(app.account or app.public)
            else:
                desc = getattr(w, "descriptor", "") or ""
            if desc:
                out[f"watch-only-{tag}.txt"] = (desc + "\n").encode()
        except Exception:  # noqa: BLE001
            pass
    last = getattr(app, "last_signed_psbt", None)
    if last:
        out["last-signed.psbt"] = base64.b64decode(last)
    for p in sorted(glob.glob(os.path.join(getattr(app, "outbox_dir", ""), "*.hex"))):
        txid = os.path.basename(p)[:-4]
        if re.fullmatch(r"[0-9a-f]{64}", txid):
            with open(p, "rb") as f:
                out[f"signed-{txid[:16]}.txn"] = f.read()
    files = []
    for name, data in out.items():
        if not NAME.match(name):
            continue
        with open(os.path.join(d, name), "wb") as f:
            f.write(data)
        files.append((name, len(data)))
    return files


def on_device() -> bool:
    return not os.environ.get("CZ_SIM") and os.path.exists(HELPER)


def save() -> str:
    """Write the staged files to the stick; returns the stick's name.
    Raises USBStickError("no-stick" | "no-fs" | other) when it can't."""
    if not on_device():
        return "simdata/app/usbstick"
    try:
        r = subprocess.run(["sudo", "-n", HELPER, "usbstick"], capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        raise USBStickError(str(e)[:60])
    lines = r.stdout.decode(errors="replace").strip().splitlines()
    last = lines[-1] if lines else f"exit {r.returncode}"
    if r.returncode == 0 and last.startswith("saved "):
        return last.split(" ", 2)[2] if last.count(" ") >= 2 else "the stick"
    raise USBStickError(last.split()[0] if last else "failed")


def clear() -> None:
    if on_device():
        shutil.rmtree(stage_dir(), ignore_errors=True)
