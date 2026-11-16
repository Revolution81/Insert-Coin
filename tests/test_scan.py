"""
The QR sandbox: camera and zbar run in czscan (core/scand.py); the app only
gets preview pixels and decoded text over a narrow, checked wire.
"""
import socket
import struct
import threading

import pytest

from core.scand import MAX_TEXT, PREVIEW_BYTES, Session
from ui.camera import CameraError, FakeCamera, RemoteScanner, decode
from ui.flow import HomeScreen
from ui.qrscreens import ScanScreen
from ui.widgets import TextInputScreen

from test_app import app, create_wallet, press  # noqa: F401
from test_core import DEST, fund
from test_qr import camera_frame, pump_until


class CountingCamera(FakeCamera):
    opened = 0
    closed = 0

    def __init__(self, imgs):
        super().__init__(imgs)
        CountingCamera.opened += 1

    def close(self):
        CountingCamera.closed += 1


def scand(camera_factory):
    """A czscan session on one end of a socketpair; the app's end back."""
    a, b = socket.socketpair()
    th = threading.Thread(target=Session(a, camera_factory, decode).serve, daemon=True)
    th.start()
    return b, th


def use_sandbox(app, texts):
    imgs = [camera_frame(t, seed=i) for i, t in enumerate(texts)]
    sessions = []

    def open_scanner(on_text):
        sock, th = scand(lambda: CountingCamera(imgs))
        sessions.append(th)
        return RemoteScanner(on_text, sock=sock)
    app.open_scanner = open_scanner
    return sessions


def test_send_scan_through_the_sandbox(app):  # noqa: F811
    create_wallet(app)
    fund(app.wallet, 100_000)
    CountingCamera.opened = CountingCamera.closed = 0
    sessions = use_sandbox(app, [f"bitcoin:{DEST}?amount=0.0004&label=Coffee"])
    press(app, "KEY_S")
    press(app, "KEY_TAB")
    app.render(0)
    pump_until(app, lambda: isinstance(app.visible, TextInputScreen) and app.visible.title == "AMOUNT")
    assert app.visible.buf.text == "40000"
    sessions[0].join(2)                        # leaving the scan screen ends the session...
    assert CountingCamera.opened == 1 and CountingCamera.closed == 1   # ...and the camera


def test_preview_arrives(app):  # noqa: F811
    create_wallet(app)
    use_sandbox(app, [])                         # nothing to read: keeps scanning
    press(app, "KEY_Q")
    scr = app.visible
    assert isinstance(scr, ScanScreen)
    scr.scanner.step()
    assert scr.scanner.preview is not None and scr.scanner.preview.size == (132, 99)
    app.render(0)
    press(app, "KEY_ESC")


def test_camera_error_comes_through(app):  # noqa: F811
    create_wallet(app)

    def broken():
        raise CameraError("camera not available: test")

    def open_scanner(on_text):
        sock, _ = scand(broken)
        return RemoteScanner(on_text, sock=sock)
    app.open_scanner = open_scanner
    press(app, "KEY_Q")
    app.pump()
    assert isinstance(app.visible, ScanScreen) and "camera" in app.visible.error
    press(app, "KEY_ESC")
    assert isinstance(app.visible, HomeScreen)


def test_scanner_service_missing(app, monkeypatch, tmp_path):  # noqa: F811
    create_wallet(app)
    monkeypatch.setenv("CZ_SCAN_SOCK", str(tmp_path / "nothing.sock"))
    from ui.camera import open_camera
    app.camera_factory = open_camera            # the device default: use czscan
    press(app, "KEY_Q")
    assert isinstance(app.visible, ScanScreen) and "scanner" in app.visible.error


# ------------------------------------------------- a compromised czscan
def fake_scand(payload: bytes):
    a, b = socket.socketpair()

    def serve():
        a.recv(1)
        a.sendall(payload)
        a.close()
    threading.Thread(target=serve, daemon=True).start()
    return b


@pytest.mark.parametrize("payload", [
    b"X" + b"\0" * 50,                                     # unknown message
    b"V" + b"\0" * 10,                                     # short preview
    b"T" + struct.pack(">H", MAX_TEXT + 1) + b"a" * (MAX_TEXT + 1),   # oversize text
    b"T" + struct.pack(">H", 2) + b"\xff\xfe",             # not UTF-8
])
def test_app_refuses_malformed_scanner_output(payload):
    got = []
    r = RemoteScanner(got.append, sock=fake_scand(payload))
    r.step()
    assert got == [] and "scanner stopped" in r.error
    r.stop()


def test_well_formed_output_is_only_text_and_pixels():
    got = []
    msg = (b"V" + bytes(PREVIEW_BYTES) + b"T" + struct.pack(">H", 5) + b"hello" + b"D")
    r = RemoteScanner(got.append, sock=fake_scand(msg))
    r.step()
    assert got == ["hello"] and r.preview.size == (132, 99) and not r.error
    r.stop()


def test_scand_drops_garbage_commands():
    sock, th = scand(lambda: CountingCamera([]))
    sock.sendall(b"Z")
    th.join(2)
    assert not th.is_alive()
    sock.close()


def test_continuous_scanning_and_camera_off_after():
    import queue
    import time
    CountingCamera.opened = CountingCamera.closed = 0
    sock, th = scand(lambda: CountingCamera([camera_frame("hello there")]))
    got = queue.Queue()
    r = RemoteScanner(got.put, sock=sock)
    r.start()
    assert got.get(timeout=5) == "hello there"
    r.stop()
    th.join(3)
    deadline = time.monotonic() + 3
    while CountingCamera.closed < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert CountingCamera.opened == 1 and CountingCamera.closed == 1
