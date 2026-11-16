"""
Camera + QR decoding.

On the device: the IMX219 through picamera2 (apt install python3-picamera2),
grayscale frames decoded by zbar (apt install libzbar0, pip install pyzbar).
Anywhere else, FakeCamera plays back a list of images, which is how the tests
"scan" generated QR codes.

The camera runs in its own thread; the scan screen just reads the latest
preview frame and whatever text was decoded.

On the device the camera and zbar don't run in the app at all: they run in
the sandboxed czscan service (core/scand.py), and the app uses
RemoteScanner, which has the same interface as Scanner.
"""
from __future__ import annotations

import hashlib
import os
import socket
import struct
import threading
import time

from PIL import Image, ImageOps

FRAME = (640, 480)


class CameraError(Exception):
    pass


class PiCamera:
    def __init__(self):
        try:
            from picamera2 import Picamera2
        except ImportError as e:
            raise CameraError("camera software missing (python3-picamera2)") from e
        try:
            self.cam = Picamera2()
            cfg = self.cam.create_preview_configuration(
                main={"size": FRAME, "format": "YUV420"}, buffer_count=2)
            self.cam.configure(cfg)
            self.cam.start()
        except Exception as e:  # noqa: BLE001 - no camera, busy, etc.
            raise CameraError(f"camera not available: {e}") from e

    def grab(self) -> Image.Image:
        arr = self.cam.capture_array("main")          # YUV420: first H rows are Y
        return Image.fromarray(arr[:FRAME[1], :FRAME[0]], "L")

    def close(self):
        try:
            self.cam.stop()
            self.cam.close()
        except Exception:  # noqa: BLE001
            pass


class FakeCamera:
    """Loops through images (for tests and the desktop)."""

    def __init__(self, images: list[Image.Image], delay: float = 0.0):
        self.images = [im.convert("L") for im in images] or [Image.new("L", FRAME, 90)]
        self.i = 0
        self.delay = delay

    def grab(self) -> Image.Image:
        if self.delay:
            time.sleep(self.delay)
        im = self.images[self.i % len(self.images)]
        self.i += 1
        return im

    def close(self):
        pass


def decode(img: Image.Image) -> list[str]:
    from pyzbar import pyzbar
    out = []
    for sym in pyzbar.decode(img, symbols=[pyzbar.ZBarSymbol.QRCODE]):
        try:
            out.append(sym.data.decode("utf-8"))
        except UnicodeDecodeError:
            continue
    return out


def frame_noise(camera, frames: int = 4) -> bytes:
    """Hash of a few full-resolution frames: camera sensors are noisy, so
    even a still scene in the dark gives fresh random bits each frame.
    Returns b"" if every frame came out identical (no noise to use)."""
    h = hashlib.sha256(b"czwallet-camera-noise-v1")
    seen = set()
    for _ in range(frames):
        raw = camera.grab().tobytes()
        d = hashlib.sha256(raw).digest()
        seen.add(d)
        h.update(d)
    return h.digest() if len(seen) > 1 else b""


def open_camera() -> PiCamera:
    return PiCamera()


class Scanner:
    """Background loop: grab, decode, hand texts to on_text (any thread),
    and keep a small preview image for the screen."""

    def __init__(self, camera, on_text, preview_size=(132, 99)):
        self.camera = camera
        self.on_text = on_text
        self.preview_size = preview_size
        self.preview: Image.Image | None = None
        self.error = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def step(self) -> list[str]:
        img = self.camera.grab()
        texts = decode(img)
        self.preview = ImageOps.autocontrast(img).resize(self.preview_size, Image.BILINEAR)
        for t in texts:
            self.on_text(t)
        return texts

    def start(self):
        def loop():
            while not self._stop.is_set():
                try:
                    self.step()
                except Exception:  # noqa: BLE001 - keep scanning through glitches
                    time.sleep(0.1)
        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        self.camera.close()


class ScanLinkError(Exception):
    pass


class RemoteScanner:
    """Scanner's interface, backed by the czscan service. Everything coming
    back is checked against the few fixed message shapes in core/scand.py;
    anything else ends the session."""

    def __init__(self, on_text, path: str | None = None, sock: socket.socket | None = None):
        from core.scand import SCAN_SOCKET
        self.on_text = on_text
        self.preview: Image.Image | None = None
        self.error = ""
        if sock is None:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.connect(path or os.environ.get("CZ_SCAN_SOCK", SCAN_SOCKET))
            except OSError as e:
                sock.close()
                raise CameraError("scanner service not running") from e
        self.sock = sock
        self.f = sock.makefile("rb")
        self._reader: threading.Thread | None = None
        self._closed = False

    def _read_one(self) -> bytes:
        """Read and act on one message; returns its kind."""
        from core.scand import MAX_TEXT, PREVIEW, PREVIEW_BYTES
        kind = self.f.read(1)
        if kind == b"V":
            data = self.f.read(PREVIEW_BYTES)
            if len(data) != PREVIEW_BYTES:
                raise ScanLinkError("short preview")
            self.preview = Image.frombytes("L", PREVIEW, data)
        elif kind == b"T":
            hdr = self.f.read(2)
            if len(hdr) != 2:
                raise ScanLinkError("short text")
            (n,) = struct.unpack(">H", hdr)
            if n > MAX_TEXT:
                raise ScanLinkError("text too long")
            raw = self.f.read(n)
            if len(raw) != n:
                raise ScanLinkError("short text")
            try:
                self.on_text(raw.decode("utf-8"))
            except UnicodeDecodeError as e:
                raise ScanLinkError("bad text") from e
        elif kind == b"E":
            n = self.f.read(1)
            raw = self.f.read(n[0]) if n else b""
            self.error = raw.decode("utf-8", "replace")[:60] or "camera error"
        elif kind == b"D":
            pass
        elif kind == b"R":
            n = self.f.read(1)
            if not n or n[0] not in (0, 32):
                raise ScanLinkError("bad noise reply")
            raw = self.f.read(n[0])
            if len(raw) != n[0]:
                raise ScanLinkError("short noise reply")
            self.noise_result = raw
        elif kind == b"":
            raise ScanLinkError("scanner closed")
        else:
            raise ScanLinkError("garbage from scanner")
        return kind

    def _read_loop(self):
        try:
            while not self._closed:
                self._read_one()
        except (ScanLinkError, OSError) as e:
            if not self._closed:
                self.error = f"scanner stopped: {e}"[:60]

    def start(self):
        self.sock.sendall(b"G")
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def step(self):
        """One frame, synchronously (tests)."""
        self.sock.sendall(b"1")
        try:
            while self._read_one() != b"D":
                pass
        except (ScanLinkError, OSError) as e:
            self.error = f"scanner stopped: {e}"[:60]

    def noise(self) -> bytes:
        """Camera noise from czscan (see frame_noise), or b"" on any problem."""
        self.noise_result = b""
        try:
            self.sock.sendall(b"N")
            while self._read_one() != b"R":
                pass
        except (ScanLinkError, OSError):
            return b""
        return self.noise_result

    def stop(self):
        self._closed = True
        try:
            self.sock.sendall(b"S")
        except OSError:
            pass
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()
