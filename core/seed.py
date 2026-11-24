"""
Seed creation and key derivation.

Entropy: the kernel's CSPRNG (os.urandom, i.e. getrandom()) is always used,
and anything the user adds (dice rolls, camera frames, key timing) is mixed
in with SHA-256. Mixing can only add entropy, never remove it, so a bad dice
roll can't weaken the seed and a bad RNG is rescued by honest dice.
There is deliberately no home-made random number generator anywhere.
"""
from __future__ import annotations

import hashlib
import hmac
import os

from embit import bip32, bip39
from embit.networks import NETWORKS as EMBIT_NETS
from embit.wordlists.bip39 import WORDLIST

from .config import NETWORKS


WORD_COUNTS = (12, 18, 24)
ENTROPY_BYTES = {12: 16, 18: 24, 24: 32}     # 128, 192, 256 bits


def new_mnemonic(words: int = 12, extra_entropy: bytes = b"", camera: bytes = b"",
                 chip: bytes = b"", user: dict[str, bytes] | None = None) -> str:
    """A new seed from every source we have, hashed together:

      - 32 bytes from the Linux kernel (getrandom: waits until the kernel's
        generator is seeded; the Pi's hardware RNG feeds it), always;
      - extra_entropy: your dice rolls, if any;
      - camera: a hash of raw camera frames (sensor noise), if a camera worked;
      - chip: 32 bytes from the secure chip's hardware RNG, if one is connected;
      - user: anything else you added (key mashing, coin flips, cards...),
        by name.

    Each source goes in under its own label. Hashing means the seed is at
    least as strong as the best source: a bad or even hostile source can't
    take strength away from the others."""
    if words not in WORD_COUNTS:
        raise ValueError("12, 18 or 24 words")
    nbytes = ENTROPY_BYTES[words]
    h = hashlib.sha256(b"czwallet-seed-v2")
    h.update(os.urandom(32))
    for label, value in ((b"dice", extra_entropy), (b"camera", camera), (b"chip", chip)):
        h.update(label + hashlib.sha256(value).digest())
    for name in sorted(user or {}):
        h.update(b"user:" + name.encode() + b"\0" + hashlib.sha256(user[name]).digest())
    return bip39.mnemonic_from_bytes(h.digest()[:nbytes])


# ------------------------------------------------- randomness you add yourself
DICE_ONLY_MIN = {12: 50, 18: 75, 24: 99}     # rolls for 128 / 192 / 256 bits


def dice_only_mnemonic(rolls: str, words: int) -> str:
    """A seed from dice alone, so you can check it elsewhere: the entropy is
    SHA-256 of the rolls exactly as typed (ASCII 1-6), first 16 bytes for
    12 words, 24 for 18, or all 32 for 24. Check with:
        printf '%s' 3615...  | sha256sum
    and give the hex (32, 48 or 64 characters) to any BIP39 tool that takes
    entropy. Refuses too few rolls."""
    dice_entropy(rolls)
    if words not in DICE_ONLY_MIN:
        raise ValueError("12, 18 or 24 words")
    if len(rolls) < DICE_ONLY_MIN[words]:
        raise ValueError(f"need at least {DICE_ONLY_MIN[words]} rolls")
    ent = hashlib.sha256(rolls.encode()).digest()
    return bip39.mnemonic_from_bytes(ent[:ENTROPY_BYTES[words]])


def coin_entropy(flips: str) -> bytes:
    f = flips.upper()
    if not f or any(c not in "HT" for c in f):
        raise ValueError("flips must be H or T")
    return f.encode()


RANKS, SUITS = "A23456789TJQK", "SHDC"


def parse_cards(text: str) -> list[str]:
    """'AS 7h 10D kc' -> ['AS', '7H', 'TD', 'KC']. Raises ValueError."""
    t = text.upper().replace("10", "T")
    tokens = t.split() if " " in t.strip() else [t[i:i + 2] for i in range(0, len(t), 2)]
    cards = []
    for tok in tokens:
        if len(tok) != 2 or tok[0] not in RANKS or tok[1] not in SUITS:
            raise ValueError(f"'{tok}' isn't a card (like AS, 7H, TD)")
        if tok in cards:
            raise ValueError(f"{tok} appears twice")
        cards.append(tok)
    if not cards:
        raise ValueError("no cards")
    return cards


