"""
czscan: the camera and QR decoder in a process of their own.

Decoding a QR means feeding camera pixels to zbar, a C library, and
picamera2/libcamera, a large C++ stack. A booby-trapped QR that exploited
a bug there would run code in whatever process did the decoding. So that
process is this one, as the `czscan` user, with nothing worth having: no
network (its own empty network namespace), no wallet or signer files, no
screen, no keyboard, no keys. All it can do is look through the camera and
send back what it read.

Wire (/run/czscan/scan.sock, one client, uid-checked):

  app -> czscan   b"G"   start scanning continuously
                  b"1"   scan exactly one frame, then answer b"D" (tests)
                  b"S"   stop (camera off)
                  b"N"   hash a few raw frames for seed randomness
                         (camera on just for that), answer b"R"
  czscan -> app   b"V" + 132*99 bytes        grayscale preview, fixed size
                  b"T" + 2-byte length + UTF-8  decoded text (<= 8 KB)
                  b"E" + 1-byte length + text   camera error
                  b"D"                       single frame done
                  b"R" + 1-byte length + hash  camera noise (32 bytes, or 0)

The camera is open only between G/1 and S (or the app disconnecting).
The app parses only these fixed shapes; a malformed message drops the
connection. What it gets is exactly what a QR code could have said anyway.
"""
from __future__ import annotations

import os
import socket
import struct
import threading

from PIL import ImageOps

PREVIEW = (132, 99)
PREVIEW_BYTES = PREVIEW[0] * PREVIEW[1]
MAX_TEXT = 8192
SCAN_SOCKET = os.environ.get("CZ_SCAN_SOCK", "/run/czscan/scan.sock")


def _preview_bytes(img) -> bytes:
    from PIL import Image
    p = ImageOps.autocontrast(img.convert("L")).resize(PREVIEW, Image.BILINEAR)
    return p.tobytes()


class Session:
    """One app connection."""

    def __init__(self, conn: socket.socket, camera_factory, decode):
        self.conn = conn
        self.camera_factory = camera_factory
        self.decode = decode
        self.camera = None
        self._send_lock = threading.Lock()
        self._loop: threading.Thread | None = None
        self._stop = threading.Event()
        self._cam_lock = threading.Lock()

    # ------------------------------------------------------------ output
    def _send(self, data: bytes):
        with self._send_lock:
            self.conn.sendall(data)

    def _text(self, text: str):
        raw = text.encode("utf-8")
        if len(raw) <= MAX_TEXT:
            self._send(b"T" + struct.pack(">H", len(raw)) + raw)

    def _error(self, msg: str):
        raw = msg.encode("utf-8", "replace")[:200]
        self._send(b"E" + bytes([len(raw)]) + raw)

    # ------------------------------------------------------------ camera
    def _open(self) -> bool:
        if self.camera is None:
            try:
                self.camera = self.camera_factory()
            except Exception as e:  # noqa: BLE001 - CameraError, missing driver...
                self._error(str(e)[:60] or "camera not available")
                return False
        return True

    def _close(self):
        self._stop.set()
        if self._loop and self._loop is not threading.current_thread():
            self._loop.join(timeout=2)
        self._loop = None
        with self._cam_lock:
            if self.camera is not None:
                try:
                    self.camera.close()
                finally:
                    self.camera = None

    def _noise(self) -> bytes:
        from ui.camera import frame_noise
        opened_here = self.camera is None
        if not self._open():
            return b"\x00"
        try:
            with self._cam_lock:
                n = frame_noise(self.camera)
        except Exception:  # noqa: BLE001
            n = b""
        finally:
            if opened_here and self._loop is None:
                self._close()
        return bytes([len(n)]) + n

    def step(self):
        with self._cam_lock:
            if self.camera is None:
                return
            img = self.camera.grab()
        texts = self.decode(img)
        self._send(b"V" + _preview_bytes(img))
        for t in texts:
            self._text(t)

    def _run(self):
        while not self._stop.is_set():
            try:
                self.step()
            except OSError:
                return                         # the app went away
            except Exception:  # noqa: BLE001 - a bad frame: keep going
                self._stop.wait(0.1)

    # -------------------------------------------------------------- loop
    def serve(self):
        f = self.conn.makefile("rb")
        try:
            while True:
                cmd = f.read(1)
                if cmd == b"G":
                    if self._open() and self._loop is None:
                        self._stop.clear()
                        self._loop = threading.Thread(target=self._run, daemon=True)
                        self._loop.start()
                elif cmd == b"1":
                    if self._open():
                        try:
                            self.step()
                        except OSError:
                            raise
                        except Exception:  # noqa: BLE001
                            pass
                    self._send(b"D")
                elif cmd == b"S":
                    self._close()
                elif cmd == b"N":
                    self._send(b"R" + self._noise())
                else:
                    break                      # EOF or garbage
        except OSError:
            pass
        finally:
            self._close()
            self.conn.close()


def serve(path: str = SCAN_SOCKET, allowed_uid: int | None = None, camera_factory=None):
    from ui.camera import decode, open_camera
    camera_factory = camera_factory or open_camera
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if os.path.exists(path):
        os.remove(path)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    os.chmod(path, 0o660)
    srv.listen(1)
    while True:
        conn, _ = srv.accept()
        creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _, uid, _ = struct.unpack("3i", creds)
        if allowed_uid is not None and uid not in (allowed_uid, 0):
            conn.close()
            continue
        Session(conn, camera_factory, decode).serve()   # one app at a time


if __name__ == "__main__":
    uid = os.environ.get("CZ_APP_UID")
    serve(allowed_uid=int(uid) if uid else None)
