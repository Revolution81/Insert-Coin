"""
Transaction IDs out of the wallet: block-explorer links (mempool.space by
default) and a CSV of the whole history for the microSD card.

The CSV goes to the app's folder, then (on the device) the root helper
cz-backup copies it to the card's PC-readable side, in InsertCoin-exports/.
It holds no keys, but it does list every payment: anyone with the card
can read it.
"""
from __future__ import annotations

import csv
import io
import os
import re
import time

from core import config

DEFAULT_EXPLORER = "https://mempool.space"
# path for each network on mempool.space-style explorers
NET_PATH = {"bitcoin": "", "testnet4": "/testnet4", "signet": "/signet", "regtest": None}
HEADER = ["date", "status", "amount_sats", "amount_btc", "fee_sats", "txid", "label", "link"]
NAME = re.compile(r"^InsertCoin-history-[0-9a-z-]{1,40}-\d{8}-\d{6}\.csv$")
KEEP = 10


def tx_url(network: str, txid: str, explorer: str | None = None) -> str | None:
    """https://mempool.space/signet/tx/<txid>, or None (regtest has no explorer)."""
    path = NET_PATH.get(network)
    if path is None:
        return None
    base = (explorer or DEFAULT_EXPLORER).strip().rstrip("/")
    if not re.match(r"^https?://", base):
        base = "https://" + base
    return f"{base}{path}/tx/{txid}"


def _cell(text: str) -> str:
    """Keep spreadsheet programs from running a label as a formula."""
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def to_csv(txs, label_of, network: str, explorer: str | None = None) -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(HEADER)
    for tx in txs:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(tx.timestamp)) if tx.timestamp else ""
        w.writerow([
            when,
            "confirmed" if tx.confirmed else "pending",
            str(tx.delta),
            f"{tx.delta / 1e8:+.8f}",
            "" if tx.fee is None else str(tx.fee),
            tx.txid,
            _cell(label_of(tx.txid) or ""),
            tx_url(network, tx.txid, explorer) or "",
        ])
    return out.getvalue()


def local_dir() -> str:
    return os.path.join(config.APP_DIR, "exports")


def save(text: str, tag: str) -> str:
    """Write the CSV; returns its file name. tag: lower-case letters, digits, '-'."""
    tag = re.sub(r"[^0-9a-z-]+", "-", tag.lower()).strip("-")[:40] or "wallet"
    name = f"InsertCoin-history-{tag}-{time.strftime('%Y%m%d-%H%M%S')}.csv"
    assert NAME.match(name)
    d = local_dir()
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, os.path.join(d, name))
    mine = sorted(n for n in os.listdir(d) if n.startswith(f"InsertCoin-history-{tag}-")
                  and n.endswith(".csv"))
    for old in mine[:-KEEP]:
        os.remove(os.path.join(d, old))
    _copy_to_card(name)
    return name


def _copy_to_card(name: str) -> None:
    import subprocess
    helper = "/usr/local/sbin/cz-backup"
    if os.environ.get("CZ_SIM") or not os.path.exists(helper):
        return
    r = subprocess.run(["sudo", "-n", helper, name], capture_output=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError("Saved on this device, but it couldn't be copied to the card.")
