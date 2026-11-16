"""
PRINT for paper wallets: send one straight to a printer on your home Wi-Fi.

On the device this runs in its own service, czprint (tools/print_service.py,
deploy/czprint.service): the only program besides SHARE FILES that can read
the paper folder, allowed onto the local network and nowhere else, and it
never sees the seed. The app asks it, over a local socket, for four things:

  files     the paper wallets there are (their addresses, which are public)
  scan      printers found on the network
  check     what a printer is and its certificate fingerprint (nothing secret)
  print     one paper wallet to one printer you've trusted

Plus trust, which pins a printer's certificate once you've compared its
fingerprint. print refuses a printer that isn't trusted, re-checks the pinned
certificate before sending, sends only to local IP addresses (see core/ipp),
and only over TLS. In the simulator the same class runs inside the app.
"""
from __future__ import annotations

import json
import os
import re
import socket

from . import ipp, mdns
from . import paper as paperlib

SOCKET = "/run/czprint/print.sock"
ADDRESS = re.compile(r"^[a-zA-Z0-9]{14,90}$")
MAX_FILE = 64 * 1024


class PrintError(Exception):
    pass


class PrintService:
    def __init__(self, paper_dir: str, state_dir: str, allow_loopback: bool = False):
        self.paper_dir = paper_dir
        self.state_dir = state_dir
        self.allow_loopback = allow_loopback

    # --------------------------------------------------------------- pins
    def _pins_path(self) -> str:
        return os.path.join(self.state_dir, "printers.json")

    def _pins(self) -> dict:
        try:
            with open(self._pins_path()) as f:
                v = json.load(f)
            return v if isinstance(v, dict) else {}
        except (FileNotFoundError, ValueError):
            return {}

    def _save_pins(self, pins: dict) -> None:
        os.makedirs(self.state_dir, mode=0o700, exist_ok=True)
        tmp = self._pins_path() + ".tmp"
        with open(tmp, "w") as f:
            json.dump(pins, f)
        os.replace(tmp, self._pins_path())

    def _key(self, host: str, port: int) -> str:
        return f"{host}:{port}"

    # --------------------------------------------------------------- the four
    def files(self) -> list[str]:
        try:
            names = os.listdir(self.paper_dir)
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            return []
        return sorted(n[:-4] for n in names if n.endswith(".txt") and ADDRESS.match(n[:-4]))

    def scan(self, timeout: float = 2.5) -> list[dict]:
        pins = self._pins()
        found = mdns.browse(timeout)
        for p in found:
            p["trusted"] = self._key(p["host"], p["port"]) in pins
        return found

    def check(self, host: str, port: int = 631, path: str = "ipp/print") -> dict:
        host = ipp.local_ip(host, self.allow_loopback)
        path = ipp.check_path(path)
        port = _port(port)
        info = ipp.get_attributes(host, port, path)
        pinned = self._pins().get(self._key(host, port))
        info.update(host=host, port=port, path=path,
                    trusted=pinned == info["fingerprint"],
                    changed=bool(pinned) and pinned != info["fingerprint"],
                    short=ipp.short_fp(info["fingerprint"]))
        return info

    def trust(self, host: str, port: int, fingerprint: str) -> bool:
        host = ipp.local_ip(host, self.allow_loopback)
        port = _port(port)
        if not re.fullmatch(r"[0-9a-f]{64}", fingerprint or ""):
            raise PrintError("bad fingerprint")
        pins = self._pins()
        pins[self._key(host, port)] = fingerprint
        self._save_pins(pins)
        return True

    def forget(self, host: str, port: int) -> bool:
        pins = self._pins()
        pins.pop(self._key(ipp.local_ip(host, self.allow_loopback), _port(port)), None)
        self._save_pins(pins)
        return True

    def print(self, address: str, host: str, port: int = 631, path: str = "ipp/print") -> dict:
        if not ADDRESS.match(address or ""):
            raise PrintError("no such paper wallet")
        host = ipp.local_ip(host, self.allow_loopback)
        path = ipp.check_path(path)
        port = _port(port)
        pin = self._pins().get(self._key(host, port))
        if not pin:
            raise PrintError("trust this printer first")
        info = ipp.get_attributes(host, port, path, pin=pin)   # same certificate as trusted
        fmt = info["format"]
        if not fmt:
            raise PrintError("this printer takes neither JPEG nor PDF pages")

        def page() -> bytes:                                    # read only once it checks out
            w = self._wallet(address)
            return paperlib.jpeg(w) if fmt == "image/jpeg" else paperlib.pdf(w)
        job = ipp.print_job(host, port, path, pin, page, fmt,
                            job_name=f"Paper wallet {address[:10]}")
        return {"job": job, "model": info["model"]}

    def _wallet(self, address: str) -> dict:
        path = os.path.join(self.paper_dir, address + ".txt")
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            raise PrintError("no such paper wallet")
        with os.fdopen(fd, "rb") as f:
            data = f.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            raise PrintError("that file isn't a paper wallet")
        try:
            w = paperlib.from_text(data.decode("utf-8"))
        except (UnicodeDecodeError, paperlib.PaperError):
            raise PrintError("that file isn't a paper wallet")
        if w["address"] != address:
            raise PrintError("that file isn't a paper wallet")
        return w

    def handle(self, method: str, params: dict):
        """One request from the app (the socket server calls this)."""
        if method not in ("files", "scan", "check", "trust", "forget", "print"):
            raise PrintError("unknown request")
        allowed = {"files": (), "scan": (), "check": ("host", "port", "path"),
                   "trust": ("host", "port", "fingerprint"), "forget": ("host", "port"),
                   "print": ("address", "host", "port", "path")}[method]
        if not isinstance(params, dict) or set(params) - set(allowed):
            raise PrintError("bad request")
        need = {"check": {"host"}, "trust": {"host", "port", "fingerprint"},
                "forget": {"host", "port"}, "print": {"address", "host"}}.get(method, set())
        if need - set(params):
            raise PrintError("bad request")
        types = {"host": str, "path": str, "address": str, "fingerprint": str, "port": int}
        for k, v in params.items():
            if not isinstance(v, types[k]) or isinstance(v, bool) or \
                    (isinstance(v, str) and len(v) > 200):
                raise PrintError("bad request")
        return getattr(self, method)(**params)


