"""
Uniform Resources (UR): the animated-QR format Sparrow, Keystone, Nunchuk,
BlueWallet and others use by default for PSBTs.

  single part:  ur:crypto-psbt/<bytewords>
  multi part:   ur:crypto-psbt/<seq>-<total>/<bytewords>

Specs (Blockchain Commons): BCR-2020-005 (UR), BCR-2020-012 (bytewords),
BCR-2020-006 (crypto-psbt). Multi-part URs use a fountain code: parts 1..N
are the fragments themselves ("pure"), later parts XOR several fragments
together. The decoder handles both, so missed frames are recovered from
later ones. The encoder only emits pure parts on a loop, which every
decoder accepts.
"""
from __future__ import annotations

import hashlib
import math
import zlib

# --------------------------------------------------------------- bytewords
WORDS = (
    "able acid also apex aqua arch atom aunt away axis back bald barn belt beta bias "
    "blue body brag brew bulb buzz calm cash cats chef city claw code cola cook cost "
    "crux curl cusp cyan dark data days deli dice diet door down draw drop drum dull "
    "duty each easy echo edge epic even exam exit eyes fact fair fern figs film fish "
    "fizz flap flew flux foxy free frog fuel fund gala game gear gems gift girl glow "
    "good gray grim guru gush gyro half hang hard hawk heat help high hill holy hope "
    "horn huts iced idea idle inch inky into iris iron item jade jazz join jolt jowl "
    "judo jugs jump junk jury keep keno kept keys kick kiln king kite kiwi knob lamb "
    "lava lazy leaf legs liar limp lion list logo loud love luau luck lung main many "
    "math maze memo menu meow mild mint miss monk nail navy need news next noon note "
    "numb obey oboe omit onyx open oval owls paid part peck play plus poem pool pose "
    "puff puma purr quad quiz race ramp real redo rich road rock roof ruby ruin runs "
    "rust safe saga scar sets silk skew slot soap solo song stub surf swan taco task "
    "taxi tent tied time tiny toil tomb toys trip tuna twin ugly undo unit urge user "
    "vast very veto vial vibe view visa void vows wall wand warm wasp wave waxy webs "
    "what when whiz wolf work yank yawn yell yoga yurt zaps zero zest zinc zone zoom"
).split()
assert len(WORDS) == 256
MINIMAL = {w[0] + w[-1]: i for i, w in enumerate(WORDS)}
assert len(MINIMAL) == 256


class URError(Exception):
    pass


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def bytewords_encode(data: bytes) -> str:
    body = data + crc32(data).to_bytes(4, "big")
    return "".join(WORDS[b][0] + WORDS[b][-1] for b in body)


def bytewords_decode(text: str) -> bytes:
    text = text.lower()
    if len(text) % 2 or len(text) < 10:
        raise URError("bad bytewords length")
    try:
        body = bytes(MINIMAL[text[i:i + 2]] for i in range(0, len(text), 2))
    except KeyError:
        raise URError("not bytewords")
    data, check = body[:-4], body[-4:]
    if crc32(data).to_bytes(4, "big") != check:
        raise URError("UR checksum failed")
    return data


# -------------------------------------------------------------- tiny CBOR
def _cbor_head(major: int, n: int) -> bytes:
    if n < 24:
        return bytes([major << 5 | n])
    for ai, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
        if n < 1 << (8 * size):
            return bytes([major << 5 | ai]) + n.to_bytes(size, "big")
    raise URError("CBOR value too big")


def cbor_encode(v) -> bytes:
    if isinstance(v, int):
        return _cbor_head(0, v)
    if isinstance(v, (bytes, bytearray)):
        return _cbor_head(2, len(v)) + bytes(v)
    if isinstance(v, list):
        return _cbor_head(4, len(v)) + b"".join(cbor_encode(x) for x in v)
    raise URError("can't CBOR-encode that")


