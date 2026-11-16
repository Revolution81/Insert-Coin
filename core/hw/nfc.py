"""
The Cap CC1101 & NFC: an ST25R3916 NFC reader and a CC1101 sub-GHz radio
on one SPI bus.

NFC: reader mode only, ISO 14443-A up to ISO-DEP (14443-4), which is what
Coinkite's SATSCARD and TAPSIGNER speak (core/cktap.py). The field is on
only while a TAP screen is up.

CC1101: this wallet never uses it. At start it's reset and put to sleep
(power-down), and HARDWARE shows it asleep. Nothing here can transmit on it.

Register map, commands and the start-up sequence follow M5Stack's
M5Unit-NFC library (MIT License, Copyright (c) 2025 M5Stack Technology
CO LTD), translated to Python over Linux spidev. Unverified on hardware:
which SPI bus/chip-selects the Cardputer Zero routes to the Cap
(CZ_NFC_SPI, CZ_CC1101_SPI), the supply setting (3.3 V assumed) and the
timing of a Python polling loop. tools/hwtest.py nfc steps through it.

CZ_NFC=sim-satscard|sim-tapsigner|none   simulator / disable
"""
from __future__ import annotations

import os
import time

# ------------------------------------------------------- ST25R3916 constants
OP_READ, OP_LOAD_FIFO, OP_READ_FIFO, SPACE_B = 0x40, 0x80, 0x9F, 0xFB
REG_IO1, REG_IO2, REG_OPCTRL, REG_MODE, REG_BITRATE, REG_ISO14443A = 0x00, 0x01, 0x02, 0x03, 0x04, 0x05
REG_PT_DEF, REG_AUX_DEF = 0x08, 0x0A
REG_RX1, REG_RX2, REG_RX3, REG_RX4 = 0x0B, 0x0C, 0x0D, 0x0E
REG_NRT1, REG_TIMER_EMV = 0x10, 0x12
REG_MASK_MAIN, REG_IRQ_MAIN = 0x16, 0x1A
REG_FIFO1, REG_COLL = 0x1E, 0x20
REG_NTX1 = 0x22
REG_ANT1, REG_ANT2, REG_TXDRV, REG_PT_MOD = 0x26, 0x27, 0x28, 0x29
REG_EFD_ON, REG_EFD_OFF, REG_AUX_DISP, REG_IC_ID = 0x2A, 0x2B, 0x31, 0x3F
B_EMD, B_CORR1, B_CORR2, B_RES_AM = 0x05, 0x0C, 0x0D, 0x2A
B_OVER1, B_OVER2, B_UNDER1, B_UNDER2 = 0x30, 0x31, 0x32, 0x33

CMD_SET_DEFAULT, CMD_STOP, CMD_TX_CRC, CMD_TX_NOCRC = 0xC1, 0xC2, 0xC4, 0xC5
CMD_REQA, CMD_WUPA, CMD_RESET_RXGAIN, CMD_ADJUST_REG = 0xC6, 0xC7, 0xD5, 0xD6
CMD_CLEAR_FIFO, CMD_TEST_ACCESS = 0xDB, 0xFC

EN, RX_EN, TX_EN, WU = 0x80, 0x40, 0x08, 0x04
ANTCL, NO_CRC_RX, DIS_CORR = 0x01, 0x80, 0x04
OSC_OK = 0x10
# 32-bit IRQ word: main << 24 | timer/nfc << 16 | error << 8 | passive target
I_OSC, I_RXS, I_RXE, I_TXE, I_COL = 0x80 << 24, 0x20 << 24, 0x10 << 24, 0x08 << 24, 0x04 << 24
I_NRE = 0x40 << 16
I_ERRORS = 0xF0 << 8          # crc, parity, soft and hard framing
IC_TYPE = 0x05

FC = 13.56e6


class NFCError(Exception):
    pass


def crc_a(data: bytes) -> bytes:
    """ISO 14443-A CRC (init 0x6363, reflected 0x8408), little-endian."""
    crc = 0x6363
    for b in data:
        b ^= crc & 0xFF
        b = (b ^ (b << 4)) & 0xFF
        crc = (crc >> 8) ^ (b << 8) ^ (b << 3) ^ (b >> 4)
    return bytes([crc & 0xFF, crc >> 8])