def _port(port) -> int:
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise PrintError("bad port")
    return port


# ------------------------------------------------------------------ the app side
class Client:
    """The app's handle on czprint, over its socket."""
    def __init__(self, path: str = SOCKET):
        self.path = path

    def call(self, method: str, timeout: float = 90.0, **params):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect(self.path)
            s.sendall(json.dumps({"method": method, "params": params}).encode() + b"\n")
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > 1_000_000:
                    raise PrintError("printing service sent too much")
        except OSError:
            raise PrintError("the printing service isn't running")
        finally:
            s.close()
        try:
            reply = json.loads(buf)
        except ValueError:
            raise PrintError("the printing service sent a broken reply")
        if "error" in reply:
            raise PrintError(str(reply["error"])[:120])
        return reply.get("result")


class InProcess:
    """Simulator: the same service, inside the app."""
    def __init__(self, svc: PrintService):
        self.svc = svc

    def call(self, method: str, timeout: float = 90.0, **params):
        try:
            return self.svc.handle(method, params)
        except (PrintError, ipp.PrinterError) as e:
            raise PrintError(str(e))


def controller():
    if os.environ.get("CZ_SIM") or not os.path.exists(SOCKET):
        from . import config, share
        state = os.path.join(config.APP_DIR, "printing")
        return InProcess(PrintService(share.folders()["paper"], state,
                                      allow_loopback=bool(os.environ.get("CZ_SIM"))))
    return Client()
