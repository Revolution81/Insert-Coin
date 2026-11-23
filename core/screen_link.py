"""
The wire between the app and the signer's screen (see ui/console.py).

The signer owns the display and the keyboard. Over one Unix socket
(/run/czwallet/screen.sock, uid-checked like the signer's):

  app -> signer   b"F" + W*H*3 bytes of RGB      a frame to show
  signer -> app   b"K" + len + key name + value   a key press for the app
                  b"S" + 0 or 1                   the signer took (1) or
                                                  returned (0) the screen

The signer never parses more of a frame than its fixed size, and only
shows it while it doesn't have the screen itself. Keys pressed while it has
the screen never go out on this socket.
"""
from __future__ import annotations

import os
import socket
import struct
import threading

from PIL import Image

W, H = 320, 170
FRAME = W * H * 3
SCREEN_SOCKET = os.environ.get("CZ_SCREEN_SOCK", "/run/czwallet/screen.sock")


def _read_exact(f, n: int) -> bytes | None:
    data = f.read(n)
    return data if data and len(data) == n else None


class ScreenServer:
    """Signer side. One app connection at a time."""

    def __init__(self, path: str = SCREEN_SOCKET, allowed_uid: int | None = None):
        self.path = path
        self.allowed_uid = allowed_uid
        self._conn: socket.socket | None = None
        self._send = threading.Lock()
        self._frame: bytes | None = None
        self.fresh = False
        self.secure = False

    def start(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        if os.path.exists(self.path):
            os.remove(self.path)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(self.path)
        os.chmod(self.path, 0o660)
        srv.listen(1)
        threading.Thread(target=self._accept, args=(srv,), daemon=True).start()
        return self

    def attach(self, conn: socket.socket):
        """Serve one connection (also used by tests with a socketpair)."""
        with self._send:
            old, self._conn = self._conn, conn
        if old:
            old.close()
        self.send_mode(self.secure)
        threading.Thread(target=self._read, args=(conn,), daemon=True).start()

    def _accept(self, srv):
        while True:
            conn, _ = srv.accept()
            creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            _, uid, _ = struct.unpack("3i", creds)
            if self.allowed_uid is not None and uid not in (self.allowed_uid, 0):
                conn.close()
                continue
            self.attach(conn)

    def _read(self, conn):
        f = conn.makefile("rb")
        try:
            while True:
                kind = f.read(1)
                if kind != b"F":
                    break                       # anything else: drop the connection
                data = _read_exact(f, FRAME)
                if data is None:
                    break
                self._frame = data
                self.fresh = True
        except OSError:
            pass
        finally:
            conn.close()

    def frame(self) -> Image.Image | None:
        data = self._frame
        return Image.frombytes("RGB", (W, H), data) if data else None

    def take_fresh(self) -> bool:
        fresh, self.fresh = self.fresh, False
        return fresh

    def _write(self, data: bytes):
        with self._send:
            if not self._conn:
                return
            try:
                self._conn.sendall(data)
            except OSError:
                self._conn = None

    def send_key(self, name: str, value: int):
        raw = str(name).encode()[:255]
        self._write(b"K" + bytes([len(raw)]) + raw + bytes([value & 0xFF]))

    def send_mode(self, secure: bool):
        self.secure = secure
        self._write(b"S" + bytes([1 if secure else 0]))


class ScreenClient:
    """App side: send frames, receive keys and mode changes."""

    def __init__(self, path: str = SCREEN_SOCKET, sock: socket.socket | None = None):
        if sock is None:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(path)
        self.sock = sock
        self._buf = b""

    def show(self, img: Image.Image):
        self.sock.sendall(b"F" + img.convert("RGB").tobytes())

    def poll(self, timeout: float) -> list[tuple]:
        """[("key", name, value) | ("secure", bool)]"""
        import select
        r, _, _ = select.select([self.sock], [], [], timeout)
        if r:
            data = self.sock.recv(4096)
            if not data:
                raise ConnectionError("the signer closed the screen link")
            self._buf += data
        out = []
        while self._buf:
            k = self._buf[:1]
            if k == b"S" and len(self._buf) >= 2:
                out.append(("secure", self._buf[1] == 1))
                self._buf = self._buf[2:]
            elif k == b"K" and len(self._buf) >= 2 and len(self._buf) >= 3 + self._buf[1]:
                n = self._buf[1]
                out.append(("key", self._buf[2:2 + n].decode(), self._buf[2 + n]))
                self._buf = self._buf[3 + n:]
            elif k in (b"S", b"K"):
                break                           # partial message: wait for the rest
            else:
                raise ConnectionError("garbage on the screen link")
        return out

    def close(self):
        self.sock.close()