def bcc(data: bytes) -> int:
    x = 0
    for b in data:
        x ^= b
    return x


# --------------------------------------------------------------- SPI bus
class SPI:
    def __init__(self, bus: int, dev: int, mode: int, hz: int = 4_000_000):
        import spidev                                   # apt: python3-spidev
        self.s = spidev.SpiDev()
        self.s.open(bus, dev)
        self.s.mode = mode
        self.s.max_speed_hz = hz

    def xfer(self, data: list[int]) -> list[int]:
        return self.s.xfer2(list(data))

    def close(self):
        self.s.close()


def _spi_from_env(var: str, default: str, mode: int) -> SPI:
    bus, dev = os.environ.get(var, default).split(".")
    return SPI(int(bus), int(dev), mode)


# --------------------------------------------------------------- ST25R3916
class ST25R3916:
    def __init__(self, spi: SPI, vdd_5v: bool = False):
        self.spi = spi
        self.vdd_5v = vdd_5v
        self.field = False

    # register access ---------------------------------------------------
    def read(self, reg: int, n: int = 1) -> bytes:
        return bytes(self.spi.xfer([OP_READ | reg] + [0] * n)[1:])

    def read8(self, reg: int) -> int:
        return self.read(reg)[0]

    def write(self, reg: int, *vals: int):
        self.spi.xfer([reg & 0x3F] + list(vals))

    def write_b(self, reg: int, val: int):
        self.spi.xfer([SPACE_B, reg & 0x3F, val])

    def modify(self, reg: int, set_bits: int, clear_bits: int):
        self.write(reg, (self.read8(reg) & ~clear_bits | set_bits) & 0xFF)

    def cmd(self, c: int, *data: int):
        self.spi.xfer([c] + list(data))

    def irqs(self) -> int:
        return int.from_bytes(self.read(REG_IRQ_MAIN, 4), "big")

    def wait(self, mask: int, timeout_ms: float) -> int:
        end = time.monotonic() + timeout_ms / 1000
        seen = 0
        while True:
            seen |= self.irqs()
            if seen & mask or time.monotonic() > end:
                return seen if seen & mask else seen | I_NRE

    def fifo_count(self) -> tuple[int, int]:
        s1, s2 = self.read(REG_FIFO1, 2)
        return s1 | ((s2 & 0xC0) << 2), (s2 >> 1) & 0x07

    def fifo_read(self, n: int) -> bytes:
        return bytes(self.spi.xfer([OP_READ_FIFO] + [0] * n)[1:]) if n else b""

    def fifo_load(self, data: bytes):
        self.spi.xfer([OP_LOAD_FIFO] + list(data))

    def tx_count(self, nbytes: int, nbits: int = 0):
        v = ((nbytes & 0x1FF) << 3) | (nbits & 7)
        self.write(REG_NTX1, v >> 8, v & 0xFF)

    def set_timeout(self, ms: float):
        """No-response timer, in 4096/fc steps (timer control nrt_step=1)."""
        nrt = min(0xFFFF, max(1, round((ms / 1000) / (4096 / FC))))
        self.write(REG_NRT1, nrt >> 8, nrt & 0xFF)

    # setup -------------------------------------------------------------
    def identity(self) -> tuple[int, int]:
        v = self.read8(REG_IC_ID)
        return (v >> 3) & 0x1F, v & 0x07

    def start(self):
        for _ in range(5):
            kind, rev = self.identity()
            if kind == IC_TYPE and rev:
                break
            time.sleep(0.02)
        else:
            raise NFCError("no ST25R3916 answering on SPI")
        self.cmd(CMD_STOP)
        self.modify(REG_OPCTRL, 0, TX_EN | RX_EN)
        time.sleep(0.002)
        self.cmd(CMD_SET_DEFAULT)
        self.cmd(CMD_TEST_ACCESS, 0x04, 0x10)          # overheat-protection workaround
        # SPI: MISO pull-downs, IO drive level; 3.3 V supply unless told otherwise
        self.write(REG_IO1, 0x00)
        self.write(REG_IO2, 0x1C | (0x00 if self.vdd_5v else 0x80))
        self.write(REG_TXDRV, (13 & 0x0F) << 4)
        self.modify(REG_IO1, 0x07, 0x07)                # no MCU clock output
        self.write_b(B_RES_AM, 0x80)
        self.modify(REG_IO2, 0x20, 0)                   # AAT D/A on
        self.write_b(B_RES_AM, 0x00)
        self.write(REG_EFD_ON, 0x13)
        self.write(REG_EFD_OFF, 0x02)
        self.modify(REG_PT_DEF, 0x50, 0xF0)
        self.write(REG_PT_MOD, 0x5F)
        self.write_b(B_EMD, 0x40)
        self.write(REG_ANT1, 0x82)
        self.write(REG_ANT2, 0x82)
        self.modify(REG_OPCTRL, 0x03, 0)                # external field detector: auto
        self.cmd(CMD_CLEAR_FIFO)
        self.write(REG_MASK_MAIN, 0xFF, 0xFF, 0x00, 0xFF)
        self.irqs()
        if not self.read8(REG_OPCTRL) & EN:             # oscillator on
            self.modify(REG_MASK_MAIN, 0, 0x80)
            self.irqs()
            self.modify(REG_OPCTRL, EN, 0)
            got = self.wait(I_OSC, 50)
            self.modify(REG_MASK_MAIN, 0x80, 0)
            if not got & I_OSC and not self.read8(REG_AUX_DISP) & OSC_OK:
                raise NFCError("NFC oscillator didn't start")
        self.write(REG_MASK_MAIN, 0, 0, 0, 0)
        self.cmd(CMD_ADJUST_REG)
        time.sleep(0.005)
        self._nfc_a()

    def _nfc_a(self):
        self.cmd(CMD_STOP)
        self.modify(REG_OPCTRL, 0, WU)
        self.write(REG_MODE, 0x08 | 0x01)               # initiator, ISO14443A, nfc_ar auto
        self.write(REG_BITRATE, 0x00)                   # 106 kbit/s both ways
        self.write(REG_ISO14443A, 0x00)
        self.modify(REG_AUX_DEF, 0, DIS_CORR)
        for reg, val in ((B_OVER1, 0x40), (B_OVER2, 0x03), (B_UNDER1, 0x40), (B_UNDER2, 0x03),
                         (B_CORR1, 0x47), (B_CORR2, 0x00)):
            self.write_b(reg, val)
        self.write(REG_RX1, 0x08)
        self.write(REG_RX2, 0x2D)
        self.write(REG_RX3, 0xD8)
        self.write(REG_RX4, 0x22)
        self.cmd(CMD_RESET_RXGAIN)
        self.write(REG_MASK_MAIN, 0, 0, 0, 0x02)
        self.write(REG_TIMER_EMV, 0x09)                 # mrt step 512/fc, nrt step 4096/fc

    def field_on(self):
        self.cmd(CMD_STOP)
        self.modify(REG_OPCTRL, TX_EN | RX_EN, 0)
        self.field = True
        time.sleep(0.005)                               # guard time before the first command

    def field_off(self):
        self.cmd(CMD_STOP)
        self.modify(REG_OPCTRL, 0, TX_EN | RX_EN)
        self.field = False

    # frames ------------------------------------------------------------
    def _receive(self, timeout_ms: float, want: int = 1) -> bytes:
        irq = self.wait(I_RXE | I_COL, timeout_ms)
        if irq & I_COL:
            raise NFCError("more than one card in the field")
        if not irq & I_RXE:
            if irq & I_RXS:                             # started: give the FIFO a moment
                end = time.monotonic() + timeout_ms / 1000
                while self.fifo_count()[0] < want and time.monotonic() < end:
                    pass
            else:
                raise NFCError("no answer")
        if irq & I_ERRORS:
            raise NFCError("garbled answer")
        n, _ = self.fifo_count()
        return self.fifo_read(n)

    def transceive(self, frame: bytes, timeout_ms: float = 10, crc: bool = True) -> bytes:
        """Send with CRC; return the answer with its CRC checked and removed."""
        self.set_timeout(timeout_ms)
        self.write(REG_ISO14443A, 0x00)
        self.modify(REG_AUX_DEF, 0, NO_CRC_RX)
        self.irqs()
        self.cmd(CMD_CLEAR_FIFO)
        self.fifo_load(frame)
        self.tx_count(len(frame))
        self.cmd(CMD_TX_CRC if crc else CMD_TX_NOCRC)
        rx = self._receive(timeout_ms, 3)
        if len(rx) < 3 or crc_a(rx[:-2]) != rx[-2:]:
            raise NFCError("bad CRC")
        return rx[:-2]

    def reqa(self) -> bytes | None:
        self.set_timeout(4)
        self.write(REG_ISO14443A, ANTCL)
        self.modify(REG_AUX_DEF, NO_CRC_RX, 0)
        self.irqs()
        self.cmd(CMD_CLEAR_FIFO)
        self.cmd(CMD_REQA)
        irq = self.wait(I_RXE | I_RXS | I_COL, 4)
        if not irq & (I_RXE | I_RXS):
            return None
        time.sleep(0.001)
        n, _ = self.fifo_count()
        atqa = self.fifo_read(n)
        return atqa[:2] if len(atqa) >= 2 else None

    def select(self) -> tuple[bytes, int]:
        """Anticollision and select, cascade levels 1-3 (one card only).
        Returns (UID, SAK)."""
        uid = b""
        for level in (1, 2, 3):
            sel = 0x91 + 2 * level
            self.set_timeout(8)
            self.write(REG_ISO14443A, ANTCL)
            self.modify(REG_AUX_DEF, NO_CRC_RX, 0)
            self.irqs()
            self.cmd(CMD_CLEAR_FIFO)
            self.fifo_load(bytes([sel, 0x20]))
            self.tx_count(2)
            self.cmd(CMD_TX_NOCRC)
            got = self._receive(8, 5)
            if len(got) != 5 or bcc(got[:4]) != got[4]:
                raise NFCError("anticollision failed")
            sak = self.transceive(bytes([sel, 0x70]) + got, 8)
            if not sak:
                raise NFCError("no SAK")
            uid += got[1:4] if got[0] == 0x88 else got[:4]
            if not sak[0] & 0x04:                       # UID complete
                return uid, sak[0]
        raise NFCError("UID too long")

    def close(self):
        try:
            self.field_off()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- ISO-DEP
