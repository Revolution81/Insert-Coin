"""
M5Stack Unit Fingerprint2 (A-K323CP) on the Grove port in UART mode.

Frame format (from M5Stack's MIT-licensed UIFlow driver):
  EF 01 | FF FF FF FF | type(1) | length(2, BE) = len(payload)+2 | payload | checksum(2, BE)
  checksum = (type + length bytes + payload bytes) & 0xFFFF
Replies: confirmation code at byte 9 (0x00 = OK).

The sensor matches fingerprints itself and only answers "match, id, score",
so a fingerprint is a convenience, never a key: it can reopen the screen
while the signer is still unlocked from a PIN entry, nothing more.
"""
from __future__ import annotations

import os
import time

PORTS = ("/dev/serial0", "/dev/ttyAMA0", "/dev/ttyS0")
MAX_ID = 99


class FingerError(Exception):
    pass


def frame(payload: bytes, ptype: int = 0x01, address: bytes = b"\xff\xff\xff\xff") -> bytes:
    length = len(payload) + 2
    csum = (ptype + (length >> 8) + (length & 0xFF) + sum(payload)) & 0xFFFF
    return b"\xef\x01" + address + bytes([ptype]) + length.to_bytes(2, "big") + payload + \
        csum.to_bytes(2, "big")


class SerialPort:
    """Minimal 115200 8N1 raw serial with termios (no extra packages)."""

    def __init__(self, path: str):
        import termios
        import tty
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        tty.setraw(self.fd)
        attrs = termios.tcgetattr(self.fd)
        attrs[4] = attrs[5] = termios.B115200
        attrs[2] |= termios.CLOCAL | termios.CREAD
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        self._termios = termios
        self.heard = 0                                  # bytes received, for the HARDWARE screen

    def exchange(self, data: bytes, timeout: float = 1.0) -> bytes:
        # a reply that came in late (after its command gave up) must not be
        # taken as the answer to this one: drop whatever is already there
        self._termios.tcflush(self.fd, self._termios.TCIFLUSH)
        end = time.monotonic() + 0.03
        while time.monotonic() < end:
            try:
                os.read(self.fd, 256)
            except BlockingIOError:
                time.sleep(0.005)
        os.write(self.fd, data)
        buf, end = b"", time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                buf += os.read(self.fd, 256)
            except BlockingIOError:
                pass
            if len(buf) >= 9:
                need = 9 + int.from_bytes(buf[7:9], "big")
                if len(buf) >= need:
                    self.heard += len(buf)
                    return buf[:need]
            time.sleep(0.005)
        self.heard += len(buf)
        return buf

    def close(self):
        os.close(self.fd)


class Fingerprint:
    def __init__(self, port):
        self.port = port

    @staticmethod
    def _valid(rx: bytes) -> bool:
        return len(rx) >= 12 and rx[:2] == b"\xef\x01" and rx[6] == 0x07

    def _cmd(self, payload: bytes, timeout: float = 1.0) -> bytes:
        rx = self.port.exchange(frame(payload), timeout)
        if not self._valid(rx) and payload[:1] != b"\xd4":
            # Unit Fingerprint2 goes to sleep after 10 s without a command
            # (its default auto-sleep mode) and then ignores everything but
            # "activate": wake it and ask once more
            self.port.exchange(frame(b"\xd4"), 1.0)
            time.sleep(0.2)
            rx = self.port.exchange(frame(payload), timeout)
        if not self._valid(rx):
            raise FingerError("fingerprint sensor didn't answer")
        return rx

    def _ok(self, payload: bytes, timeout: float = 1.0) -> bool:
        try:
            return self._cmd(payload, timeout)[9] == 0x00
        except FingerError:
            return False

    # ----------------------------------------------------------------- api
    def connected(self) -> bool:
        return self._ok(b"\x35")

    def wake(self) -> bool:
        return self._ok(b"\xd4")

    def led(self, color: int) -> None:
        """0 off, bit0 blue, bit1 green, bit2 red."""
        self._ok(b"\x3c" + (bytes([4, 0, 0, 0]) if color == 0 else bytes([3, color, color, 0])))

    def count(self) -> int:
        rx = self._cmd(b"\x1d", 2.0)
        if rx[9] != 0 or len(rx) < 14:
            raise FingerError("sensor didn't give its finger count")
        return int.from_bytes(rx[10:12], "big")

    def capture(self, enroll: bool = False) -> bool:
        """True if a finger was on the sensor and an image was taken."""
        return self._ok(b"\x29" if enroll else b"\x01")

    def features(self) -> bool:
        return self._ok(b"\x02")

    # building, storing and erasing write the sensor's flash: they can take
    # well over a second, and a reply cut off by a short timeout used to
    # arrive as the "answer" to the next command (count read 0 after a save)
    def template(self) -> bool:
        return self._ok(b"\x05", 3.0)

    def store(self, slot: int) -> bool:
        return self._ok(b"\x06\x01" + slot.to_bytes(2, "big"), 3.0)

    def search(self) -> tuple[int, int] | None:
        rx = self._cmd(b"\x04\x01" + (0).to_bytes(2, "big") + MAX_ID.to_bytes(2, "big"))
        if len(rx) >= 16 and rx[9] == 0:
            return int.from_bytes(rx[10:12], "big"), int.from_bytes(rx[12:14], "big")
        return None

    def delete_all(self) -> bool:
        return self._ok(b"\x0d", 5.0)

    def settled_count(self, tries: int = 4) -> int | None:
        """The count once the sensor has finished writing (None if it won't say)."""
        for _ in range(tries):
            time.sleep(0.3)
            try:
                return self.count()
            except FingerError:
                continue
        return None

    # --------------------------------------------------------- high level
    def verify_once(self) -> int | None:
        """One attempt: the matched slot if a known finger is on the sensor."""
        if not self.capture():
            return None
        if not self.features():
            return None
        hit = self.search()
        return hit[0] if hit else None