def cbor_decode(data: bytes, pos: int = 0, depth: int = 0):
    """Returns (value, next_pos). Handles uints, byte strings, arrays, tags."""
    if depth > 16:
        raise URError("CBOR nested too deep")
    if pos >= len(data):
        raise URError("truncated CBOR")
    ib = data[pos]
    major, ai = ib >> 5, ib & 31
    pos += 1
    if ai < 24:
        n = ai
    elif ai in (24, 25, 26, 27):
        size = 1 << (ai - 24)
        n = int.from_bytes(data[pos:pos + size], "big")
        pos += size
    else:
        raise URError("unsupported CBOR")
    if major == 0:
        return n, pos
    if major == 2:
        if pos + n > len(data):
            raise URError("truncated CBOR")
        return data[pos:pos + n], pos + n
    if major == 4:
        out = []
        for _ in range(n):
            v, pos = cbor_decode(data, pos, depth + 1)
            out.append(v)
        return out, pos
    if major == 6:                                   # tag: skip it, keep the value
        return cbor_decode(data, pos, depth + 1)
    raise URError("unsupported CBOR")


# ----------------------------------------------------------- fountain PRNG
M64 = (1 << 64) - 1


def _rotl(x: int, k: int) -> int:
    return ((x << k) | (x >> (64 - k))) & M64


class Xoshiro256:
    """xoshiro256** seeded from 32 bytes, big-endian words (as the spec)."""

    def __init__(self, seed: bytes):
        self.s = [int.from_bytes(seed[i * 8:i * 8 + 8], "big") for i in range(4)]

    def next(self) -> int:
        s = self.s
        result = (_rotl((s[1] * 5) & M64, 7) * 9) & M64
        t = (s[1] << 17) & M64
        s[2] ^= s[0]
        s[3] ^= s[1]
        s[1] ^= s[2]
        s[0] ^= s[3]
        s[2] ^= t
        s[3] = _rotl(s[3], 45)
        return result

    def next_double(self) -> float:
        return self.next() / (M64 + 1)

    def next_int(self, low: int, high: int) -> int:
        return int(self.next_double() * (high - low + 1)) + low


class _Sampler:
    """Walker/Vose alias method, built exactly as the reference code."""

    def __init__(self, probs: list[float]):
        total = sum(probs)
        n = len(probs)
        P = [p * n / total for p in probs]
        S, L = [], []
        for i in range(n - 1, -1, -1):
            (S if P[i] < 1 else L).append(i)
        self.probs = [0.0] * n
        self.aliases = [0] * n
        while S and L:
            a, g = S.pop(), L.pop()
            self.probs[a] = P[a]
            self.aliases[a] = g
            P[g] += P[a] - 1
            (S if P[g] < 1 else L).append(g)
        while L:
            self.probs[L.pop()] = 1.0
        while S:
            self.probs[S.pop()] = 1.0
        self.n = n

    def next(self, rng: Xoshiro256) -> int:
        r1, r2 = rng.next_double(), rng.next_double()
        i = int(self.n * r1)
        return i if r2 < self.probs[i] else self.aliases[i]


def choose_fragments(seq_num: int, seq_len: int, checksum: int) -> list[int]:
    if seq_num <= seq_len:
        return [seq_num - 1]
    seed = hashlib.sha256(seq_num.to_bytes(4, "big") + checksum.to_bytes(4, "big")).digest()
    rng = Xoshiro256(seed)
    degree = _Sampler([1.0 / i for i in range(1, seq_len + 1)]).next(rng) + 1
    remaining = list(range(seq_len))
    shuffled = []
    while remaining:
        shuffled.append(remaining.pop(rng.next_int(0, len(remaining) - 1)))
    return sorted(shuffled[:degree])


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


# ----------------------------------------------------------------- decode
MAX_PARTS = 10_000          # ~2 MB of PSBT at typical sizes; anything bigger is hostile


