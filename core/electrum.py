"""
Minimal Electrum protocol client (JSON-RPC over TCP/TLS, optionally through
Tor's SOCKS5 proxy). Pure Python: no compiled dependencies on the device.

Protocol reference: https://electrum-protocol.readthedocs.io
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import ssl
import struct


class ElectrumError(Exception):
    pass


MAX_REPLY = 16 * 1024 * 1024        # the biggest real reply is a 4 MB transaction in hex


def scripthash(script_pubkey: bytes) -> str:
    """Electrum indexes by sha256(scriptPubKey), byte-reversed, hex."""
    return hashlib.sha256(script_pubkey).digest()[::-1].hex()


def _socks5_connect(proxy: str, host: str, port: int, timeout: float) -> socket.socket:
    phost, pport = proxy.rsplit(":", 1)
    s = socket.create_connection((phost, int(pport)), timeout=timeout)
    s.sendall(b"\x05\x01\x00")                       # SOCKS5, no auth
    if s.recv(2) != b"\x05\x00":
        s.close()
        raise ElectrumError("Tor proxy refused the connection")
    h = host.encode()
    # Hostname (not a resolved IP) goes to the proxy, so DNS and .onion work over Tor.
    s.sendall(b"\x05\x01\x00\x03" + bytes([len(h)]) + h + struct.pack(">H", port))
    resp = s.recv(4)
    if len(resp) < 4 or resp[1] != 0:
        s.close()
        raise ElectrumError(f"Tor couldn't reach {host} (code {resp[1] if len(resp) > 1 else '?'})")
    skip = {1: 4, 3: s.recv(1)[0], 4: 16}[resp[3]] + 2
    s.recv(skip)
    return s


def _private_host(host: str) -> bool:
    if host.endswith((".onion", ".local", ".lan")) or host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


class ElectrumClient:
    def __init__(self, url: str, socks5: str | None = None, timeout: float = 30,
                 verify_tls: bool | None = None):
        scheme, rest = url.split("://", 1) if "://" in url else ("ssl", url)
        host, port = rest.rsplit(":", 1)
        self.scheme, self.host, self.port = scheme, host, int(port)
        self.socks5, self.timeout = socks5, timeout
        # Personal servers (Umbrel, Start9, .onion) usually have self-signed
        # certificates; Tor already authenticates .onion addresses.
        self.verify_tls = (not _private_host(host)) if verify_tls is None else verify_tls
        self._sock = None
        self._buf = b""
        self._id = 0

    def connect(self):
        if self.socks5:
            raw = _socks5_connect(self.socks5, self.host, self.port, self.timeout)
        else:
            raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
        raw.settimeout(self.timeout)
        if self.scheme == "ssl":
            ctx = ssl.create_default_context()
            if not self.verify_tls:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            raw = ctx.wrap_socket(raw, server_hostname=self.host)
        self._sock = raw
        self.call("server.version", "czwallet 0.1", "1.4")
        return self

    def close(self):
        if self._sock:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def _readline(self) -> bytes:
        while b"\n" not in self._buf:
            if len(self._buf) > MAX_REPLY:
                # a server that never ends its line would eat all the memory
                self.close()
                raise ElectrumError("server sent an oversized reply")
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ElectrumError("server closed the connection")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return line

    def call(self, method: str, *params):
        return self.call_many([(method, list(params))])[0]

    def call_many(self, calls: list[tuple[str, list]]) -> list:
        """Pipelined requests: send them all, then read the replies."""
        if self._sock is None:
            raise ElectrumError("not connected")
        ids = []
        out = b""
        for method, params in calls:
            self._id += 1
            ids.append(self._id)
            out += json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method,
                               "params": params}).encode() + b"\n"
        self._sock.sendall(out)
        results = {}
        while len(results) < len(ids):
            try:
                msg = json.loads(self._readline())
            except ValueError:
                raise ElectrumError("server sent something that isn't JSON")
            if not isinstance(msg, dict) or "id" not in msg or msg["id"] not in ids:
                continue                          # subscription notification
            if msg.get("error"):
                err = msg["error"]
                raise ElectrumError(err.get("message", str(err)) if isinstance(err, dict) else str(err))
            results[msg["id"]] = msg.get("result")
        return [results[i] for i in ids]