FSC_TABLE = [16, 24, 32, 40, 48, 64, 96, 128, 256]


class IsoDep:
    """ISO 14443-4 block protocol over a transceive(frame, timeout) function:
    RATS, I-blocks with chaining both ways, R-ACK, WTX."""

    def __init__(self, transceive):
        self.tx = transceive
        self.bn = 0
        self.fsc = 32
        self.fwt_ms = 77

    def rats(self) -> bytes:
        ats = self.tx(bytes([0xE0, 0x80]), 10)        # FSD 256, CID 0
        if not ats or ats[0] != len(ats):
            raise NFCError("bad ATS")
        t0 = ats[1] if len(ats) > 1 else 0x02
        self.fsc = FSC_TABLE[min(t0 & 0x0F, 8)]
        i = 2 + bool(t0 & 0x10)                        # skip TA
        if t0 & 0x20 and len(ats) > i:                 # TB: FWI in the high nibble
            fwi = ats[i] >> 4
            self.fwt_ms = min(5000, 256 * 16 / FC * (1 << fwi) * 1000 + 5)
        self.bn = 0
        return ats

    def exchange(self, inf: bytes) -> bytes:
        room = self.fsc - 3                            # PCB + CRC
        chunks = [inf[i:i + room] for i in range(0, len(inf), room)] or [b""]
        for k, chunk in enumerate(chunks):
            last = k == len(chunks) - 1
            pcb = 0x02 | self.bn | (0 if last else 0x10)
            resp = self._send(bytes([pcb]) + chunk)
            self.bn ^= 1
            if not last:
                if resp[0] & 0xF6 != 0xA2:            # expect R(ACK)
                    raise NFCError("card didn't acknowledge")
        out = b""
        while True:
            if resp[0] & 0xE2 != 0x02:
                raise NFCError("unexpected block from the card")
            out += resp[1:]
            if not resp[0] & 0x10:
                return out
            resp = self._send(bytes([0xA2 | self.bn]))     # R(ACK): next part
            self.bn ^= 1

    def _send(self, frame: bytes) -> bytes:
        resp = self.tx(frame, self.fwt_ms)
        for _ in range(60):                            # S(WTX): card wants more time
            if not resp or resp[0] & 0xF7 != 0xF2:
                break
            wtxm = resp[1] & 0x3F
            resp = self.tx(bytes([0xF2, wtxm]), self.fwt_ms * max(1, wtxm))
        if not resp:
            raise NFCError("empty block")
        return resp

    def apdu(self, data: bytes) -> tuple[bytes, int]:
        r = self.exchange(data)
        if len(r) < 2:
            raise NFCError("short APDU reply")
        return r[:-2], int.from_bytes(r[-2:], "big")