def card_bits(n: int) -> float:
    """Bits in the order of n cards from a shuffled 52-card deck."""
    import math
    return sum(math.log2(52 - i) for i in range(min(n, 52)))


def dice_entropy(rolls: str) -> bytes:
    """Rolls as a string of 1-6. 50 rolls ~ 128 bits, 99 rolls ~ 256 bits."""
    if not rolls or any(c not in "123456" for c in rolls):
        raise ValueError("dice rolls must be digits 1-6")
    return rolls.encode()


def dice_bits(rolls: str) -> float:
    return len(rolls) * 2.585


def is_valid(mnemonic: str) -> bool:
    return bip39.mnemonic_is_valid(normalize(mnemonic))


def normalize(mnemonic: str) -> str:
    return " ".join(mnemonic.lower().split())


def completions(prefix: str, limit: int = 6) -> list[str]:
    """BIP39 words starting with prefix: words are unique in 4 letters, so
    autocomplete makes 24-word entry fast on a small keyboard."""
    prefix = prefix.lower()
    return [w for w in WORDLIST if w.startswith(prefix)][:limit]


def last_word_options(first_words: list[str]) -> list[str]:
    """Valid final words for 11 or 23 chosen words (to finish a dice seed)."""
    out = []
    for w in WORDLIST:
        if bip39.mnemonic_is_valid(" ".join(first_words + [w])):
            out.append(w)
    return out


def root_key(mnemonic: str, passphrase: str, network: str) -> bip32.HDKey:
    _, emb, _ = NETWORKS[network]
    seed = bip39.mnemonic_to_seed(normalize(mnemonic), password=passphrase)
    return bip32.HDKey.from_seed(seed, version=EMBIT_NETS[emb]["xprv"])


def account_path(network: str, account: int = 0, kind: str = "wpkh") -> str:
    from .accounts import purpose
    coin = NETWORKS[network][2]
    return f"m/{purpose(kind)}h/{coin}h/{account}h"


def public_descriptors(root: bip32.HDKey, network: str, account: int = 0,
                       kind: str = "wpkh") -> tuple[str, str]:
    """Receive and change descriptors of a single-sig account, public only:
    BIP84 wpkh(...) or BIP86 tr(...). These go to the wallet service; the
    private key never leaves the signer."""
    _, emb, _ = NETWORKS[network]
    path = account_path(network, account, kind)
    xpub = root.derive(path).to_public().to_string(version=EMBIT_NETS[emb]["xpub"])
    origin = f"{root.my_fingerprint.hex()}/{path[2:].replace('h', chr(39))}"
    return (f"{kind}([{origin}]{xpub}/0/*)", f"{kind}([{origin}]{xpub}/1/*)")


def fingerprint(root: bip32.HDKey) -> str:
    return root.my_fingerprint.hex()


def words_match(a: str, b: str) -> bool:
    return hmac.compare_digest(normalize(a).encode(), normalize(b).encode())


def seedqr_encode(mnemonic: str) -> str:
    """SeedSigner's Standard SeedQR: each word's index as 4 digits."""
    return "".join(f"{WORDLIST.index(w):04d}" for w in normalize(mnemonic).split())


def seedqr_decode(digits: str) -> str:
    digits = digits.strip()
    if not digits.isdigit() or len(digits) not in (48, 96):
        raise ValueError("not a SeedQR")
    idx = [int(digits[i:i + 4]) for i in range(0, len(digits), 4)]
    if any(i > 2047 for i in idx):
        raise ValueError("not a SeedQR")
    m = " ".join(WORDLIST[i] for i in idx)
    if not is_valid(m):
        raise ValueError("SeedQR checksum failed")
    return m
