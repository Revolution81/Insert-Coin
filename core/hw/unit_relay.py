"""
The signer's side of the NFC Unit on the side (Grove) port.

In I2C mode the side port is I2C1, the keyboard's bus (keyboard controller,
power chip, battery gauge, audio codec). The app must never open it: it
could read every key you press, PIN included. So only the signer has it,
and it passes the app's NFC transfers on with these limits:

  - address 0x50 only (the NFC Unit's): nothing else on the bus can be
    reached through this;
  - only while the side port is switched to I2C;
  - only after an ST25R3916 has answered its identity register there, and
    again after any failed transfer;
  - one write, or one write then one read, of at most 300 bytes each.

The signer never looks at what's in the bytes: card data is handled by the
app, as for the Cap. Nothing here can touch keys or the screen.
"""
from __future__ import annotations

import os
import threading

from . import grove
from .nfc import I2C, I2C_M_RD, IC_TYPE, REG_IC_ID

BUS = "/dev/i2c-1"
ADDRESS = 0x50
MAX_BYTES = 300


class RelayError(Exception):
    pass


class UnitRelay:
    def __init__(self, bus: str | None = None):
        self.bus_path = bus or os.environ.get("CZ_UNIT_BUS", BUS)
        self._bus = None
        self._verified = False
        self._lock = threading.Lock()

    @staticmethod
    def _check(msgs) -> list[tuple[int, bytes | int]]:
        if not isinstance(msgs, list) or not 1 <= len(msgs) <= 2:
            raise RelayError("one or two messages")
        out = []
        for i, m in enumerate(msgs):
            if not isinstance(m, list) or len(m) != 2 or m[0] not in (0, I2C_M_RD):
                raise RelayError("bad message")
            flags, payload = m
            if flags & I2C_M_RD:
                if i != len(msgs) - 1 or not isinstance(payload, int) or \
                        not 1 <= payload <= MAX_BYTES:
                    raise RelayError("bad read")
                out.append((flags, payload))
            else:
                if not isinstance(payload, str) or len(payload) > 2 * MAX_BYTES:
                    raise RelayError("bad write")
                try:
                    data = bytes.fromhex(payload)
                except ValueError:
                    raise RelayError("bad write") from None
                if not data:
                    raise RelayError("bad write")
                out.append((flags, data))
        if len(out) == 2 and out[0][0] & I2C_M_RD:
            raise RelayError("bad message")
        return out

    def _open(self):
        if self._bus is None:
            self._bus = I2C(self.bus_path, ADDRESS, shared_ok=True)
        if not self._verified:
            ident = self._bus._rdwr([(0, bytes([0x40 | REG_IC_ID])), (I2C_M_RD, 1)])
            if not ident or (ident[0] >> 3) & 0x1F != IC_TYPE:
                raise RelayError("no NFC Unit at 0x50")
            self._verified = True
        return self._bus

    def xfer(self, msgs) -> str:
        wire = self._check(msgs)
        if grove.mode() != "i2c":
            raise RelayError("side port is not in I2C mode")
        with self._lock:
            try:
                return self._open()._rdwr(wire).hex()
            except OSError as e:
                self._verified = False
                raise RelayError(f"NFC Unit: {e}") from None

    def close(self):
        with self._lock:
            if self._bus is not None:
                try:
                    self._bus.close()
                except OSError:
                    pass
            self._bus, self._verified = None, False
