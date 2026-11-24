"""
Silent payments (BIP-352): one reusable address, sp1q... (tsp1q... on test
networks), and every payment to it lands at a fresh Taproot output nobody
else can link to it.

Sending: the payer's input keys and the receiver's scan key make a shared
secret (ECDH); each output key is the receiver's spend key plus a tweak
from that secret. Receiving: the same secret from the scan private key and
the sum of the transaction's input public keys.

Pure computation, checked against BIP-352's test vectors
(tests/data/bip352_vectors.json). The elliptic-curve maths runs on
libsecp256k1 through embit when it's there, in plain Python otherwise.
"""
from __future__ import annotations

import hashlib

from embit import bech32
from embit.hashes import hash160

P = 2**256 - 2**32 - 977
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
GX = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
GY = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8
G = (GX, GY)
NUMS_H = bytes.fromhex("50929b74c1a04954b78b4b6035e97a5e078a5a0f28ec96d547bfee9ace803ac0")
K_MAX = 2323                      # per-scan-key recipient limit


class SilentPaymentError(Exception):
    pass


# ------------------------------------------------------------ curve maths
def _add(a, b):
    if a is None:
        return b
    if b is None:
        return a
    if a[0] == b[0] and (a[1] + b[1]) % P == 0:
        return None
    if a == b:
        m = 3 * a[0] * a[0] * pow(2 * a[1], -1, P) % P
    else:
        m = (b[1] - a[1]) * pow(b[0] - a[0], -1, P) % P
    x = (m * m - a[0] - b[0]) % P
    return x, (m * (a[0] - x) - a[1]) % P


def _mul_py(pt, k):
    out = None
    k %= N
    while k:
        if k & 1:
            out = _add(out, pt)
        pt = _add(pt, pt)
        k >>= 1
    return out


def _lib():
    from embit.util import secp256k1 as s
    return s if hasattr(s, "ec_pubkey_tweak_mul") else None


def mul(pt, k: int):
    """k * pt (pt a point, or None for infinity)."""
    k %= N
    if pt is None or k == 0:
        return None
    s = _lib()
    if s is not None:
        pub = s.ec_pubkey_parse(ser(pt))
        s.ec_pubkey_tweak_mul(pub, k.to_bytes(32, "big"))
        return parse(s.ec_pubkey_serialize(pub))
    return _mul_py(pt, k)


def base(k: int):
    return mul(G, k)


def add(a, b):
    return _add(a, b)


def neg(pt):
    return None if pt is None else (pt[0], (-pt[1]) % P)


def ser(pt) -> bytes:
    if pt is None:
        raise SilentPaymentError("point at infinity")
    return bytes([2 + (pt[1] & 1)]) + pt[0].to_bytes(32, "big")


def parse(b: bytes):
    b = bytes(b)
    if len(b) == 33 and b[0] in (2, 3):
        x = int.from_bytes(b[1:], "big")
        y = _lift(x)
        if y is None:
            raise SilentPaymentError("not a point")
        return (x, y if (y & 1) == (b[0] & 1) else P - y)
    if len(b) == 32:                                     # x-only: even y
        return parse(b"\x02" + b)
    raise SilentPaymentError("bad public key")


