"""
SECURE NOTES & PASSWORDS: small secrets kept by the signer.

Each entry is a note (title + text) or a password (title, user name, site,
password, note). The list lives in the signer's folder, encrypted with
AES-256-GCM under a key from the seed (like the multisig registry), so each
seed sees only its own and a copied card shows nothing. Entries are shown
only on the signer's screen; the app never receives them. Card backups
carry them, sealed with the seed's backup key.

BIP-85 passwords (application 707764' base64 and 707785' base85) are
derived from the seed instead of stored: the same number always gives the
same password, so they come back from the seed words alone.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import string
import time

MAX_ENTRIES = 100
LIMITS = {"title": 32, "user": 64, "site": 64, "password": 128, "text": 1000}
KINDS = ("note", "password")


class NoteError(Exception):
    pass


def clean(entry: dict) -> dict:
    """A checked copy of one entry: known fields only, sizes capped,
    printable text. NoteError if it isn't usable."""
    if not isinstance(entry, dict):
        raise NoteError("bad entry")
    kind = entry.get("kind")
    if kind not in KINDS:
        raise NoteError("a note or a password")
    out = {"kind": kind}
    for field, limit in LIMITS.items():
        v = entry.get(field, "")
        if not isinstance(v, str):
            raise NoteError(f"bad {field}")
        v = "".join(ch for ch in v if ch.isprintable())
        if len(v) > limit:
            raise NoteError(f"{field} is too long (max {limit})")
        if v:
            out[field] = v
    if not out.get("title"):
        raise NoteError("give it a title")
    if kind == "password" and not out.get("password"):
        raise NoteError("no password to keep")
    made = entry.get("made")
    out["made"] = int(made) if isinstance(made, int) and made > 0 else int(time.time())
    return out


def merge(mine: list[dict], theirs: list[dict]) -> tuple[list[dict], int]:
    """Add entries from a backup that aren't here yet (same content = same
    entry). Never removes or changes one. Returns (list, number added)."""
    have = {_fingerprint(e) for e in mine}
    out, added = list(mine), 0
    for e in theirs:
        try:
            e = clean(e)
        except NoteError:
            continue
        f = _fingerprint(e)
        if f not in have and len(out) < MAX_ENTRIES:
            out.append(e)
            have.add(f)
            added += 1
    return out, added


def _fingerprint(e: dict) -> str:
    return hashlib.sha256(repr(sorted((k, v) for k, v in e.items() if k != "made")).encode()).hexdigest()


# ------------------------------------------------------------ passwords
PW_CHARS = string.ascii_letters + string.digits + "!#$%&*+-=?@^_~"


def random_password(length: int = 20) -> str:
    """A fresh random password from the kernel's generator."""
    if not 8 <= length <= 64:
        raise NoteError("8 to 64 characters")
    while True:
        pw = "".join(secrets.choice(PW_CHARS) for _ in range(length))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw)
                and any(c.isdigit() for c in pw)):
            return pw


def bip85_password(root, length: int, index: int, kind: str = "base64") -> str:
    """BIP-85 PWD BASE64 (m/83696968'/707764'/{len}'/{index}', 20-86
    characters) or PWD BASE85 (m/83696968'/707785'/{len}'/{index}', 10-80)."""
    if not isinstance(index, int) or not 0 <= index < 2 ** 31:
        raise NoteError("bad index")
    if kind == "base64":
        if not 20 <= length <= 86:
            raise NoteError("20 to 86 characters")
        k = root.derive(f"m/83696968h/707764h/{length}h/{index}h")
        ent = hmac.new(b"bip-entropy-from-k", k.secret, hashlib.sha512).digest()
        return base64.b64encode(ent).decode()[:length]
    if kind == "base85":
        if not 10 <= length <= 80:
            raise NoteError("10 to 80 characters")
        k = root.derive(f"m/83696968h/707785h/{length}h/{index}h")
        ent = hmac.new(b"bip-entropy-from-k", k.secret, hashlib.sha512).digest()
        return _b85(ent)[:length]
    raise NoteError("base64 or base85")


def _b85(data: bytes) -> str:
    """Base85 with RFC 1924's alphabet, as BIP-85 specifies (Python's
    base64.b85encode uses the same alphabet)."""
    return base64.b85encode(data).decode()
