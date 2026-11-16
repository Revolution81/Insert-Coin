"""
Signing a text message with an address's key: proof that you control the
address, without moving coins (exchange withdrawals, proof of reserves).

Native SegWit (bc1q...) addresses can use any of these; Taproot (bc1p...)
addresses only BIP322:

  "electrum"  the classic "Bitcoin Signed Message": a 65-byte recoverable
              signature, base64. Header 31-34, as Electrum and Sparrow
              write it; most verifiers accept this for bc1q addresses.
  "bip137"    the same signature with header 39-42, as Trezor writes it.
  "bip322"    BIP322 "simple": the witness of a virtual transaction that
              spends from the address. The modern standard.

A message can never be mistaken for a transaction: the classic formats
hash it behind the "Bitcoin Signed Message:" prefix, and BIP322 signs a
transaction that can't exist on chain.
"""
from __future__ import annotations

import base64
import hashlib
from io import BytesIO

from embit import compact, ec, script
from embit.transaction import SIGHASH, Transaction, TransactionInput, TransactionOutput
from embit.util import secp256k1

FORMATS = ("electrum", "bip137", "bip322")
MAX_LEN = 400


class MessageError(Exception):
    pass


def check_text(message: str) -> str:
    if not isinstance(message, str) or not message:
        raise MessageError("empty message")
    if len(message) > MAX_LEN:
        raise MessageError(f"message too long (max {MAX_LEN})")
    if any(ord(ch) < 32 and ch != "\n" for ch in message) or "\x7f" in message:
        raise MessageError("message has control characters")
    return message


# ------------------------------------------------------------ classic
def magic_hash(message: str) -> bytes:
    m = message.encode()
    data = b"\x18Bitcoin Signed Message:\n" + compact.to_bytes(len(m)) + m
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def sign_classic(key: ec.PrivateKey, message: str, fmt: str = "electrum") -> str:
    sig = secp256k1.ecdsa_sign_recoverable(magic_hash(message), key.secret)
    raw, recid = secp256k1.ecdsa_recoverable_signature_serialize_compact(sig)
    header = (39 if fmt == "bip137" else 31) + recid
    return base64.b64encode(bytes([header]) + raw).decode()


def verify_classic(address_script: script.Script, message: str, signature: str) -> bool:
    """True if the signature recovers to the key behind this P2WPKH script."""
    try:
        raw = base64.b64decode(signature, validate=True)
        if len(raw) != 65 or not 27 <= raw[0] <= 42:
            return False
        recid = (raw[0] - 27) & 3
        rsig = secp256k1.ecdsa_recoverable_signature_parse_compact(raw[1:], recid)
        pub = ec.PublicKey(secp256k1.ecdsa_recover(rsig, magic_hash(message)))
        pub.compressed = True
        return script.p2wpkh(pub).data == address_script.data
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------- BIP322
def _tagged(tag: bytes, data: bytes) -> bytes:
    t = hashlib.sha256(tag).digest()
    return hashlib.sha256(t + t + data).digest()


def bip322_hash(message: str) -> bytes:
    return _tagged(b"BIP0322-signed-message", message.encode())


def bip322_txs(spk: script.Script, message: str) -> tuple[Transaction, Transaction]:
    """(to_spend, to_sign) virtual transactions."""
    sig_script = script.Script(b"\x00\x20" + bip322_hash(message))
    to_spend = Transaction(
        version=0, locktime=0,
        vin=[TransactionInput(b"\x00" * 32, 0xFFFFFFFF, sig_script, 0)],
        vout=[TransactionOutput(0, spk)])
    to_sign = Transaction(
        version=0, locktime=0,
        vin=[TransactionInput(to_spend.txid(), 0, script.Script(b""), 0)],
        vout=[TransactionOutput(0, script.Script(b"\x6a"))])
    return to_spend, to_sign


def sign_bip322(key: ec.PrivateKey, message: str) -> str:
    pub = key.get_public_key()
    spk = script.p2wpkh(pub)
    _, to_sign = bip322_txs(spk, message)
    h = to_sign.sighash_segwit(0, script.p2pkh_from_p2wpkh(spk), 0)
    sig = key.sign(h).serialize() + bytes([SIGHASH.ALL])
    wit = script.Witness([sig, pub.sec()])
    return base64.b64encode(wit.serialize()).decode()


def verify_bip322(spk: script.Script, message: str, signature: str) -> bool:
    try:
        wit = script.Witness.read_from(BytesIO(base64.b64decode(signature, validate=True)))
        sig, sec = wit.items
        pub = ec.PublicKey.parse(sec)
        if script.p2wpkh(pub).data != spk.data or sig[-1] != SIGHASH.ALL:
            return False
        _, to_sign = bip322_txs(spk, message)
        h = to_sign.sighash_segwit(0, script.p2pkh_from_p2wpkh(spk), 0)
        return pub.verify(ec.Signature.parse(sig[:-1]), h)
    except Exception:  # noqa: BLE001
        return False


def sign_bip322_taproot(key: ec.PrivateKey, message: str) -> str:
    """BIP322 simple for a BIP86 key-path Taproot address: one 64-byte
    Schnorr signature (SIGHASH_DEFAULT) with the tweaked key."""
    spk = script.p2tr(key.get_public_key())
    _, to_sign = bip322_txs(spk, message)
    h = to_sign.sighash_taproot(0, script_pubkeys=[spk], values=[0], sighash=SIGHASH.DEFAULT)
    sig = key.taproot_tweak(b"").schnorr_sign(h)
    return base64.b64encode(script.Witness([sig.serialize()]).serialize()).decode()


def verify_bip322_taproot(spk: script.Script, message: str, signature: str) -> bool:
    try:
        if len(spk.data) != 34 or spk.data[:2] != b"\x51\x20":
            return False
        wit = script.Witness.read_from(BytesIO(base64.b64decode(signature, validate=True)))
        if len(wit.items) != 1 or len(wit.items[0]) not in (64, 65):
            return False
        sig = wit.items[0]
        sighash = sig[64] if len(sig) == 65 else SIGHASH.DEFAULT
        if len(sig) == 65 and sighash == SIGHASH.DEFAULT:
            return False
        _, to_sign = bip322_txs(spk, message)
        h = to_sign.sighash_taproot(0, script_pubkeys=[spk], values=[0], sighash=sighash)
        return ec.PublicKey.from_xonly(spk.data[2:]).schnorr_verify(
            ec.SchnorrSig.parse(sig[:64]), h)
    except Exception:  # noqa: BLE001
        return False


def sign(key: ec.PrivateKey, message: str, fmt: str, taproot: bool = False) -> str:
    check_text(message)
    if fmt not in FORMATS:
        raise MessageError("unknown signature format")
    if taproot:
        if fmt != "bip322":
            raise MessageError("Taproot addresses sign with BIP322 only")
        return sign_bip322_taproot(key, message)
    return sign_bip322(key, message) if fmt == "bip322" else sign_classic(key, message, fmt)


def verify(spk: script.Script, message: str, signature: str) -> bool:
    if spk.data[:2] == b"\x51\x20":
        return verify_bip322_taproot(spk, message, signature)
    return verify_classic(spk, message, signature) or verify_bip322(spk, message, signature)


def armored(address: str, message: str, signature: str) -> str:
    return ("-----BEGIN BITCOIN SIGNED MESSAGE-----\n" + message +
            "\n-----BEGIN BITCOIN SIGNATURE-----\n" + address + "\n" + signature +
            "\n-----END BITCOIN SIGNATURE-----")
