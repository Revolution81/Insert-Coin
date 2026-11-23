"""
IPP, the Internet Printing Protocol, over TLS only (ipps://).

Just enough to ask a printer what it is and to send it one page: the two
operations Get-Printer-Attributes and Print-Job, as IPP Everywhere / AirPrint
printers speak them. No drivers, no CUPS.

Safety rules, enforced here:
- Printers are reached by **local IP address only** (10/8, 172.16/12,
  192.168/16, fc00::/7, link-local). No hostnames, so no DNS lookup can
  steer a print job to the internet or a cloud print service.
- **TLS only.** A printer that can't do encrypted IPP is refused.
- Printers sign their own certificates, so there's no authority to check
  them against. Instead the certificate is **pinned on first use**: you
  approve its fingerprint once, and every later print checks it again
  **before a single byte of the page is sent**.
"""
from __future__ import annotations

import hashlib
import ipaddress
import os
import socket
import ssl
import struct

PRINT_JOB = 0x0002
GET_PRINTER_ATTRIBUTES = 0x000B

# delimiter tags
OPERATION, JOB, END, PRINTER = 0x01, 0x02, 0x03, 0x04
# value tags
INTEGER, BOOLEAN, ENUM = 0x21, 0x22, 0x23
TEXT, NAME, KEYWORD, URI, CHARSET, LANGUAGE, MIME = 0x41, 0x42, 0x44, 0x45, 0x47, 0x48, 0x49

MAX_REPLY = 2 * 1024 * 1024
# what we can send, best first: JPEG and PDF if the printer takes them, else
# the raster formats every driverless printer must take (core/raster.py)
FORMATS = ("image/jpeg", "application/pdf", "image/pwg-raster", "image/urf")
STATES = {3: "IDLE", 4: "PRINTING", 5: "STOPPED"}


class PrinterError(Exception):
    pass


# ---------------------------------------------------------------- addresses
def local_ip(host: str, allow_loopback: bool = False) -> str:
    """The printer's address, checked: a literal IP on the local network."""
    try:
        ip = ipaddress.ip_address(host.strip().strip("[]"))
    except ValueError:
        raise PrinterError("type the printer's IP address, e.g. 192.168.1.40")
    if ip.is_loopback and allow_loopback:
        return str(ip)
    if ip.is_loopback or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        raise PrinterError("that isn't a printer's address")
    if not (ip.is_private or ip.is_link_local):
        raise PrinterError("printers on the local network only")
    return str(ip)


def check_path(path: str) -> str:
    path = (path or "ipp/print").strip().lstrip("/")
    if len(path) > 64 or not all(c.isalnum() or c in "/-_." for c in path) or ".." in path:
        raise PrinterError("bad printer path")
    return path


# ---------------------------------------------------------------- encoding
def _attr(tag: int, name: str, value) -> bytes:
    n = name.encode()
    if tag in (INTEGER, ENUM):
        v = struct.pack(">i", value)
    elif tag == BOOLEAN:
        v = b"\x01" if value else b"\x00"
    else:
        v = value.encode() if isinstance(value, str) else bytes(value)
    return bytes([tag]) + struct.pack(">H", len(n)) + n + struct.pack(">H", len(v)) + v


def encode_request(op: int, request_id: int, uri: str, extra: list | None = None) -> bytes:
    out = struct.pack(">BBHI", 2, 0, op, request_id) + bytes([OPERATION])
    out += _attr(CHARSET, "attributes-charset", "utf-8")
    out += _attr(LANGUAGE, "attributes-natural-language", "en")
    out += _attr(URI, "printer-uri", uri)
    for tag, name, value in extra or []:
        out += _attr(tag, name, value)
    return out + bytes([END])


def decode_response(buf: bytes) -> tuple[int, dict]:
    """(status code, {attribute name: [values]}) across all groups."""
    status, attrs, _ = decode(buf)
    return status, attrs


