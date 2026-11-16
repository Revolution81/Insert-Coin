"""
Signer daemon and its client.

The daemon runs as the `czsigner` user (see deploy/czsigner.service, which
also blocks all network address families for it). It does two jobs:

1. Answers the app's requests on a Unix socket, one JSON object per line:
       {"method": "describe", "params": {"psbt": "..."}}
    -> {"ok": true, "result": {...}}  or  {"ok": false, "error": "...", "type": "..."}
   Only the methods in core/trusted.py exist: none takes a PIN or returns
   seed words. Only the app's user id may connect (SO_PEERCRED).

2. Owns the display and keyboard (the trusted path, ui/console.py): it
   shows the app's frames and passes keys to it, except while it's asking
   you something itself.

For development, SignerClient(inproc=True) runs all of this in-process;
the App then routes keys and frames through the same Console.
"""
from __future__ import annotations

import json
import os
import socket
import socketserver
import struct
import sys
import threading
import time

from .config import SIGNER_DIR, SIGNER_SOCKET
from .signer import Signer
from .trusted import PROMPTS, SAFE, TrustedSigner
from .vault import ChipMissing, Wiped, WipePinUsed, WrongPin

METHODS = SAFE | PROMPTS


class RemoteError(Exception):
    def __init__(self, message: str, kind: str, extra: dict | None = None):
        super().__init__(message)
        self.kind = kind
        self.extra = extra or {}


def _dispatch(signer: TrustedSigner, req: dict) -> dict:
    method = req.get("method") if isinstance(req, dict) else None
    params = req.get("params", {}) if isinstance(req, dict) else None
    if method not in METHODS or not isinstance(params, dict):
        return {"ok": False, "error": "unknown method", "type": "BadRequest"}
    try:
        result = getattr(signer, method)(**params)
        return {"ok": True, "result": result}
    except WrongPin as e:
        return {"ok": False, "error": str(e), "type": "WrongPin",
                "extra": {"tries_left": e.tries_left}}
    except WipePinUsed:
        return {"ok": False, "error": "wiped", "type": "FreshStart"}
    except Wiped as e:
        return {"ok": False, "error": str(e), "type": "Wiped"}
    except ChipMissing as e:
        return {"ok": False, "error": str(e), "type": "ChipMissing"}
    except TypeError as e:            # wrong parameters
        return {"ok": False, "error": str(e), "type": "BadRequest"}
    except Exception as e:  # noqa: BLE001 - every error goes back to the UI
        return {"ok": False, "error": str(e), "type": type(e).__name__}


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        creds = self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                        struct.calcsize("3i"))
        _, uid, _ = struct.unpack("3i", creds)
        if self.server.allowed_uid is not None and uid not in (self.server.allowed_uid, 0):
            return
        for line in self.rfile:
            try:
                req = json.loads(line)
            except ValueError:
                resp = {"ok": False, "error": "bad json", "type": "BadRequest"}
            else:
                resp = _dispatch(self.server.trusted, req)
            self.wfile.write(json.dumps(resp).encode() + b"\n")
            self.wfile.flush()


# Unix sockets exist only on Linux/macOS; the desktop simulator on Windows
# imports this module but never starts the server.
_UnixServer = getattr(socketserver, "UnixStreamServer", socketserver.TCPServer)


class _Server(socketserver.ThreadingMixIn, _UnixServer):
    daemon_threads = True


def serve(sock_path: str = SIGNER_SOCKET, data_dir: str = SIGNER_DIR,
          allowed_uid: int | None = None, screen: bool = True) -> None:
    signer = Signer(data_dir, auto_lock_s=int(os.environ.get("CZ_AUTO_LOCK", 120)))
    trusted = TrustedSigner(signer)
    os.makedirs(os.path.dirname(sock_path), exist_ok=True)
    if os.path.exists(sock_path):
        os.remove(sock_path)
    srv = _Server(sock_path, _Handler)
    srv.trusted, srv.allowed_uid = trusted, allowed_uid
    os.chmod(sock_path, 0o660)        # group = app user's group (set by systemd unit)
    if not screen:
        srv.serve_forever()
        return
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    run_screen(trusted.console, allowed_uid)


def run_screen(console, allowed_uid: int | None = None):
    """The signer's display loop: keyboard in, frames out."""
    from ui.display import Display
    from ui.input import KeyboardDevice
    from .screen_link import ScreenServer
    link = ScreenServer(allowed_uid=allowed_uid).start()
    console.listeners.append(link.send_mode)
    disp, kb = Display("frame.png"), KeyboardDevice()
    frame, last_blink = 0, time.monotonic()
    disp.show(console.compose(None, frame))
    while True:
        keys = kb.poll(0.03)
        for name, value in keys:
            if not console.feed(name, value):
                link.send_key(name, value)
        changed = console.pump() or bool(keys) or link.take_fresh()
        now = time.monotonic()
        if now - last_blink >= 0.5:
            frame ^= 1
            last_blink = now
            changed = changed or console.active
        if changed or console.animated:
            disp.show(console.compose(link.frame(), frame))


class SignerClient:
    """Same methods as Signer, over the socket (or in-process for dev)."""

    def __init__(self, sock_path: str = SIGNER_SOCKET, inproc: bool | None = None,
                 data_dir: str = SIGNER_DIR, sync_jobs: bool = False):
        if inproc is None:
            inproc = os.environ.get("CZ_SIGNER_INPROC") == "1"
        self._local = TrustedSigner(Signer(data_dir), sync_jobs=sync_jobs) if inproc else None
        # in-process only: the App draws and feeds keys through this console
        self.console = self._local.console if inproc else None
        self._path = sock_path
        self._tls = threading.local()     # one connection per thread: prompts block theirs

    @property
    def local(self):
        """The real Signer (in-process only; tests use it to skip the screens)."""
        return self._local.signer

    def _call(self, method: str, **params):
        req = {"method": method, "params": params}
        if self._local is not None:
            resp = _dispatch(self._local, req)
        else:
            f = getattr(self._tls, "f", None)
            if f is None:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(self._path)
                f = self._tls.f = s.makefile("rwb")
            try:
                f.write(json.dumps(req).encode() + b"\n")
                f.flush()
                resp = json.loads(f.readline())
            except (OSError, ValueError):
                self._tls.f = None
                raise
        if not resp["ok"]:
            raise RemoteError(resp["error"], resp["type"], resp.get("extra"))
        return resp["result"]

    def __getattr__(self, name):
        if name in METHODS:
            return lambda **kw: self._call(name, **kw)
        raise AttributeError(name)


if __name__ == "__main__":
    uid = os.environ.get("CZ_APP_UID") or (sys.argv[1] if len(sys.argv) > 1 else None)
    serve(allowed_uid=int(uid) if uid else None,
          screen=os.environ.get("CZ_HEADLESS") != "1")
