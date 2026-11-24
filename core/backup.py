"""
Card backup: everything about your setup except the seed.

  - accounts and their names, settings (server, Tor, QR format...)
  - labels of every wallet
  - multisig and inheritance wallets (their descriptors: losing one can
    mean losing access to that multisig)
  - account rules (vault-only Savings)

Not in it: the seed, passphrase, PIN, duress setup, secure-chip binding,
fingerprint, spend cap (set those again yourself), and transaction history
(it comes back from the network).

The file is encrypted (AES-256-GCM) with a key the signer derives from your
seed and passphrase, so only the same seed can open it and nobody who finds
the card learns anything. The same key authenticates it: a file someone
edited, or one made with another seed, won't open at all.

Files go to the app's folder and, through a tiny root helper, to the card's
FAT partition (czwallet-backup/), which a PC can read; on restore any of
those and USB sticks under /media are searched. Five newest kept per seed.
"""
from __future__ import annotations

import base64
import glob
import hashlib
import hmac
import json
import os
import re
import time

from . import config

MAGIC = b"CZWB1"
MAX_BYTES = 4 * 1024 * 1024
KEEP = 5
NAME_RE = re.compile(r"^czw-[0-9a-f]{12}-\d{8}-\d{6}\.bak$")
SETTINGS_KEYS = ("electrum_url", "use_tor", "tor_proxy", "stop_gap", "auto_lock_s",
                 "qr_format", "accounts", "explorer", "sound")


class BackupError(Exception):
    pass


def local_dir() -> str:
    return os.path.join(config.APP_DIR, "backups")


def search_dirs() -> list[str]:
    extra = os.environ.get("CZ_BACKUP_DIRS", "/boot/firmware/czwallet-backup:/media")
    return [local_dir()] + [d for d in extra.split(":") if d]


# ------------------------------------------------------------ signer side
def keys(root_secret: bytes) -> tuple[bytes, str]:
    """(AES key, file tag) from the seed. The tag names this seed's files
    without revealing anything about it."""
    key = hmac.new(root_secret, b"czwallet-backup-v1", hashlib.sha256).digest()
    tag = hashlib.sha256(b"tag" + key).hexdigest()[:12]
    return key, tag


def seal(key: bytes, payload: dict) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    data = json.dumps(payload, separators=(",", ":")).encode()
    if len(data) > MAX_BYTES:
        raise BackupError("backup too large")
    nonce = os.urandom(12)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, data, MAGIC)


def open_sealed(key: bytes, blob: bytes) -> dict:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 12 + 16:
        raise BackupError("not an Insert Coin backup")
    nonce, ct = blob[len(MAGIC):len(MAGIC) + 12], blob[len(MAGIC) + 12:]
    try:
        return json.loads(AESGCM(key).decrypt(nonce, ct, MAGIC))
    except InvalidTag:
        raise BackupError("this backup is from a different seed or passphrase "
                          "(or the file was changed)")


def summary(payload: dict) -> list[str]:
    """What a restore would bring back, for the signer's screen."""
    app = payload.get("app", {})
    sig = payload.get("signer", {})
    accts = app.get("settings", {}).get("accounts", [])
    ms = sig.get("multisig", [])
    inherit = sum(1 for m in ms if m["descriptor"].startswith("wsh(or_d("))
    rules = sig.get("rules", {})
    labels = sum(len(w.get("labels", {})) for w in app.get("labels", []))
    lines = [f"Made {payload.get('made', '?')}"]
    lines.append(f"{len(accts)} extra account{'s' if len(accts) != 1 else ''}"
                 + (": " + ", ".join(a["name"] for a in accts)[:22] if accts else ""))
    if ms:
        lines.append(f"{len(ms) - inherit} multisig, {inherit} inheritance")
    if rules:
        lines.append(f"{len(rules)} vault-only rule{'s' if len(rules) != 1 else ''}")
    lines.append(f"{labels} labels, settings")
    return lines


# --------------------------------------------------------------- app side
def wallet_refs(app) -> list[tuple[str, str, str | None, str | None]]:
    """(ref, descriptor, change_descriptor, role) of every wallet the app knows."""
    out = [("main", app.public["descriptor"], app.public["change_descriptor"], None)]
    for a in app.settings.accounts:
        info = app.signer.account_info(account=a["id"])
        out.append((a["id"], info["descriptor"], info["change_descriptor"], None))
    for e in app.signer.list_multisig():
        out.append((e["id"], e["descriptor"], None, e.get("role")))
    return out