def decode(buf: bytes) -> tuple[int, dict, int]:
    """Like decode_response, plus where the attributes end (any document
    data follows there)."""
    if len(buf) < 9:
        raise PrinterError("the printer sent a broken reply")
    status = struct.unpack(">H", buf[2:4])[0]
    attrs: dict[str, list] = {}
    i, name = 8, None
    while i < len(buf):
        tag = buf[i]
        i += 1
        if tag == END:
            return status, attrs, i
        if tag <= 0x0F:                       # a new group
            continue
        if i + 2 > len(buf):
            raise PrinterError("the printer sent a broken reply")
        nlen = struct.unpack(">H", buf[i:i + 2])[0]
        i += 2
        if nlen:
            name = buf[i:i + nlen].decode("utf-8", "replace")
        i += nlen
        if i + 2 > len(buf):
            raise PrinterError("the printer sent a broken reply")
        vlen = struct.unpack(">H", buf[i:i + 2])[0]
        i += 2
        raw = buf[i:i + vlen]
        if len(raw) != vlen:
            raise PrinterError("the printer sent a broken reply")
        i += vlen
        if name is None:
            continue
        if tag in (INTEGER, ENUM) and vlen == 4:
            value = struct.unpack(">i", raw)[0]
        elif tag == BOOLEAN and vlen == 1:
            value = bool(raw[0])
        elif 0x40 <= tag <= 0x4F:
            value = raw.decode("utf-8", "replace")
        else:
            value = raw
        attrs.setdefault(name, []).append(value)
    raise PrinterError("the printer sent a broken reply")


# ---------------------------------------------------------------- transport
def _tls_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE           # self-signed by design: pinned instead
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def _read_http(tls) -> bytes:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = tls.recv(65536)
        if not chunk:
            raise PrinterError("the printer closed the connection")
        data += chunk
        if len(data) > MAX_REPLY:
            raise PrinterError("the printer sent too much")
    head, body = data.split(b"\r\n\r\n", 1)
    lines = head.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ", 2)
    if len(parts) < 2 or not parts[1].isdigit():
        raise PrinterError("the printer didn't answer in IPP")
    code = int(parts[1])
    headers = {k.strip().lower(): v.strip() for k, _, v in
               (ln.partition(":") for ln in lines[1:])}
    if code == 100:                            # "100 Continue": the real reply follows
        rest = body
        while b"\r\n\r\n" not in rest:
            chunk = tls.recv(65536)
            if not chunk:
                raise PrinterError("the printer closed the connection")
            rest += chunk
        return _read_http_from(rest, tls)
    if code != 200:
        raise PrinterError(f"the printer answered HTTP {code}")
    return _body(headers, body, tls)


def _read_http_from(data: bytes, tls) -> bytes:
    head, body = data.split(b"\r\n\r\n", 1)
    lines = head.decode("latin-1").split("\r\n")
    code = int(lines[0].split(" ", 2)[1])
    if code != 200:
        raise PrinterError(f"the printer answered HTTP {code}")
    headers = {k.strip().lower(): v.strip() for k, _, v in
               (ln.partition(":") for ln in lines[1:])}
    return _body(headers, body, tls)


def _body(headers: dict, body: bytes, tls) -> bytes:
    def more():
        chunk = tls.recv(65536)
        if not chunk:
            raise PrinterError("the printer closed the connection")
        return chunk
    if "chunked" in headers.get("transfer-encoding", "").lower():
        out, buf = b"", body
        while True:
            while b"\r\n" not in buf:
                buf += more()
            size_line, buf = buf.split(b"\r\n", 1)
            size = int(size_line.split(b";")[0].strip() or b"0", 16)
            if size == 0:
                return out
            while len(buf) < size + 2:
                buf += more()
            out += buf[:size]
            buf = buf[size + 2:]
            if len(out) > MAX_REPLY:
                raise PrinterError("the printer sent too much")
    if "content-length" in headers:
        n = int(headers["content-length"])
        if n > MAX_REPLY:
            raise PrinterError("the printer sent too much")
        while len(body) < n:
            body += more()
        return body[:n]
    while True:                                # until the printer closes
        try:
            chunk = tls.recv(65536)
        except (ssl.SSLError, OSError):
            break
        if not chunk:
            break
        body += chunk
        if len(body) > MAX_REPLY:
            raise PrinterError("the printer sent too much")
    return body


