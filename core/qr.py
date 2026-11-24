"""
What a scanned QR code can mean, and how to show long data as QR.

Understood when scanned:
  - a plain address, or a BIP21 URI  (bitcoin:ADDR?amount=0.001&label=...)
  - a PSBT as base64 (fits in one QR when small)
  - BBQr multi-part QR (Coinkite's spec, supported by Sparrow, Nunchuk,
    Coldcard Q): PSBTs, transactions, text. Parts can arrive in any order.
  - UR (ur:crypto-psbt, ur:psbt, ur:bytes), single or animated, including
    the fountain-coded parts (see core/ur.py).

Shown for other apps to scan:
  - BBQr animated frames for signed PSBTs and transactions
  - single QR for addresses and the watch-only descriptor

BBQr frame layout:  B$ <encoding> <type> <total:2 base36> <index:2 base36> <data>
  encoding  H = hex, 2 = base32, Z = raw-deflate then base32
  type      P = PSBT, T = transaction, U = text, J = JSON, B = binary
Spec: https://bbqr.org
"""
from __future__ import annotations

import base64
import binascii
import zlib
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote

B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
PSBT_MAGIC = b"psbt\xff"


@dataclass
class Scanned:
    kind: str                     # "address" | "psbt" | "tx" | "seedqr" | "text" | "teleport"
    address: str = ""
    sats: int | None = None
    label: str = ""
    message: str = ""
    data: bytes = b""             # PSBT or tx bytes
    text: str = ""


class QRFormatError(Exception):
    pass


# --------------------------------------------------------------- BIP21
def parse_bip21(s: str) -> Scanned:
    body = s.strip()
    if body.lower().startswith("bitcoin:"):
        body = body[8:]
    addr, _, query = body.partition("?")
    q = parse_qs(query)
    sats = None
    if "amount" in q:
        try:
            # Decimal BTC; avoid float rounding by working on the string
            whole, _, frac = q["amount"][0].partition(".")
            if len(frac) > 8 or not (whole + frac).isdigit():
                raise ValueError
            sats = int(whole or "0") * 100_000_000 + int((frac + "00000000")[:8])
        except ValueError:
            raise QRFormatError("bad amount in payment request")
    return Scanned("address", address=addr, sats=sats,
                   label=unquote(q.get("label", [""])[0]),
                   message=unquote(q.get("message", [""])[0]))


# ---------------------------------------------------------------- BBQr
def _b32decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 8)
    return base64.b32decode(s + pad)


def _b32encode(b: bytes) -> str:
    return base64.b32encode(b).decode().rstrip("=")


def bbqr_header(frame: str) -> tuple[str, str, int, int] | None:
    if len(frame) < 8 or not frame.startswith("B$"):
        return None
    enc, ftype = frame[2], frame[3]
    try:
        total, idx = int(frame[4:6], 36), int(frame[6:8], 36)
    except ValueError:
        return None
    if enc not in "H2Z" or total < 1 or not 0 <= idx < total:
        return None
    return enc, ftype, total, idx


def bbqr_join(parts: list[str], enc: str) -> bytes:
    data = "".join(parts)
    try:
        if enc == "H":
            return binascii.unhexlify(data)
        raw = _b32decode(data)
        # window 15 also reads streams made with the spec's smaller window 10
        return zlib.decompress(raw, wbits=-15) if enc == "Z" else raw
    except (binascii.Error, zlib.error, ValueError) as e:
        raise QRFormatError(f"damaged BBQr data: {e}")


def bbqr_encode(data: bytes, ftype: str, max_chars: int = 140, compress: bool = True) -> list[str]:
    """Split data into BBQr frames of at most max_chars of payload each.
    140 base32 chars keeps every frame a small QR that's easy to scan off a
    1.9" screen."""
    z = zlib.compressobj(wbits=-10, level=9)
    packed = z.compress(data) + z.flush()
    if compress and len(packed) < len(data):
        enc, payload = "Z", _b32encode(packed)
    else:                                             # KEY TELEPORT: always plain base32
        enc, payload = "2", _b32encode(data)
    step = max(8, max_chars - max_chars % 8)          # base32 parts: multiples of 8 chars
    chunks = [payload[i:i + step] for i in range(0, len(payload), step)] or [""]
    if len(chunks) > 1295:
        raise QRFormatError("too big for BBQr")
    n = len(chunks)
    return [f"B${enc}{ftype}{B36[n // 36]}{B36[n % 36]}{B36[i // 36]}{B36[i % 36]}{c}"
            for i, c in enumerate(chunks)]


