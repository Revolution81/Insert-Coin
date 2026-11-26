"""
Microchip ATECC608 secure element over Linux I2C (/dev/i2c-1).

Two kinds of chip are recognised:

  trustgo  M5Stack Unit ID (ATECC608B-TNGTLS "Trust&GO", I2C 0x35). Its
           configuration is locked at the factory with P-256 private keys
           that can't be read out. We use one of them for ECDH: the vault key
           mixes in ECDH(chip key, point derived from the PIN), so every PIN
           guess has to go through this physical chip.
  blank    An unconfigured ATECC608 (usually 0x60). Detected and reported.
           The one-time setup for chip-enforced PIN limits is permanent, so
           it's written and validated on real hardware before it's enabled.

Protocol (ATECC608 datasheet): wake by holding SDA low >60 us, then
  [0x03][count][opcode][p1][p2 lo][p2 hi][data...][crc16 lo][crc16 hi]
CRC-16 poly 0x8005, LSB-first, over count..data. Replies: [count][data][crc].
"""
from __future__ import annotations

import hashlib
import os
import time

ADDRESSES = (0x35, 0x60)          # Trust&GO (Unit ID), factory default blank
I2C_SLAVE = 0x0703

OP_INFO, OP_READ, OP_GENKEY, OP_ECDH, OP_RANDOM = 0x30, 0x02, 0x40, 0x43, 0x1B
EXEC_MS = {OP_INFO: 2, OP_READ: 2, OP_GENKEY: 120, OP_ECDH: 60, OP_RANDOM: 25}
STATUS = {0x01: "checkmac failed", 0x03: "parse error", 0x05: "ECC fault",
          0x07: "self test error", 0x0F: "execution error", 0x11: "after wake",
          0xEE: "watchdog", 0xFF: "CRC error"}


class ChipError(Exception):
    pass


def crc16(data: bytes) -> bytes:
    crc = 0
    for byte in data:
        for i in range(8):
            data_bit = (byte >> i) & 1
            crc_bit = (crc >> 15) & 1
            crc = (crc << 1) & 0xFFFF
            if data_bit != crc_bit:
                crc ^= 0x8005
    return crc.to_bytes(2, "little")


def packet(opcode: int, p1: int, p2: int, data: bytes = b"") -> bytes:
    body = bytes([7 + len(data), opcode, p1, p2 & 0xFF, p2 >> 8]) + data
    return b"\x03" + body + crc16(body)


def parse_reply(raw: bytes) -> bytes:
    if len(raw) < 4 or raw[0] < 4 or raw[0] > len(raw):
        raise ChipError("no reply from secure chip")
    n = raw[0]
    if crc16(raw[:n - 2]) != raw[n - 2:n]:
        raise ChipError("secure chip reply CRC error")
    data = raw[1:n - 2]
    if len(data) == 1 and data[0] != 0x00:
        raise ChipError(f"secure chip: {STATUS.get(data[0], hex(data[0]))}")
    return data


# --------------------------------------------------------------- transport
class I2CTransport:
    def __init__(self, address: int, bus: str = "/dev/i2c-1"):
        import fcntl
        self._fcntl = fcntl
        self.address = address
        self.fd = os.open(bus, os.O_RDWR)

    def _select(self, addr):
        self._fcntl.ioctl(self.fd, I2C_SLAVE, addr)

    def wake(self):
        # A write to address 0 holds SDA low long enough (at 100 kHz) to wake it.
        try:
            self._select(0x00)
            os.write(self.fd, b"\x00")
        except OSError:
            pass                              # NACK is expected
        time.sleep(0.0015)
        self._select(self.address)
        try:
            reply = os.read(self.fd, 4)
        except OSError:
            raise ChipError("secure chip didn't answer")
        if reply != b"\x04\x11\x33\x43":
            raise ChipError("secure chip didn't wake properly")

    def command(self, pkt: bytes, opcode: int) -> bytes:
        os.write(self.fd, pkt)
        time.sleep(EXEC_MS.get(opcode, 50) / 1000)
        for _ in range(20):                   # chip NACKs until it's done
            try:
                head = os.read(self.fd, 1)
                if not head:
                    continue
                n = head[0]
                rest = os.read(self.fd, n - 1) if n > 1 else b""
                return head + rest
            except OSError:
                time.sleep(0.005)
        raise ChipError("secure chip timed out")

    def sleep(self):
        try:
            os.write(self.fd, b"\x01")
        except OSError:
            pass

    def close(self):
        self.sleep()
        os.close(self.fd)