def fingerprint(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def exchange(host: str, port: int, path: str, payload, pin: str | None = None,
             timeout: float = 30.0) -> tuple[bytes, str]:
    """One IPP request over TLS. payload is the request, or a callable that
    returns it (so a page is only produced after the certificate checks
    out). Returns (IPP reply, certificate fingerprint)."""
    try:
        raw = socket.create_connection((host, port), timeout=timeout)
    except OSError as e:
        raise PrinterError(f"can't reach the printer ({e.strerror or e})")
    try:
        try:
            tls = _tls_context().wrap_socket(raw)
        except (ssl.SSLError, OSError):
            raise PrinterError("this printer doesn't do encrypted printing (IPPS)")
        fp = fingerprint(tls.getpeercert(binary_form=True) or b"")
        if pin is not None and fp != pin:
            raise PrinterError("this printer's certificate changed: not sending")
        body = payload() if callable(payload) else payload
        head = (f"POST /{path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
                f"Content-Type: application/ipp\r\nContent-Length: {len(body)}\r\n"
                f"Connection: close\r\n\r\n").encode()
        tls.sendall(head + body)
        return _read_http(tls), fp
    except (ssl.SSLError, OSError, ValueError) as e:
        if isinstance(e, PrinterError):
            raise
        raise PrinterError(f"printing failed ({e})")
    finally:
        try:
            raw.close()
        except OSError:
            pass


# ---------------------------------------------------------------- operations
def uri(host: str, port: int, path: str) -> str:
    h = f"[{host}]" if ":" in host else host
    return f"ipps://{h}:{port}/{path}"


def get_attributes(host: str, port: int = 631, path: str = "ipp/print",
                   pin: str | None = None) -> dict:
    """What the printer is, and the certificate fingerprint. Sends nothing
    secret, so it's safe before the printer is trusted."""
    req = encode_request(GET_PRINTER_ATTRIBUTES, 1, uri(host, port, path), [
        (KEYWORD, "requested-attributes", "printer-make-and-model"),
        (KEYWORD, "requested-attributes", "printer-name"),
        (KEYWORD, "requested-attributes", "printer-state"),
        (KEYWORD, "requested-attributes", "document-format-supported"),
        (KEYWORD, "requested-attributes", "pwg-raster-document-resolution-supported"),
        (KEYWORD, "requested-attributes", "pwg-raster-document-type-supported"),
        (KEYWORD, "requested-attributes", "urf-supported"),
    ])
    reply, fp = exchange(host, port, path, req, pin=pin, timeout=10)
    status, attrs = decode_response(reply)
    if status >= 0x0400:
        raise PrinterError(f"the printer refused (IPP status 0x{status:04x})")
    formats = [f for f in attrs.get("document-format-supported", []) if isinstance(f, str)]
    model = (attrs.get("printer-make-and-model") or attrs.get("printer-name") or ["Printer"])[0]
    state = (attrs.get("printer-state") or [0])[0]
    fmt, raster = choose_format(formats, attrs)
    return {"model": str(model)[:40], "formats": formats, "format": fmt, "raster": raster,
            "state": STATES.get(state, "?"), "fingerprint": fp}


def choose_format(supported: list[str], attrs: dict | None = None):
    """(the format to send, and for a raster format its dpi and colour)."""
    from . import raster
    attrs = attrs or {}
    for f in FORMATS:
        if f not in supported:
            continue
        if f == raster.PWG:
            pick = raster.pwg_choice(attrs.get("pwg-raster-document-resolution-supported", []),
                                     attrs.get("pwg-raster-document-type-supported", []))
        elif f == raster.URF:
            pick = raster.urf_choice(attrs.get("urf-supported", []))
        else:
            return f, None
        if pick:
            return f, pick
    return None, None


def print_job(host: str, port: int, path: str, pin: str, document, fmt: str,
              job_name: str = "Insert Coin") -> int:
    """Send one document. document is bytes, or a callable making them,
    called only after the pinned certificate matched."""
    if fmt not in FORMATS:
        raise PrinterError("unsupported page format")
    if not pin:
        raise PrinterError("this printer isn't trusted yet")

    def body():
        data = document() if callable(document) else document
        return encode_request(PRINT_JOB, 2, uri(host, port, path), [
            (NAME, "requesting-user-name", "insert-coin"),
            (NAME, "job-name", job_name[:60]),
            (MIME, "document-format", fmt),
        ]) + data
    reply, _ = exchange(host, port, path, body, pin=pin, timeout=60)
    status, attrs = decode_response(reply)
    if status >= 0x0400:
        raise PrinterError(f"the printer refused the page (IPP status 0x{status:04x})")
    return int((attrs.get("job-id") or [0])[0] or 0)


def short_fp(fp: str) -> str:
    """First 16 hex digits in groups of 4, for comparing by eye."""
    return " ".join(fp[i:i + 4].upper() for i in range(0, 16, 4))


ALLOW_LOOPBACK = bool(os.environ.get("CZ_SIM"))     # the simulator's test printers