# ----------------------------------------------------------- collector
@dataclass
class Collector:
    """Feed it every QR string the camera sees; `result` fills in once a
    complete payload has been read. Repeated and out-of-order frames are fine."""
    result: Scanned | None = None
    total: int = 0
    parts: dict[int, str] = field(default_factory=dict)
    _enc: str = ""
    _type: str = ""
    error: str = ""
    _ur: object = None

    @property
    def progress(self) -> tuple[int, int]:
        if self._ur is not None:
            return self._ur.progress
        return len(self.parts), self.total

    def add(self, text: str) -> bool:
        """Returns True when something new was learned. Never raises: whatever
        a QR holds, the worst it can do is show an error and keep scanning."""
        try:
            return self._add(text)
        except QRFormatError as e:
            self.error = str(e)
        except Exception:  # noqa: BLE001 - hostile or damaged codes
            self.error = "couldn't read that code"
        self.parts.clear()
        self.total = 0
        self._ur = None
        return False

    def _add(self, text: str) -> bool:
        if self.result:
            return False
        text = text.strip()
        if text.lower().startswith("ur:"):
            return self._add_ur(text)
        hdr = bbqr_header(text)
        if hdr:
            enc, ftype, total, idx = hdr
            if self.total and (total != self.total or enc != self._enc or ftype != self._type):
                self.parts.clear()                    # a different animation started
            self.total, self._enc, self._type = total, enc, ftype
            if idx in self.parts:
                return False
            self.parts[idx] = text[8:]
            if len(self.parts) == total:
                self._finish_bbqr()
            return True
        try:
            self.result = classify(text)
            return True
        except QRFormatError as e:
            self.error = str(e)
            return False

    def _add_ur(self, text: str) -> bool:
        from .ur import URDecoder, URError, ur_payload
        if self._ur is None:
            self._ur = URDecoder()
        try:
            new = self._ur.add(text)
            if self._ur.result is not None:
                utype, payload = ur_payload(self._ur)
                if utype in ("crypto-psbt", "psbt"):
                    self.result = Scanned("psbt", data=payload)
                elif utype == "bytes":
                    self.result = classify(payload.decode("utf-8", "replace"))
                else:
                    self.error = f"{utype} codes aren't supported"
                    self._ur = None
            return new
        except URError as e:
            self.error = str(e)
            return False

    def _finish_bbqr(self):
        try:
            data = bbqr_join([self.parts[i] for i in range(self.total)], self._enc)
        except QRFormatError as e:
            self.error = str(e)
            self.parts.clear()
            return
        t = self._type
        if t == "P":
            self.result = Scanned("psbt", data=data)
        elif t == "T":
            self.result = Scanned("tx", data=data)
        elif t in "UJ":
            self.result = classify(data.decode("utf-8", "replace"))
        elif t in "RSE":                              # KEY TELEPORT (COLDCARD)
            self.result = Scanned("teleport", data=data, text=t)
        else:
            self.error = f"unsupported BBQr type {t}"
            self.parts.clear()


def classify(text: str) -> Scanned:
    """A single (non-animated) QR."""
    t = text.strip()
    low = t.lower()
    if low.startswith("bitcoin:"):
        return parse_bip21(t)
    if low.startswith(("lightning:", "lnbc", "lntb", "lnurl")):
        raise QRFormatError("Lightning isn't supported yet")
    if t.startswith("cHNidP"):                        # base64 of "psbt\xff"
        try:
            return Scanned("psbt", data=base64.b64decode(t, validate=True))
        except (binascii.Error, ValueError):
            raise QRFormatError("damaged PSBT")
    if low.startswith("70736274ff"):                  # hex PSBT
        try:
            return Scanned("psbt", data=bytes.fromhex(t))
        except ValueError:
            raise QRFormatError("damaged PSBT")
    if t.isdigit() and len(t) in (48, 96):
        return Scanned("seedqr", text=t)               # SeedSigner Standard SeedQR
    if " " not in t and 25 <= len(t) <= 90:
        return Scanned("address", address=t)
    from .silentpay import is_address as _is_sp
    if " " not in t and _is_sp(t) and len(t) <= 1023:
        return Scanned("address", address=t)          # a silent payment address
    return Scanned("text", text=t)