# ---------------------------------------------------------------- the chip
class ATECC:
    def __init__(self, transport):
        self.t = transport
        self._config: bytes | None = None

    def _cmd(self, opcode, p1, p2, data=b""):
        self.t.wake()
        try:
            return parse_reply(self.t.command(packet(opcode, p1, p2, data), opcode))
        finally:
            self.t.sleep()

    def info(self) -> bytes:
        return self._cmd(OP_INFO, 0x00, 0x0000)

    def config(self) -> bytes:
        if self._config is None:
            out = b""
            for block in range(4):            # 4 x 32-byte reads of the config zone
                out += self._cmd(OP_READ, 0x80, block << 3)
            self._config = out
        return self._config

    def serial(self) -> bytes:
        c = self.config()
        return c[0:4] + c[8:13]

    @property
    def config_locked(self) -> bool:
        return self.config()[87] != 0x55

    def slot_config(self, slot: int) -> int:
        c = self.config()
        return int.from_bytes(c[20 + 2 * slot:22 + 2 * slot], "little")

    def key_config(self, slot: int) -> int:
        c = self.config()
        return int.from_bytes(c[96 + 2 * slot:98 + 2 * slot], "little")

    def ecdh_slot(self) -> int | None:
        """First P-256 private-key slot that allows ECDH with the result
        returned to us (not written into another slot)."""
        for slot in range(16):
            kc, sc = self.key_config(slot), self.slot_config(slot)
            private = kc & 0x1
            key_type = (kc >> 2) & 0x7
            ecdh_ok = (sc >> 2) & 0x1
            to_slot = (sc >> 3) & 0x1
            if private and key_type == 4 and ecdh_ok and not to_slot:
                return slot
        return None

    def public_key(self, slot: int) -> bytes:
        return self._cmd(OP_GENKEY, 0x00, slot)       # mode 0: compute public, don't change key

    def ecdh(self, slot: int, point_xy: bytes) -> bytes:
        if len(point_xy) != 64:
            raise ChipError("ECDH needs a 64-byte P-256 point")
        out = self._cmd(OP_ECDH, 0x00, slot, point_xy)
        if len(out) != 32:
            raise ChipError("ECDH result not returned (slot set to store it?)")
        return out

    def random(self) -> bytes:
        return self._cmd(OP_RANDOM, 0x00, 0x0000)


# --------------------------------------------------------------- PIN mixing
def pin_point(secret: bytes) -> bytes:
    """Turn the Argon2 output into a P-256 point (X||Y). The chip's ECDH with
    it can only be computed by the chip, and differs for every PIN."""
    from cryptography.hazmat.primitives.asymmetric import ec
    n = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
    scalar = int.from_bytes(hashlib.sha256(b"czwallet-se-v1" + secret).digest(), "big") % (n - 1) + 1
    pub = ec.derive_private_key(scalar, ec.SECP256R1()).public_key().public_numbers()
    return pub.x.to_bytes(32, "big") + pub.y.to_bytes(32, "big")


def mix(secret: bytes, chip_shared: bytes) -> bytes:
    return hashlib.sha256(b"czwallet-se-key-v1" + secret + chip_shared).digest()


# --------------------------------------------------------------- simulator
class SimulatedChip:
    """Behaves like a Trust&GO chip at the protocol level (wake, CRC,
    Info/Read/GenKey/ECDH). Keys are real P-256 keys, so the vault math is
    exercised exactly as on hardware. blank=True mimics an unlocked chip."""

    def __init__(self, blank: bool = False, seed: bytes | None = None):
        from cryptography.hazmat.primitives.asymmetric import ec
        self.blank = blank
        rnd = seed or os.urandom(32)
        self.keys = {}
        for slot in (0, 2, 3, 4):
            k = int.from_bytes(hashlib.sha256(rnd + bytes([slot])).digest(), "big") % (2 ** 255) + 1
            self.keys[slot] = ec.derive_private_key(k, ec.SECP256R1())
        cfg = bytearray(128)
        cfg[0:4] = b"\x01\x23\x6a\x01"
        cfg[8:13] = rnd[:4] + b"\xee"
        for slot in range(16):
            sc, kc = 0x0000, 0x003C               # data slots by default
            if slot in self.keys:
                sc = 0x2087 if slot == 0 else 0x2007   # ECDH allowed, clear output
                kc = 0x0053                            # private, P-256
            cfg[20 + 2 * slot:22 + 2 * slot] = sc.to_bytes(2, "little")
            cfg[96 + 2 * slot:98 + 2 * slot] = kc.to_bytes(2, "little")
        cfg[86] = 0x55 if blank else 0x00
        cfg[87] = 0x55 if blank else 0x00
        self.cfg = bytes(cfg)
        self.awake = False
        self.calls = 0
        self._pending = b""

    # transport interface
    def wake(self):
        self.awake = True

    def sleep(self):
        self.awake = False

    def close(self):
        pass

    def command(self, pkt: bytes, opcode: int) -> bytes:
        from cryptography.hazmat.primitives.asymmetric import ec
        if not self.awake:
            raise ChipError("secure chip didn't answer")
        self.calls += 1
        body, crc = pkt[1:-2], pkt[-2:]
        if pkt[0] != 0x03 or crc16(body) != crc or body[0] != len(body) + 2:
            return self._reply(b"\xff")
        op, p1, p2, data = body[1], body[2], body[3] | body[4] << 8, body[5:]
        if op == OP_INFO:
            return self._reply(b"\x00\x00\x60\x03")
        if op == OP_READ:
            return self._reply(self.cfg[(p2 >> 3) * 32:(p2 >> 3) * 32 + 32])
        if op == OP_GENKEY and p2 in self.keys:
            n = self.keys[p2].public_key().public_numbers()
            return self._reply(n.x.to_bytes(32, "big") + n.y.to_bytes(32, "big"))
        if op == OP_RANDOM:
            # an unlocked (blank) chip returns this fixed test pattern
            return self._reply(BLANK_RANDOM if self.blank else os.urandom(32))
        if op == OP_ECDH and p2 in self.keys and not self.blank:
            pub = ec.EllipticCurvePublicNumbers(int.from_bytes(data[:32], "big"),
                                                int.from_bytes(data[32:], "big"),
                                                ec.SECP256R1()).public_key()
            return self._reply(self.keys[p2].exchange(ec.ECDH(), pub))
        return self._reply(b"\x0f")

    @staticmethod
    def _reply(data: bytes) -> bytes:
        n = len(data) + 3
        head = bytes([n]) + data
        return head + crc16(head)


