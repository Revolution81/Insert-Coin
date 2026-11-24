"""
Seed XOR, as on COLDCARD: a seed split into 2, 3 or 4 parts, each a normal,
valid BIP39 phrase of the same length. XOR-ing the parts' entropy gives the
original seed's entropy back. Every part alone (or any set short of all of
them) is just another random seed: it can even hold a little decoy money.

Unlike SLIP39 split backups, all the parts are needed (N of N), and each
part works in any BIP39 wallet, so the parts don't look like a split.
"""
from __future__ import annotations

import os

from embit import bip39

from . import seed as seedlib

PARTS = (2, 3, 4)


class SeedXorError(Exception):
    pass


def _entropy(mnemonic: str) -> bytes:
    m = seedlib.normalize(mnemonic)
    if not seedlib.is_valid(m):
        raise SeedXorError("a part has a wrong word (checksum failed)")
    return bip39.mnemonic_to_bytes(m)


def split(mnemonic: str, parts: int, rand: bytes | None = None) -> list[str]:
    """parts-1 random phrases plus the one that XORs them back to the seed."""
    if parts not in PARTS:
        raise SeedXorError("2, 3 or 4 parts")
    ent = _entropy(mnemonic)
    n = len(ent)
    rand = rand if rand is not None else os.urandom(n * (parts - 1))
    if len(rand) != n * (parts - 1):
        raise SeedXorError("bad randomness")
    pieces = [rand[i * n:(i + 1) * n] for i in range(parts - 1)]
    last = bytearray(ent)
    for p in pieces:
        for i in range(n):
            last[i] ^= p[i]
    pieces.append(bytes(last))
    return [bip39.mnemonic_from_bytes(p) for p in pieces]


def combine(parts: list[str]) -> str:
    """The original seed from all its parts (any order)."""
    if not 2 <= len(parts) <= 4:
        raise SeedXorError("2 to 4 parts")
    ents = [_entropy(p) for p in parts]
    if len({len(e) for e in ents}) != 1:
        raise SeedXorError("the parts have different lengths")
    if len(set(ents)) != len(ents):
        raise SeedXorError("the same part was entered twice")
    out = bytearray(len(ents[0]))
    for e in ents:
        for i, b in enumerate(e):
            out[i] ^= b
    return bip39.mnemonic_from_bytes(bytes(out))
