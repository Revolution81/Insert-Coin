"""
Finding printers on the local network with multicast DNS (Bonjour /
AirPrint discovery): one question to 224.0.0.251, answers collected for a
couple of seconds. Read-only, nothing about the wallet is sent.

Asked from an ordinary port, so printers answer us directly ("legacy
unicast", RFC 6762 6.7) instead of to the whole network.
"""
from __future__ import annotations

import socket
import struct
import time

GROUP, PORT = "224.0.0.251", 5353
A, PTR, TXT, AAAA, SRV = 1, 12, 16, 28, 33
SERVICES = ("_ipps._tcp.local", "_ipp._tcp.local")


def _name(n: str) -> bytes:
    out = b""
    for label in n.strip(".").split("."):
        b = label.encode()
        out += bytes([len(b)]) + b
    return out + b"\x00"


def query(names=SERVICES) -> bytes:
    head = struct.pack(">HHHHHH", 0, 0, len(names), 0, 0, 0)
    return head + b"".join(_name(n) + struct.pack(">HH", PTR, 1) for n in names)


def _read_name(buf: bytes, i: int) -> tuple[str, int]:
    labels, jumped, end, hops = [], False, i, 0
    while True:
        if i >= len(buf) or hops > 40:
            raise ValueError("bad name")
        n = buf[i]
        if n == 0:
            i += 1
            break
        if n & 0xC0 == 0xC0:                     # compression pointer
            if i + 1 >= len(buf):
                raise ValueError("bad pointer")
            ptr = ((n & 0x3F) << 8) | buf[i + 1]
            if not jumped:
                end = i + 2
            jumped, i, hops = True, ptr, hops + 1
            continue
        labels.append(buf[i + 1:i + 1 + n].decode("utf-8", "replace"))
        i += 1 + n
    return ".".join(labels), (end if jumped else i)


def parse(buf: bytes) -> list[tuple]:
    """Every resource record in an answer: (name, type, data)."""
    if len(buf) < 12:
        return []
    _, flags, qd, an, ns, ar = struct.unpack(">HHHHHH", buf[:12])
    i, out = 12, []
    for _ in range(qd):
        _, i = _read_name(buf, i)
        i += 4
    for _ in range(an + ns + ar):
        name, i = _read_name(buf, i)
        if i + 10 > len(buf):
            break
        rtype, _, _, rlen = struct.unpack(">HHIH", buf[i:i + 10])
        i += 10
        rdata, start = buf[i:i + rlen], i
        i += rlen
        if rtype == PTR:
            out.append((name, PTR, _read_name(buf, start)[0]))
        elif rtype == SRV and rlen >= 7:
            port = struct.unpack(">H", rdata[4:6])[0]
            out.append((name, SRV, (_read_name(buf, start + 6)[0], port)))
        elif rtype == A and rlen == 4:
            out.append((name, A, socket.inet_ntoa(rdata)))
        elif rtype == TXT:
            kv, j = {}, 0
            while j < len(rdata):
                n = rdata[j]
                item = rdata[j + 1:j + 1 + n].decode("utf-8", "replace")
                k, _, v = item.partition("=")
                kv[k.lower()] = v
                j += 1 + n
            out.append((name, TXT, kv))
    return out


def printers_from(records: list[tuple]) -> list[dict]:
    """Turn records into printers: name, IPv4 address, port, path, and
    whether it offers encrypted printing."""
    ptr = [(svc, inst) for svc, t, inst in records if t == PTR]
    srv = {n: d for n, t, d in records if t == SRV}
    txt = {n: d for n, t, d in records if t == TXT}
    addr = {n: d for n, t, d in records if t == A}
    found: dict[str, dict] = {}
    for svc, inst in ptr:
        if svc not in SERVICES or inst not in srv:
            continue
        host, port = srv[inst]
        ip = addr.get(host)
        if not ip:
            continue
        t = txt.get(inst, {})
        label = inst[:-len(svc) - 1] if inst.endswith(svc) else inst
        enc = svc.startswith("_ipps")
        cur = found.get(ip)
        if cur and cur["encrypted"] and not enc:
            continue                             # already have its encrypted service
        found[ip] = {"name": (t.get("ty") or label)[:40], "host": ip, "port": port,
                     "path": t.get("rp") or "ipp/print", "encrypted": enc}
    return sorted(found.values(), key=lambda p: (not p["encrypted"], p["name"]))


def browse(timeout: float = 2.5) -> list[dict]:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        s.settimeout(0.3)
        s.sendto(query(), (GROUP, PORT))
        records, end = [], time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                buf, _ = s.recvfrom(9000)
            except socket.timeout:
                continue
            try:
                records += parse(buf)
            except (ValueError, struct.error, IndexError):
                continue                         # one bad packet doesn't stop the scan
        return printers_from(records)
    except OSError:
        return []
    finally:
        s.close()