# --------------------------------------------------------------- discovery
def open_chip() -> tuple[ATECC | None, dict]:
    """Find a chip. Returns (chip or None, status dict for the UI).
    CZ_SE=sim / sim-blank uses the simulator (desktop + tests)."""
    sim = os.environ.get("CZ_SE")
    if sim in ("sim", "sim-blank"):
        tr = _sim_singleton(sim == "sim-blank")
        if _OPEN.get("sim") is None or _OPEN["sim"].t is not tr:
            _OPEN["sim"] = ATECC(tr)
        chip = _OPEN["sim"]
        return chip, describe(chip, 0x35 if sim == "sim" else 0x60)
    if sim == "none":
        return None, {"state": "absent"}
    cached = _OPEN.get("hw")
    if cached is not None:                  # reuse: the config zone is read once
        try:
            cached.info()
            return cached, describe(cached, cached.t.address)
        except (OSError, ChipError):
            _OPEN.pop("hw", None)
    for bus in chip_buses():
        for addr in ADDRESSES:
            try:
                chip = ATECC(I2CTransport(addr, bus))
                chip.info()
                _OPEN["hw"] = chip
                st = describe(chip, addr)
                st["bus"] = bus
                return chip, st
            except (OSError, ChipError, ImportError):
                continue
    return None, {"state": "absent"}


# EXT header pins 3 (SDA) and 1 (SCL) as a bus of their own, set up from
# SETUP › DEVICE › HARDWARE › EXT HEADER › SECURE CHIP (tools/copy_backup.py
# extbus). A chip there can't touch the keyboard's bus: an unpowered or
# miswired chip on I2C1 held its lines down at power-on, so the keyboard
# and the power chip never started and the device restarted in a loop.
EXT_BUS = "/dev/i2c-3"
SHARED_BUS = "/dev/i2c-1"


def chip_buses() -> list[str]:
    forced = os.environ.get("CZ_ATECC_BUS")
    if forced:
        return [forced]
    return [b for b in (EXT_BUS, SHARED_BUS) if os.path.exists(b)] or [SHARED_BUS]


_OPEN: dict = {}

# What Random returns while the chip's config zone is unlocked: not random.
BLANK_RANDOM = b"\xff\xff\x00\x00" * 8


def chip_entropy() -> bytes:
    """32 bytes from the chip's hardware random number generator, or b""
    if there's no chip, it fails, or it's a blank chip (which only returns
    a fixed test pattern). Only ever mixed in, never used alone."""
    try:
        chip, _ = open_chip()
        if chip is None:
            return b""
        r = chip.random()
    except Exception:  # noqa: BLE001 - no chip, bus error: just don't use it
        return b""
    if len(r) != 32 or r == BLANK_RANDOM or len(set(r)) < 8:
        return b""
    return r


_SIMS: dict = {}


def _sim_singleton(blank: bool) -> SimulatedChip:
    key = "blank" if blank else "tng"
    if key not in _SIMS:
        _SIMS[key] = SimulatedChip(blank=blank, seed=hashlib.sha256(key.encode()).digest())
    return _SIMS[key]


def describe(chip: ATECC, addr: int) -> dict:
    try:
        locked = chip.config_locked
        serial = chip.serial().hex()
    except ChipError as e:
        return {"state": "error", "error": str(e), "address": addr}
    if not locked:
        return {"state": "blank", "address": addr, "serial": serial,
                "kind": "Blank ATECC608"}
    slot = chip.ecdh_slot()
    if slot is None:
        return {"state": "unusable", "address": addr, "serial": serial,
                "error": "no ECDH key slot"}
    return {"state": "ready", "address": addr, "serial": serial, "slot": slot,
            "kind": "Trust&GO" if addr == 0x35 else "ATECC608"}
