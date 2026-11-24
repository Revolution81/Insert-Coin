"""
Split backups: your seed as K-of-N SLIP39 shares (Trezor's Shamir backup
format, using their reference implementation). Any K shares rebuild it;
fewer reveal nothing about it.

What gets split is the BIP39 *entropy* behind your 12, 18 or 24 words, so the
shares rebuild exactly those words on this wallet. Note for other tools: a
SLIP39 wallet such as a Trezor would treat the same shares as a different
wallet (SLIP39 derives keys differently from BIP39). To use the shares
elsewhere, recover the secret with Trezor's `shamir recover` tool and turn
it back into BIP39 words; the words are what every wallet understands.
"""
from __future__ import annotations

from dataclasses import dataclass

import shamir_mnemonic as sm
from embit import bip39
from shamir_mnemonic.share import Share
from shamir_mnemonic.wordlist import WORDLIST

from . import seed as seedlib

SCHEMES = [(2, 3), (3, 5), (2, 2)]


class SplitError(Exception):
    pass


def words_per_share(mnemonic_words: int) -> int:
    return {12: 20, 18: 27, 24: 33}[mnemonic_words]


def split(mnemonic: str, threshold: int, count: int) -> list[list[str]]:
    if not 1 <= threshold <= count <= 16 or (threshold == 1 and count > 1):
        raise SplitError("need 2 <= K <= N <= 16")
    entropy = bip39.mnemonic_to_bytes(seedlib.normalize(mnemonic))
    groups = sm.generate_mnemonics(1, [(threshold, count)], entropy, b"")
    return [m.split() for m in groups[0]]


@dataclass
class ShareInfo:
    identifier: int
    index: int            # 0-based member index
    threshold: int
    words: int


def check_share(words: list[str]) -> ShareInfo:
    """Checksum and fields of one share, or SplitError."""
    try:
        s = Share.from_mnemonic(" ".join(words))
    except sm.MnemonicError as e:
        raise SplitError(_plain(str(e)))
    if s.group_count != 1:
        raise SplitError("multi-group shares aren't supported")
    return ShareInfo(s.identifier, s.index, s.member_threshold, len(words))


def combine(shares: list[list[str]]) -> str:
    """K shares -> the original BIP39 words."""
    try:
        entropy = sm.combine_mnemonics([" ".join(w) for w in shares], b"")
    except sm.MnemonicError as e:
        raise SplitError(_plain(str(e)))
    if len(entropy) not in (16, 24, 32):
        raise SplitError("these shares aren't from this wallet's split backup")
    return bip39.mnemonic_from_bytes(entropy)


def completions(prefix: str, limit: int = 6) -> list[str]:
    prefix = prefix.lower()
    return [w for w in WORDLIST if w.startswith(prefix)][:limit]


def _plain(msg: str) -> str:
    low = msg.lower()
    if "checksum" in low:
        return "Checksum failed: a word is wrong"
    if "identifier" in low or "common" in low:
        return "Shares are from different backups"
    if "duplicate" in low or "same" in low:
        return "That share was already entered"
    if "wrong number" in low:
        return "Not enough shares yet"
    return msg[:60]