def _wallet(app, desc, change, role):
    from .wallet import WalletService
    return WalletService(app.settings, desc, change, app.fingerprint or "", role=role)


def collect(app) -> dict:
    """The app's part: settings and every wallet's labels."""
    s = app.settings
    labels = []
    for ref, desc, change, role in wallet_refs(app):
        w = app.wallet if app.wallet and _same(app, ref) else _wallet(app, desc, change, role)
        if w.state.get("labels"):
            labels.append({"ref": ref, "labels": dict(w.state["labels"])})
    return {"settings": {k: getattr(s, k) for k in SETTINGS_KEYS}, "labels": labels}


def _same(app, ref) -> bool:
    return (app.wallet_id is None and ref == "main") or app.wallet_id == ref


def apply(app, data: dict) -> None:
    """Put the app's part back. Accounts are merged (nothing removed);
    labels are merged into each wallet's file (existing labels win)."""
    s = app.settings
    st = data.get("settings", {})
    for k in SETTINGS_KEYS:
        if k == "accounts":
            have = {a["id"] for a in s.accounts}
            for a in st.get("accounts", []):
                if isinstance(a, dict) and a.get("id") not in have:
                    s.accounts.append({"id": str(a["id"]), "name": str(a.get("name", ""))[:16]})
        elif k in st:
            setattr(s, k, st[k])
    s.save()
    by_ref = {w["ref"]: w["labels"] for w in data.get("labels", [])}
    for ref, desc, change, role in wallet_refs(app):
        if ref not in by_ref:
            continue
        w = app.wallet if app.wallet and _same(app, ref) else _wallet(app, desc, change, role)
        merged = dict(by_ref[ref])
        merged.update(w.state.get("labels", {}))
        w.state["labels"] = merged
        w._save()


def write(blob: bytes, tag: str) -> str:
    """Save to the app's folder (and the card's FAT side via the helper).
    Returns the file name."""
    name = f"czw-{tag}-{time.strftime('%Y%m%d-%H%M%S')}.bak"
    d = local_dir()
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(blob)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, os.path.join(d, name))
    for old in sorted(glob.glob(os.path.join(d, f"czw-{tag}-*.bak")))[:-KEEP]:
        os.remove(old)
    _copy_to_card(name)
    return name


def _copy_to_card(name: str) -> None:
    """Root helper: copy one backup file to the card's FAT partition."""
    import subprocess
    helper = "/usr/local/sbin/cz-backup"
    if os.environ.get("CZ_SIM") or not os.path.exists(helper):
        return
    r = subprocess.run(["sudo", "-n", helper, name], capture_output=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError("Saved on this device, but it couldn't be copied to the card.")


def find(tag: str | None = None) -> list[str]:
    """Backup files, newest first. With tag: only this seed's."""
    seen, out = set(), []
    for d in search_dirs():
        pattern = "**/czw-*.bak" if d.startswith("/media") else "czw-*.bak"
        for p in glob.glob(os.path.join(d, pattern), recursive=True):
            name = os.path.basename(p)
            if not NAME_RE.match(name) or (tag and not name.startswith(f"czw-{tag}-")):
                continue
            if name in seen or os.path.getsize(p) > MAX_BYTES + 64:
                continue
            seen.add(name)
            out.append(p)
    return sorted(out, key=lambda p: os.path.basename(p)[-19:], reverse=True)


def copies(tag: str) -> list[tuple[str, str]]:
    """Every copy of this seed's backups, newest first, as (where, path):
    DEVICE, CARD or USB. Same-named copies in different places all count,
    so VERIFY BACKUP checks each one."""
    out = []
    for d in search_dirs():
        where = "DEVICE" if d == local_dir() else "USB" if d.startswith("/media") else "CARD"
        pattern = "**/czw-*.bak" if d.startswith("/media") else "czw-*.bak"
        for p in glob.glob(os.path.join(d, pattern), recursive=True):
            name = os.path.basename(p)
            if NAME_RE.match(name) and name.startswith(f"czw-{tag}-"):
                out.append((where, p))
    order = {"DEVICE": 0, "CARD": 1, "USB": 2}
    return sorted(out, key=lambda wp: (os.path.basename(wp[1])[-19:], -order[wp[0]]),
                  reverse=True)


def read(path: str) -> bytes:
    with open(path, "rb") as f:
        data = f.read(MAX_BYTES + 65)
    if len(data) > MAX_BYTES + 64:
        raise BackupError("file too large")
    return data


def b64(blob: bytes) -> str:
    return base64.b64encode(blob).decode()


def unb64(text: str) -> bytes:
    return base64.b64decode(text, validate=True)