# --------------------------------------------------------------- readers
class Tag:
    def __init__(self, uid: bytes, transmit):
        self.uid = uid
        self.transmit = transmit


class Reader:
    """Hardware reader: poll() finds one ISO-DEP card, or returns None."""
    kind = "ST25R3916"

    def __init__(self, chip: ST25R3916):
        self.chip = chip

    def poll(self) -> Tag | None:
        if not self.chip.field:
            self.chip.field_on()
        if self.chip.reqa() is None:
            return None
        uid, sak = self.chip.select()
        if not sak & 0x20:
            raise NFCError("that card doesn't speak ISO-DEP (not a tap card)")
        dep = IsoDep(self.chip.transceive)
        dep.rats()
        return Tag(uid, dep.apdu)

    def stop(self):
        self.chip.field_off()

    def close(self):
        self.chip.close()


class SimReader:
    """Simulator: a card is in the field while `present` is True."""
    kind = "simulated"

    def __init__(self, card):
        self.card = card
        self.present = False
        self.field = False

    def poll(self) -> Tag | None:
        self.field = True
        if not self.present:
            return None
        return Tag(b"\x04\x5a\x11\x22\x33\x44\x55", self.card.transmit)

    def stop(self):
        self.field = False

    def close(self):
        self.field = False


# ---------------------------------------------------------------- CC1101
class CC1101:
    """Only ever reset and put to sleep."""
    SRES, SIDLE, SPWD = 0x30, 0x36, 0x39
    PARTNUM, VERSION = 0x30, 0x31

    def __init__(self, spi: SPI):
        self.spi = spi

    def status(self, reg: int) -> int:
        return self.spi.xfer([reg | 0xC0, 0])[1]            # burst bit = status register

    def sleep(self) -> dict:
        self.spi.xfer([self.SRES])
        time.sleep(0.002)
        part, ver = self.status(self.PARTNUM), self.status(self.VERSION)
        self.spi.xfer([self.SIDLE])
        self.spi.xfer([self.SPWD])
        ok = part == 0x00 and ver in (0x04, 0x14)
        return {"state": "asleep" if ok else "unknown", "version": ver}


