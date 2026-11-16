"""
Small strict CBOR (RFC 8949) for the Coinkite card protocol: unsigned and
negative ints, byte and text strings, arrays, maps, true/false/null.
Definite lengths only, nesting and sizes capped: replies come from an NFC
card, which isn't trusted.
"""
from __future__ import annotations

MAX_ITEMS = 256
MAX_DEPTH = 8


class CBORError(ValueError):
    pass


def _head(major: int, n: int) -> bytes:
    if n < 24:
        return bytes([major << 5 | n])
    for ai, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
        if n < 1 << (8 * size):
            return bytes([major << 5 | ai]) + n.to_bytes(size, "big")
    raise CBORError("value too big")


def encode(v) -> bytes:
    if v is False:
        return b"\xf4"
    if v is True:
        return b"\xf5"
    if v is None:
        return b"\xf6"
    if isinstance(v, int):
        return _head(0, v) if v >= 0 else _head(1, -1 - v)
    if isinstance(v, (bytes, bytearray)):
        return _head(2, len(v)) + bytes(v)
    if isinstance(v, str):
        raw = v.encode()
        return _head(3, len(raw)) + raw
    if isinstance(v, (list, tuple)):
        return _head(4, len(v)) + b"".join(encode(x) for x in v)
    if isinstance(v, dict):
        return _head(5, len(v)) + b"".join(encode(k) + encode(x) for k, x in v.items())
    raise CBORError(f"can't encode {type(v).__name__}")


def decode(data: bytes):
    v, pos = _dec(bytes(data), 0, 0)
    if pos != len(data):
        raise CBORError("trailing bytes")
    return v


def _dec(data: bytes, pos: int, depth: int):
    if depth > MAX_DEPTH:
        raise CBORError("nested too deep")
    if pos >= len(data):
        raise CBORError("truncated")
    ib = data[pos]
    major, ai = ib >> 5, ib & 31
    pos += 1
    if major == 7:
        simple = {20: False, 21: True, 22: None}
        if ai in simple:
            return simple[ai], pos
        raise CBORError("unsupported simple value")
    if ai < 24:
        n = ai
    elif ai in (24, 25, 26, 27):
        size = 1 << (ai - 24)
        if pos + size > len(data):
            raise CBORError("truncated")
        n = int.from_bytes(data[pos:pos + size], "big")
        pos += size
    else:
        raise CBORError("indefinite lengths not supported")
    if major == 0:
        return n, pos
    if major == 1:
        return -1 - n, pos
    if major in (2, 3):
        if pos + n > len(data):
            raise CBORError("truncated")
        raw = data[pos:pos + n]
        if major == 3:
            try:
                return raw.decode(), pos + n
            except UnicodeDecodeError:
                raise CBORError("bad text")
        return raw, pos + n
    if n > MAX_ITEMS:
        raise CBORError("too many items")
    if major == 4:
        out = []
        for _ in range(n):
            v, pos = _dec(data, pos, depth + 1)
            out.append(v)
        return out, pos
    if major == 5:
        out = {}
        for _ in range(n):
            k, pos = _dec(data, pos, depth + 1)
            if not isinstance(k, (str, int)):
                raise CBORError("bad map key")
            v, pos = _dec(data, pos, depth + 1)
            out[k] = v
        return out, pos
    if major == 6:
        return _dec(data, pos, depth + 1)
    raise CBORError("unsupported")