def _lift(x: int):
    if x >= P:
        return None
    y2 = (pow(x, 3, P) + 7) % P
    y = pow(y2, (P + 1) // 4, P)
    return y if y * y % P == y2 else None


def tagged(tag: str, data: bytes) -> bytes:
    t = hashlib.sha256(tag.encode()).digest()
    return hashlib.sha256(t + t + data).digest()


def scalar(b: bytes) -> int:
    v = int.from_bytes(b, "big")
    if not 0 < v < N:
        raise SilentPaymentError("hash out of range")
    return v


# ---------------------------------------------------------------- address
def hrp(network: str) -> str:
    return "sp" if network == "bitcoin" else "tsp"


def encode(scan_pub: bytes, spend_pub: bytes, network: str) -> str:
    data = bech32.convertbits(list(scan_pub + spend_pub), 8, 5)
    return bech32.bech32_encode(bech32.Encoding.BECH32M, hrp(network), [0] + data)


def is_address(text: str) -> bool:
    t = (text or "").strip().lower()
    return t.startswith(("sp1q", "tsp1q")) and len(t) >= 116


def decode(address: str, network: str) -> tuple[bytes, bytes]:
    """(scan public key, spend public key), 33 bytes each. Versions 1-30
    are read as version 0 (their first 66 bytes), as BIP-352 says."""
    a = address.strip().lower()
    if len(a) > 1023:
        raise SilentPaymentError("too long")
    want = hrp(network)
    got, data, spec = _bech32_decode_long(a)
    if got != want:
        raise SilentPaymentError("a silent payment address for "
                                 + ("mainnet" if got == "sp" else "a test network"))
    if spec != bech32.Encoding.BECH32M or not data:
        raise SilentPaymentError("damaged silent payment address")
    version = data[0]
    if version == 31:
        raise SilentPaymentError("unknown silent payment version")
    raw = bytes(bech32.convertbits(data[1:], 5, 8, False) or [])
    if version == 0 and len(raw) != 66 or len(raw) < 66:
        raise SilentPaymentError("damaged silent payment address")
    scan, spend = raw[:33], raw[33:66]
    parse(scan)
    parse(spend)
    return scan, spend


def _bech32_decode_long(bech: str):
    """bech32.bech32_decode, without BIP-173's 90-character limit."""
    if any(ord(x) < 33 or ord(x) > 126 for x in bech) or bech.lower() != bech:
        return None, None, None
    pos = bech.rfind("1")
    if pos < 1 or pos + 7 > len(bech):
        return None, None, None
    if not all(x in bech32.CHARSET for x in bech[pos + 1:]):
        return None, None, None
    h = bech[:pos]
    data = [bech32.CHARSET.find(x) for x in bech[pos + 1:]]
    spec = bech32.bech32_verify_checksum(h, data)
    if spec is None:
        return None, None, None
    return h, data[:-6], spec


# ----------------------------------------------------- inputs that count
def _outpoint(txid_hex: str, vout: int) -> bytes:
    return bytes.fromhex(txid_hex)[::-1] + vout.to_bytes(4, "little")


def input_pubkey(prev_script: bytes, script_sig: bytes, witness: list[bytes]):
    """The public key an input contributes (BIP-352's rules), or None."""
    spk = bytes(prev_script)
    if len(spk) == 25 and spk[:3] == b"\x76\xa9\x14" and spk[23:] == b"\x88\xac":   # P2PKH
        h = spk[3:23]
        for i in range(len(script_sig), 32, -1):
            cand = script_sig[i - 33:i]
            if hash160(cand) == h:
                try:
                    return parse(cand)
                except SilentPaymentError:
                    pass
        return None
    if len(spk) == 23 and spk[:2] == b"\xa9\x14" and spk[22] == 0x87:              # P2SH
        redeem = script_sig[1:]
        if len(redeem) == 22 and redeem[:2] == b"\x00\x14" and witness:
            try:
                return parse(witness[-1]) if len(witness[-1]) == 33 else None
            except SilentPaymentError:
                return None
        return None
    if len(spk) == 22 and spk[:2] == b"\x00\x14":                                  # P2WPKH
        if witness and len(witness[-1]) == 33:
            try:
                return parse(witness[-1])
            except SilentPaymentError:
                return None
        return None
    if len(spk) == 34 and spk[:2] == b"\x51\x20":                                  # P2TR
        stack = list(witness)
        if not stack:
            return None
        if len(stack) > 1 and stack[-1][:1] == b"\x50":                            # annex
            stack.pop()
        if len(stack) > 1:                                                          # script path
            cb = stack[-1]
            if len(cb) < 33 or (len(cb) - 33) % 32 or len(cb) > 33 + 32 * 128:
                return None
            if cb[1:33] == NUMS_H:
                return None
        try:
            return parse(spk[2:])
        except SilentPaymentError:
            return None
    return None


def skip_tx(prev_scripts: list[bytes]) -> bool:
    """BIP-352: a transaction spending a SegWit version above 1 isn't scanned."""
    for spk in prev_scripts:
        if len(spk) >= 4 and 0x52 <= spk[0] <= 0x60 and spk[1] == len(spk) - 2:
            return True
    return False


def input_hash(outpoints: list[tuple[str, int]], a_sum) -> int:
    low = min(_outpoint(t, v) for t, v in outpoints)
    return scalar(tagged("BIP0352/Inputs", low + ser(a_sum)))


# ----------------------------------------------------------------- sending
def sender_key(secret: int, taproot: bool) -> int:
    """An input's private key as it counts: negated for a Taproot output
    key with an odd y."""
    if taproot and base(secret)[1] & 1:
        return N - secret
    return secret


def outputs(keys: list[tuple[int, bool]], outpoints: list[tuple[str, int]],
            recipients: list[tuple[bytes, bytes]]) -> list[bytes]:
    """The x-only output keys, one per recipient, in recipients' order
    (k counts up per scan key in that order)."""
    a = sum(sender_key(k, tap) for k, tap in keys) % N
    if a == 0:
        raise SilentPaymentError("input keys cancel out")
    h = input_hash(outpoints, base(a))
    counts: dict[bytes, int] = {}
    out = []
    shared_by_scan: dict[bytes, tuple] = {}
    for scan, spend in recipients:
        if scan not in shared_by_scan:
            shared_by_scan[scan] = mul(parse(scan), h * a % N)
        k = counts.get(scan, 0)
        if k >= K_MAX:
            raise SilentPaymentError("too many payments to one address")
        counts[scan] = k + 1
        t = scalar(tagged("BIP0352/SharedSecret", ser(shared_by_scan[scan]) + k.to_bytes(4, "big")))
        out.append(ser(add(parse(spend), base(t)))[1:])
    return out


# --------------------------------------------------------------- receiving
def keys_from_root(root, network: str, account: int = 0) -> tuple[int, int]:
    """(scan, spend) private keys: m/352'/coin'/account'/1'/0 and /0'/0."""
    coin = 0 if network == "bitcoin" else 1
    base_path = f"m/352h/{coin}h/{account}h"
    scan = root.derive(base_path + "/1h/0").key.secret
    spend = root.derive(base_path + "/0h/0").key.secret
    return int.from_bytes(scan, "big"), int.from_bytes(spend, "big")


def tweak_point(outpoints, pubkeys):
    """input_hash * A for a transaction (what Frigate sends as tweak_key)."""
    a = None
    for p in pubkeys:
        a = add(a, p)
    if a is None:
        return None
    return mul(a, input_hash(outpoints, a))


def scan(b_scan: int, spend_pub, tweak, outputs_xonly: list[bytes],
         labels: dict[bytes, int] | None = None) -> list[tuple[bytes, int]]:
    """Which of a transaction's Taproot output keys are ours: [(x-only key,
    tweak to add to the spend key)]. labels: {serialized m*G: m-scalar}."""
    shared = mul(tweak, b_scan)
    left = list(outputs_xonly)
    found, k = [], 0
    while k < K_MAX and left:
        t = scalar(tagged("BIP0352/SharedSecret", ser(shared) + k.to_bytes(4, "big")))
        pk = add(spend_pub, base(t))
        hit = None
        for o in left:
            if ser(pk)[1:] == o:
                hit = (o, t)
                break
            if labels:
                outp = parse(o)
                for cand in (add(outp, neg(pk)), add(neg(outp), neg(pk))):
                    if cand is not None and ser(cand) in labels:
                        hit = (o, (t + labels[ser(cand)]) % N)
                        break
                if hit:
                    break
        if not hit:
            break
        found.append(hit)
        left.remove(hit[0])
        k += 1
    return found


def label_tweak(b_scan: int, m: int) -> int:
    return scalar(tagged("BIP0352/Label", b_scan.to_bytes(32, "big") + m.to_bytes(4, "big")))


def spend_key(b_spend: int, tweak: int) -> int:
    """The private key of a found output (for signing; negate for odd y)."""
    d = (b_spend + tweak) % N
    return N - d if base(d)[1] & 1 else d
