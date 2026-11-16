"""
cznet: the only process that talks to the internet.

It runs as its own user with no keys, no screen, no keyboard and no wallet
files; the app process runs with no IP networking at all and reaches the
Electrum server only through this relay. So a bug in TLS, SOCKS or Electrum
parsing (the code most exposed to strangers) lands in a process that can't
see PIN entry, the seed, labels or balances, and can't sign. The worst it can
do is lie about the blockchain, which the signer's own checks bound.

Protocol over a Unix socket, one JSON object per line:
  {"op": "connect", "server": "ssl://host:50002", "tor": "127.0.0.1:9050" | null}
  {"op": "call", "calls": [["blockchain.scripthash.get_history", ["..."]], ...]}
-> {"ok": true, "result": ...}  or  {"ok": false, "error": "..."}

Only the Electrum methods the wallet uses are relayed, with checked
parameters, and only the app's user may connect (SO_PEERCRED).
"""
from __future__ import annotations

import json
import os
import re
import socket
import socketserver
import struct
import sys
import threading

from .electrum import ElectrumClient, ElectrumError

SOCKET = os.environ.get("CZ_NET_SOCK", "/run/cznet/net.sock")
MAX_LINE = 4 * 1024 * 1024          # requests are small; replies can be big transactions
MAX_CALLS = 200

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX = re.compile(r"^[0-9a-f]{2,400000}$")
_HOST = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
_PROXY = re.compile(r"^127\.0\.0\.1:\d{1,5}$|^localhost:\d{1,5}$")


def _int(v, lo, hi):
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


ALLOWED = {
    # method: parameter check
    "server.version": lambda p: len(p) == 2 and all(isinstance(x, str) and len(x) < 40 for x in p),
    "blockchain.headers.subscribe": lambda p: p == [],
    "blockchain.scripthash.get_history": lambda p: len(p) == 1 and isinstance(p[0], str)
    and bool(_HEX64.match(p[0])),
    "blockchain.transaction.get": lambda p: len(p) == 1 and isinstance(p[0], str)
    and bool(_HEX64.match(p[0])),
    "blockchain.block.header": lambda p: len(p) == 1 and _int(p[0], 0, 10_000_000),
    "blockchain.estimatefee": lambda p: len(p) == 1 and _int(p[0], 1, 1008),
    "blockchain.transaction.broadcast": lambda p: len(p) == 1 and isinstance(p[0], str)
    and bool(_HEX.match(p[0])),
}


class RelayError(Exception):
    pass


def check_server(url: str) -> tuple[str, str, int]:
    scheme, _, rest = url.partition("://")
    host, _, port = rest.rpartition(":")
    if scheme not in ("ssl", "tcp") or not _HOST.match(host) or not port.isdigit() \
            or not 1 <= int(port) <= 65535:
        raise RelayError("bad server address")
    return scheme, host, int(port)


def check_calls(calls) -> list[tuple[str, list]]:
    if not isinstance(calls, list) or not 1 <= len(calls) <= MAX_CALLS:
        raise RelayError("bad request")
    out = []
    for c in calls:
        if not (isinstance(c, list) and len(c) == 2 and isinstance(c[1], list)):
            raise RelayError("bad request")
        method, params = c
        check = ALLOWED.get(method)
        if check is None:
            raise RelayError(f"method not allowed: {str(method)[:40]}")
        if not check(params):
            raise RelayError(f"bad parameters for {method}")
        out.append((method, params))
    return out


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        srv = self.server
        if srv.allowed_uid is not None:
            creds = self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                            struct.calcsize("3i"))
            _, uid, _ = struct.unpack("3i", creds)
            if uid != srv.allowed_uid:
                return
        upstream: ElectrumClient | None = None
        try:
            while True:
                line = self.rfile.readline(MAX_LINE)
                if not line:
                    break
                try:
                    req = json.loads(line)
                    op = req.get("op")
                    if op == "connect":
                        check_server(req.get("server", ""))
                        tor = req.get("tor")
                        if tor is not None and not _PROXY.match(str(tor)):
                            raise RelayError("Tor proxy must be on this device")
                        if upstream:
                            upstream.close()
                        upstream = srv.client_factory(req["server"], socks5=tor).connect()
                        resp = {"ok": True, "result": None}
                    elif op == "call":
                        calls = check_calls(req.get("calls"))
                        if upstream is None:
                            raise RelayError("not connected")
                        resp = {"ok": True, "result": upstream.call_many(calls)}
                    else:
                        raise RelayError("bad request")
                except (RelayError, ElectrumError, ValueError, OSError, KeyError) as e:
                    if isinstance(e, OSError) and upstream:
                        upstream.close()
                        upstream = None
                    resp = {"ok": False, "error": str(e)[:200]}
                self.wfile.write(json.dumps(resp).encode() + b"\n")
                self.wfile.flush()
        finally:
            if upstream:
                upstream.close()


# Unix sockets exist only on Linux/macOS; the desktop simulator on Windows
# imports this module but never starts the server.
_UnixServer = getattr(socketserver, "UnixStreamServer", socketserver.TCPServer)


class RelayServer(socketserver.ThreadingMixIn, _UnixServer):
    daemon_threads = True

    def __init__(self, path: str, allowed_uid: int | None, client_factory=ElectrumClient):
        if os.path.exists(path):
            os.remove(path)
        self.allowed_uid = allowed_uid
        self.client_factory = client_factory
        super().__init__(path, _Handler)
        os.chmod(path, 0o660)


def serve(path: str = SOCKET, allowed_uid: int | None = None) -> RelayServer:
    srv = RelayServer(path, allowed_uid)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class RelayClient:
    """Drop-in for ElectrumClient, used by the app process."""

    def __init__(self, url: str, socks5: str | None = None, timeout: float = 60,
                 path: str | None = None):
        self.url, self.socks5, self.timeout = url, socks5, timeout
        self.path = path or os.environ.get("CZ_NET_SOCK", SOCKET)
        self._f = None
        self._sock = None

    def _send(self, req: dict):
        try:
            self._f.write(json.dumps(req).encode() + b"\n")
            self._f.flush()
            line = self._f.readline(64 * 1024 * 1024)
            resp = json.loads(line) if line else None
        except (OSError, ValueError) as e:
            self.close()
            raise ElectrumError(f"network service: {e}") from e
        if not isinstance(resp, dict):
            self.close()
            raise ElectrumError("network service closed the connection")
        if not resp.get("ok"):
            raise ElectrumError(str(resp.get("error", "network service error")))
        return resp.get("result")

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        try:
            s.connect(self.path)
        except OSError as e:
            s.close()
            raise ElectrumError(f"network service not running ({e.strerror})") from e
        self._sock = s
        self._f = s.makefile("rwb")
        self._send({"op": "connect", "server": self.url, "tor": self.socks5})
        return self

    def call(self, method: str, *params):
        return self.call_many([(method, list(params))])[0]

    def call_many(self, calls):
        if self._f is None:
            raise ElectrumError("not connected")
        return self._send({"op": "call", "calls": [[m, list(p)] for m, p in calls]})

    def close(self):
        try:
            if self._sock:
                self._sock.close()
        finally:
            self._sock = self._f = None


if __name__ == "__main__":
    uid = os.environ.get("CZ_APP_UID") or (sys.argv[1] if len(sys.argv) > 1 else None)
    srv = RelayServer(SOCKET, int(uid) if uid else None)
    srv.serve_forever()