class SimulatedSensor:
    """Speaks the same frames. Tests put a finger on with .finger = name."""

    def __init__(self):
        self.finger: str | None = None
        self.stored: dict[int, str] = {}
        self.features: list[str] = []
        self.template: str | None = None
        self.image: str | None = None

    def exchange(self, data: bytes, timeout: float = 1.0) -> bytes:
        assert data[:6] == b"\xef\x01\xff\xff\xff\xff"
        length = int.from_bytes(data[7:9], "big")
        payload = data[9:9 + length - 2]
        csum = int.from_bytes(data[9 + length - 2:9 + length], "big")
        if csum != (data[6] + data[7] + data[8] + sum(payload)) & 0xFFFF:
            return self._ack(0x01)
        op = payload[0]
        if getattr(self, "asleep", False):              # like the real one: only "activate" wakes it
            if op != 0xD4:
                return b""
            self.asleep = False
        if op in (0x35, 0xD4, 0x3C):
            return self._ack(0)
        if op == 0x1D:
            return self._ack(0, len(self.stored).to_bytes(2, "big"))
        if op in (0x01, 0x29):
            self.image = self.finger
            return self._ack(0 if self.finger else 0x02)
        if op == 0x02:
            if not self.image:
                return self._ack(0x06)
            self.features.append(self.image)
            return self._ack(0)
        if op == 0x05:
            ok = len(self.features) >= 2 and len(set(self.features)) == 1
            self.template = self.features[0] if ok else None
            self.features = []
            return self._ack(0 if ok else 0x0A)
        if op == 0x06:
            slot = int.from_bytes(payload[2:4], "big")
            if self.template is None:
                return self._ack(0x0B)
            self.stored[slot] = self.template
            return self._ack(0)
        if op == 0x04:
            probe = self.features[-1] if self.features else None
            self.features = []
            for slot, who in self.stored.items():
                if who == probe:
                    return self._ack(0, slot.to_bytes(2, "big") + (180).to_bytes(2, "big"))
            return self._ack(0x09)
        if op == 0x0D:
            self.stored.clear()
            return self._ack(0)
        return self._ack(0x01)

    @staticmethod
    def _ack(code: int, extra: bytes = b"") -> bytes:
        body = bytes([code]) + extra
        return frame(body, ptype=0x07)

    def close(self):
        pass


_SIM = None


def open_sensor() -> tuple[Fingerprint | None, dict]:
    """CZ_FINGER=sim uses the simulator; =none skips probing."""
    global _SIM
    mode = os.environ.get("CZ_FINGER")
    if mode == "none":
        return None, {"state": "absent"}
    if mode == "sim":
        _SIM = _SIM or SimulatedSensor()
        f = Fingerprint(_SIM)
        return f, {"state": "ready", "saved": f.count(), "port": "sim"}
    errors, seen = [], set()
    for path in PORTS:
        if not os.path.exists(path):
            continue
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen.add(real)
        name = os.path.basename(real)
        try:
            f = Fingerprint(SerialPort(path))
        except OSError as e:
            errors.append(f"{name}: {e.strerror or e}"[:40])
            continue
        try:
            f.wake()
            if f.connected():
                return f, {"state": "ready", "saved": f.settled_count(2) or 0, "port": path}
            heard = getattr(f.port, "heard", 0)
            errors.append(f"{name}: no answer" if not heard else f"{name}: {heard} bytes, garbled")
        except (OSError, FingerError) as e:
            errors.append(f"{name}: {e}"[:40])
        f.port.close()
    if not seen:
        errors.append("No serial port (UART off)")
    return None, {"state": "absent", "errors": errors}


def simulator() -> SimulatedSensor | None:
    return _SIM
