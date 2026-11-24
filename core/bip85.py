"""
BIP85: child seeds from your main seed.

Each child is a normal 12, 18 or 24-word seed for another wallet (a phone
wallet, a second device, a gift). They're derived one-way from your main
seed, so the main backup can always re-create every child, while a leaked
child reveals nothing about the main seed or its siblings.

  k        = root / 83696968' / 39' / 0' / <words>' / <index>'
  entropy  = HMAC-SHA512(key="bip-entropy-from-k", msg=k)
  mnemonic = BIP39 of the first 16 (12 words) or 32 (24 words) bytes

Your passphrase, if you use one, is part of the root: a different
passphrase gives different children.
"""
from __future__ import annotations

import hashlib
import hmac

from embit import bip32, bip39

MAX_INDEX = 2 ** 31 - 1


def child_mnemonic(root: bip32.HDKey, words: int, index: int) -> str:
    if words not in (12, 18, 24):
        raise ValueError("12, 18 or 24 words")
    if not isinstance(index, int) or not 0 <= index <= MAX_INDEX:
        raise ValueError("bad index")
    k = root.derive(f"m/83696968h/39h/0h/{words}h/{index}h")
    ent = hmac.new(b"bip-entropy-from-k", k.secret, hashlib.sha512).digest()
    return bip39.mnemonic_from_bytes(ent[:{12: 16, 18: 24, 24: 32}[words]])