# ---------------------------------------------------------------- discovery
_SIM: dict = {}


def sim_reader() -> SimReader | None:
    return _SIM.get("reader")


def open_reader() -> tuple[object | None, dict]:
    """(reader or None, status for HARDWARE). Also puts the CC1101 to sleep."""
    mode = os.environ.get("CZ_NFC", "auto")
    if mode == "none":
        return None, {"state": "absent"}
    if mode.startswith("sim"):
        if "reader" not in _SIM:
            from core.cktap import SimSatscard, SimTapsigner
            card = SimTapsigner() if mode == "sim-tapsigner" else SimSatscard()
            _SIM["reader"] = SimReader(card)
        return _SIM["reader"], {"state": "ready", "kind": "simulated",
                                "subghz": {"state": "asleep"}}
    st: dict = {"state": "absent"}
    try:
        sub = CC1101(_spi_from_env("CZ_CC1101_SPI", "0.1", mode=0)).sleep()
    except Exception as e:  # noqa: BLE001
        sub = {"state": "absent", "error": str(e)[:40]}
    try:
        chip = ST25R3916(_spi_from_env("CZ_NFC_SPI", "0.0", mode=1),
                         vdd_5v=os.environ.get("CZ_NFC_5V") == "1")
        chip.start()
        kind, rev = chip.identity()
        st = {"state": "ready", "kind": f"ST25R3916 rev {rev}", "subghz": sub}
        return Reader(chip), st
    except Exception as e:  # noqa: BLE001 - no Cap, no spidev, wrong bus
        st = {"state": "absent", "error": str(e)[:40], "subghz": sub}
        return None, st