class URDecoder:
    """Feed it every 'ur:' string the camera sees, in any order."""

    def __init__(self):
        self.type = ""
        self.result: bytes | None = None      # the CBOR message
        self.seq_len = 0
        self.checksum = None
        self.message_len = 0
        self.simple: dict[int, bytes] = {}
        self.mixed: list[tuple[set[int], bytes]] = []
        self.seen: set[int] = set()

    @property
    def progress(self) -> tuple[int, int]:
        return len(self.simple), self.seq_len

    def add(self, text: str) -> bool:
        if self.result is not None:
            return False
        low = text.strip().lower()
        if not low.startswith("ur:"):
            raise URError("not a UR")
        parts = low[3:].split("/")
        if len(parts) == 2:                     # single part
            self.type = parts[0]
            self.result = bytewords_decode(parts[1])
            return True
        if len(parts) != 3:
            raise URError("bad UR")
        utype, seq, body = parts
        try:
            seq_num, seq_len = (int(x) for x in seq.split("-"))
        except ValueError:
            raise URError("bad UR sequence")
        part, _ = cbor_decode(bytewords_decode(body))
        if not (isinstance(part, list) and len(part) == 5):
            raise URError("bad UR part")
        p_num, p_len, msg_len, checksum, frag = part
        if (p_num, p_len) != (seq_num, seq_len):
            raise URError("UR part header mismatch")
        if not (isinstance(frag, bytes) and frag and isinstance(msg_len, int)
                and isinstance(checksum, int) and 1 <= p_len <= MAX_PARTS
                and 1 <= p_num and 0 < msg_len <= p_len * len(frag)):
            raise URError("bad UR part")
        if self.checksum is not None and (checksum != self.checksum or utype != self.type):
            self.__init__()                     # a different animation started
        if self.checksum is None:
            self.type, self.seq_len, self.checksum, self.message_len = utype, p_len, checksum, msg_len
        if seq_num in self.seen:
            return False
        self.seen.add(seq_num)
        idx = set(choose_fragments(seq_num, p_len, checksum))
        self._process(idx, bytes(frag))
        if len(self.simple) == self.seq_len:
            msg = b"".join(self.simple[i] for i in range(self.seq_len))[:self.message_len]
            if crc32(msg) != self.checksum:
                self.__init__()
                raise URError("UR message checksum failed; rescanning")
            self.result = msg
        return True

    def _process(self, idx: set[int], data: bytes):
        queue = [(idx, data)]
        while queue:
            idx, data = queue.pop()
            for i in list(idx):                   # strip fragments we already know
                if i in self.simple and len(idx) > 1:
                    data = _xor(data, self.simple[i])
                    idx = idx - {i}
            if len(idx) == 1:
                (i,) = idx
                if i in self.simple:
                    continue
                self.simple[i] = data
                # anything mixed that contains i can now shrink
                still = []
                for m_idx, m_data in self.mixed:
                    if i in m_idx:
                        queue.append((m_idx, m_data))
                    else:
                        still.append((m_idx, m_data))
                self.mixed = still
            elif idx and not any(idx == m for m, _ in self.mixed):
                self.mixed.append((idx, data))


def ur_payload(decoder: URDecoder) -> tuple[str, bytes]:
    """(type, inner bytes) for crypto-psbt / psbt / bytes."""
    value, _ = cbor_decode(decoder.result)
    if not isinstance(value, bytes):
        raise URError(f"unsupported UR content ({decoder.type})")
    return decoder.type, value


# ----------------------------------------------------------------- encode
def nominal_fragment_len(message_len: int, max_len: int, min_len: int = 10) -> int:
    max_count = max(1, message_len // min_len)
    frag = message_len
    for count in range(1, max_count + 1):
        frag = math.ceil(message_len / count)
        if frag <= max_len:
            break
    return frag


def ur_encode(payload: bytes, ur_type: str = "crypto-psbt", max_fragment: int = 85) -> list[str]:
    """85-byte fragments keep each frame a version 7 QR: 3-pixel modules,
    big enough to scan off the 1.9" screen."""
    message = cbor_encode(payload)
    if len(message) <= max_fragment:
        return [f"ur:{ur_type}/{bytewords_encode(message)}".upper()]
    flen = nominal_fragment_len(len(message), max_fragment)
    frags = [message[i:i + flen].ljust(flen, b"\x00") for i in range(0, len(message), flen)]
    checksum = crc32(message)
    n = len(frags)
    return [f"ur:{ur_type}/{k + 1}-{n}/"
            f"{bytewords_encode(cbor_encode([k + 1, n, len(message), checksum, f]))}".upper()
            for k, f in enumerate(frags)]
