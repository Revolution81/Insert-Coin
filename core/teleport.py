"""
KEY TELEPORT, as on the COLDCARD Q (docs/key-teleport.md in Coinkite's
firmware): send a seed, notes or a PSBT to another device by QR, with nothing
in between able to read it.

1. The receiver makes a one-time key pair and shows its public key as a BBQr
   (type R), weakly encrypted with an 8-digit RECEIVER PASSWORD that goes
   by another channel (a phone call).
2. The sender scans it, types the 8 digits, makes its own key pair: ECDH
   gives a session key. The secret is AES-256-CTR encrypted with a TELEPORT
   PASSWORD (8 base32 characters, 40 bits, stretched with PBKDF2-SHA512 and
   the session key), then again with the session key. Each layer ends with
   2 bytes of SHA256 as a check. Shown as BBQr type S: sender pubkey + data.
3. The receiver scans that, ECDH again, and types the teleport password.

For a multisig PSBT (BBQr type E) no setup is needed: both ends already
have each other's xpub, so the keys are derived at .../20250317/(nonce)
from the wallet's keys and only a 4-byte nonce goes along.

Everything here is pure computation: the signer calls it, the app never
sees a key or a cleartext.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from embit import bip32, bip39
from embit.util import secp256k1

KT_DERIV = 20250317            # COLDCARD's KT_RXPUBKEY_DERIV, for multisig PSBTs
NOID_ITERATIONS = 5000
DTYPES = "sxnvpb"              # what's inside (first byte of the cleartext)


class TeleportError(Exception):
    pass


# ------------------------------------------------------------ primitives
def _ctr(key: bytes, data: bytes) -> bytes:
    """AES-256-CTR from a zero counter (pyaes Counter(0) on COLDCARD's side)."""
    c = Cipher(algorithms.AES(key), modes.CTR(b"\x00" * 16)).encryptor()
    return c.update(data) + c.finalize()


def _chk(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()[-2:]


def pubkey(secret: bytes) -> bytes:
    """Compressed public key (33 bytes) of a 32-byte private key."""
    return secp256k1.ec_pubkey_serialize(secp256k1.ec_pubkey_create(secret))


def valid_pubkey(pub: bytes) -> bool:
    try:
        secp256k1.ec_pubkey_parse(bytes(pub))
        return True
    except Exception:  # noqa: BLE001 - any parse failure means "not a point"
        return False


def new_secret() -> bytes:
    while True:
        k = os.urandom(32)
        try:
            secp256k1.ec_pubkey_create(k)
            return k
        except Exception:  # noqa: BLE001 - zero or >= n: pick again
            continue


_P = 2**256 - 2**32 - 977


def _mul_py(sec: bytes, k: int) -> tuple[int, int]:
    """Plain double-and-add on secp256k1, for when embit runs without its
    C library (it has no point multiply then)."""
    x = int.from_bytes(sec[1:33], "big")
    y = pow((x * x * x + 7) % _P, (_P + 1) // 4, _P)
    if y % 2 != sec[0] % 2:
        y = _P - y

    def add(a, b):
        if a is None:
            return b
        if b is None:
            return a
        if a[0] == b[0] and (a[1] + b[1]) % _P == 0:
            return None
        if a == b:
            m = 3 * a[0] * a[0] * pow(2 * a[1], -1, _P) % _P
        else:
            m = (b[1] - a[1]) * pow(b[0] - a[0], -1, _P) % _P
        rx = (m * m - a[0] - b[0]) % _P
        return rx, (m * (a[0] - rx) - a[1]) % _P

    out, pt = None, (x, y)
    while k:
        if k & 1:
            out = add(out, pt)
        pt = add(pt, pt)
        k >>= 1
    return out


def ecdh(secret: bytes, his_pubkey: bytes) -> bytes:
    """SHA256 over the shared point's X and Y (64 bytes), as libngu does."""
    if not valid_pubkey(his_pubkey):
        raise TeleportError("not a valid public key")
    if hasattr(secp256k1, "ec_pubkey_tweak_mul"):
        p = secp256k1.ec_pubkey_parse(bytes(his_pubkey))
        secp256k1.ec_pubkey_tweak_mul(p, secret)            # in place
        xy = secp256k1.ec_pubkey_serialize(p, secp256k1.EC_UNCOMPRESSED)[1:65]
    else:
        x, y = _mul_py(bytes(his_pubkey), int.from_bytes(secret, "big"))
        xy = x.to_bytes(32, "big") + y.to_bytes(32, "big")
    return hashlib.sha256(xy).digest()


# ---------------------------------------------------- receiver password
def rx_code(secret: bytes) -> tuple[str, bytes]:
    """(8-digit receiver password, encrypted pubkey for the R code). The
    password comes from the private key, so it needn't be stored."""
    pub = bytearray(pubkey(secret))
    nk = hashlib.sha256(hashlib.sha256(secret + b"COLCARD4EVER").digest()).digest()
    pub[0] ^= nk[20] & 0xFE                     # hide the 0x02/0x03 prefix's known bits
    num = "%08d" % (int.from_bytes(nk[4:8], "big") % 100_000_000)
    return num, _ctr(hashlib.sha256(num.encode()).digest(), bytes(pub))


def decrypt_rx(code: str, payload: bytes) -> bytes | None:
    """The receiver's pubkey from an R code and its 8 digits. A wrong code
    is caught only about half the time (None); otherwise the sender's ECDH
    simply won't match and the receiver sees a damaged package."""
    if len(payload) != 33 or not (isinstance(code, str) and len(code) == 8 and code.isdigit()):
        return None
    pub = bytearray(_ctr(hashlib.sha256(code.encode()).digest(), payload))
    pub[0] = (pub[0] & 0x01) | 0x02
    return bytes(pub) if valid_pubkey(pub) else None


# ---------------------------------------------------- teleport password
_B32_FIX = str.maketrans({"0": "O", "1": "L", "8": "B"})   # libngu's forgiving decoder


def new_password() -> tuple[bytes, str]:
    k = os.urandom(5)
    return k, base64.b32encode(k).decode()


def password_bytes(text: str) -> bytes:
    t = "".join(text.split()).upper().translate(_B32_FIX)
    if len(t) != 8:
        raise TeleportError("8 characters")
    try:
        return base64.b32decode(t)
    except Exception as e:  # noqa: BLE001
        raise TeleportError("letters A-Z and digits 2-7") from e


def _stretch(session_key: bytes, noid: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha512", session_key, noid, NOID_ITERATIONS)[:32]


# ------------------------------------------------------------- packages
def seal(my_secret: bytes, his_pubkey: bytes, noid: bytes, cleartext: bytes,
         with_pubkey: bool = True) -> bytes:
    if len(noid) != 5 or not cleartext or cleartext[:1].decode("latin1") not in DTYPES:
        raise TeleportError("bad package")
    sk = ecdh(my_secret, his_pubkey)
    b1 = _ctr(_stretch(sk, noid), cleartext) + _chk(cleartext)
    b2 = _ctr(sk, b1) + _chk(b1)
    return (pubkey(my_secret) + b2) if with_pubkey else b2


def open_outer(my_secret: bytes, his_pubkey: bytes, body: bytes) -> tuple[bytes, bytes] | None:
    """(session key, inner layer), or None: wrong key, or a damaged code."""
    if len(body) < 3:
        return None
    try:
        sk = ecdh(my_secret, his_pubkey)
    except TeleportError:
        return None
    inner = _ctr(sk, body[:-2])
    return (sk, inner) if _chk(inner) == body[-2:] else None


def open_inner(session_key: bytes, noid: bytes, inner: bytes) -> tuple[str, bytes] | None:
    """(type, contents) with the teleport password, or None if it's wrong."""
    if len(inner) < 3:
        return None
    msg = _ctr(_stretch(session_key, noid), inner[:-2])
    if _chk(msg) != inner[-2:] or chr(msg[0]) not in DTYPES:
        return None
    return chr(msg[0]), msg[1:]


# ------------------------------------------- COLDCARD's secret encoding
def encode_secret(secret: str) -> bytes:
    """Words or an xprv, as COLDCARD stores them ("s" packages, seed vault
    entries): 0x80|len for words (entropy follows), 0x01 + chain code +
    private key for an xprv. Trailing zero bytes trimmed."""
    if secret.startswith(("xprv", "tprv")):
        k = bip32.HDKey.from_string(secret)
        out = b"\x01" + k.chain_code + k.key.secret
    else:
        ent = bip39.mnemonic_to_bytes(secret)
        out = bytes([0x80 | (len(ent) // 8 - 2)]) + ent
    return out.rstrip(b"\x00") or out[:1]


def decode_secret(raw: bytes, xprv_version: bytes) -> str:
    """Back to words, or an xprv string (an xprv, or a raw BIP32 master seed,
    which COLDCARD can hold as "master seed bytes")."""
    if not raw:
        raise TeleportError("empty secret")
    raw = bytes(raw) + b"\x00" * (72 - len(raw))
    m = raw[0]
    if m == 0x01:
        k = bip32.HDKey(bip32.ec.PrivateKey(raw[33:65]), raw[1:33], version=xprv_version)
        return k.to_string()
    if m & 0x80:
        n = ((m & 0x03) + 2) * 8
        if n not in (16, 24, 32):
            raise TeleportError("unknown seed length")
        return bip39.mnemonic_from_bytes(raw[1:1 + n])
    if 16 <= m <= 64:
        return bip32.HDKey.from_seed(raw[1:1 + m], version=xprv_version).to_string()
    raise TeleportError("unknown kind of secret")


# ---------------------------------------------------- notes, vault entries
def notes_out(entries: list[dict]) -> bytes:
    """Our notes as COLDCARD's: a note is {title, misc}, a password has
    user (even empty: that's how it's told apart), password, site, misc."""
    out = []
    for e in entries:
        if e.get("kind") == "password":
            r = {"title": e.get("title", ""), "user": e.get("user", ""),
                 "password": e.get("password", "")}
            if e.get("site"):
                r["site"] = e["site"]
            if e.get("text"):
                r["misc"] = e["text"]
        else:
            r = {"title": e.get("title", ""), "misc": e.get("text", "")}
        out.append(r)
    return json.dumps(out).encode()


def notes_in(raw: bytes) -> list[dict]:
    """COLDCARD's notes as ours (each still goes through notes.clean)."""
    try:
        recs = json.loads(raw)
    except ValueError as e:
        raise TeleportError("damaged notes") from e
    if not isinstance(recs, list):
        raise TeleportError("damaged notes")
    out = []
    for r in recs:
        if not isinstance(r, dict):
            continue
        title = str(r.get("title") or "Teleported")[:32]
        if "user" in r:
            e = {"kind": "password", "title": title, "user": str(r.get("user", "")),
                 "password": str(r.get("password", "")), "site": str(r.get("site", "")),
                 "text": str(r.get("misc", ""))}
        else:
            e = {"kind": "note", "title": title, "text": str(r.get("misc", ""))}
        out.append({k: v for k, v in e.items() if v != ""} | {"kind": e["kind"]})
    return out


def vault_out(fingerprint: str, encoded: bytes, label: str, origin: str = "Insert Coin") -> bytes:
    """One seed vault entry, as COLDCARD's VaultEntry: [xfp, hex, label, origin]."""
    return json.dumps([fingerprint.upper(), encoded.hex(), label, origin]).encode()


def vault_in(raw: bytes) -> tuple[bytes, str]:
    try:
        xfp, enc, label, _origin = json.loads(raw)
        enc = str(enc)
        return bytes.fromhex(enc + "0" * (len(enc) % 2)), str(label)[:16]
    except (ValueError, TypeError) as e:
        raise TeleportError("damaged seed vault entry") from e


def backup_in(raw: bytes) -> tuple[bytes, list]:
    """A COLDCARD full backup sent by teleport: its master secret, and its
    notes when it has them. Nothing else of it applies here."""
    vals = {}
    for line in raw.decode("utf-8", "replace").split("\n"):
        if " = " not in line or line.startswith("#"):
            continue
        k, v = line.split(" = ", 1)
        try:
            vals[k] = json.loads(v)
        except ValueError:
            continue
    rs = vals.get("raw_secret")
    if not isinstance(rs, str):
        raise TeleportError("no secret in that backup")
    rs += "0" * (len(rs) % 2)
    notes = vals.get("setting.notes") or []
    return bytes.fromhex(rs), notes if isinstance(notes, list) else []


# ------------------------------------------------------- multisig PSBTs
def ms_child_secret(my_key: bip32.HDKey, nonce: int) -> bytes:
    """Our side's private key for nonce: our multisig account key /20250317/nonce."""
    return my_key.derive([KT_DERIV, nonce]).key.secret


def ms_child_pubkey(xpub: bip32.HDKey, nonce: int) -> bytes:
    return xpub.derive([KT_DERIV, nonce]).key.sec()


def new_nonce() -> int:
    return int.from_bytes(os.urandom(4), "big") % (1 << 28)
