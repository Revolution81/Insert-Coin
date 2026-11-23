"""
A fake IPP Everywhere printer for tests and the simulator: TLS (self-signed)
on a local port, answers Get-Printer-Attributes and Print-Job, and keeps
every page it's sent (in memory, and as files if out_dir is given).

    python3 tools/fake_printer.py [port] [out_dir]

    then in the simulator: PAPER WALLETS > PRINT > TYPE ADDRESS > 127.0.0.1
"""
from __future__ import annotations

import datetime
import os
import socket
import ssl
import struct
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import ipp  # noqa: E402


def _cert(dirpath: str) -> tuple[str, str]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Fake Printer")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=365)).sign(key, hashes.SHA256()))
    c, k = os.path.join(dirpath, "cert.pem"), os.path.join(dirpath, "key.pem")
    with open(c, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(k, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
    return c, k


def requested(body: bytes) -> list[str]:
    """requested-attributes as a strict printer reads it: the first attribute
    of that name and the empty-named values right after it; a second
    attribute with the same name is ignored."""
    i, out, cur, seen = 9, [], None, False
    while i < len(body):
        tag = body[i]
        i += 1
        if tag == ipp.END:
            break
        if tag <= 0x0F:
            continue
        nlen = struct.unpack(">H", body[i:i + 2])[0]
        name = body[i + 2:i + 2 + nlen].decode()
        i += 2 + nlen
        vlen = struct.unpack(">H", body[i:i + 2])[0]
        val = body[i + 2:i + 2 + vlen]
        i += 2 + vlen
        if nlen:
            cur = name
            if name == "requested-attributes":
                if seen:
                    cur = None
                    continue
                seen = True
        if cur == "requested-attributes":
            out.append(val.decode())
    return out


class FakePrinter:
    def __init__(self, port: int = 0, formats=("image/jpeg", "application/pdf"),
                 tls: bool = True, out_dir: str | None = None, chunked: bool = False,
                 pwg_res=(300, 600), pwg_types=("black_1", "sgray_8", "srgb_8"),
                 urf=("V1.4", "W8", "SRGB24", "RS300-600", "CP1", "DM1"),
                 model: str = "Fake LaserJet 1000", strict: bool = True):
        self.formats, self.tls, self.out_dir, self.chunked = list(formats), tls, out_dir, chunked
        self.pwg_res, self.pwg_types, self.urf = list(pwg_res), list(pwg_types), list(urf)
        self.model, self.strict = model, strict
        self.jobs: list[dict] = []
        self.requests = 0
        self._dir = tempfile.mkdtemp()
        self._ctx = None
        if tls:
            self._ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            self._ctx.load_cert_chain(*_cert(self._dir))
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def new_certificate(self):
        """Pretend the printer (or someone in between) has a new certificate."""
        self._ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._ctx.load_cert_chain(*_cert(tempfile.mkdtemp()))

    def close(self):
        self.running = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)    # wakes the accept() thread, frees the port
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def _loop(self):
        while self.running:
            try:
                raw, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(raw,), daemon=True).start()

    def _serve(self, raw):
        try:
            conn = self._ctx.wrap_socket(raw, server_side=True) if self.tls else raw
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                data += chunk
            head, body = data.split(b"\r\n\r\n", 1)
            length = 0
            for line in head.decode().split("\r\n")[1:]:
                k, _, v = line.partition(":")
                if k.strip().lower() == "content-length":
                    length = int(v)
            while len(body) < length:
                body += conn.recv(65536)
            self.requests += 1
            reply = self._ipp(body)
            if self.chunked:
                mid = len(reply) // 2
                payload = b"".join(b"%x\r\n" % len(p) + p + b"\r\n"
                                   for p in (reply[:mid], reply[mid:]) if p) + b"0\r\n\r\n"
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/ipp\r\n"
                             b"Transfer-Encoding: chunked\r\n\r\n" + payload)
            else:
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/ipp\r\n"
                             + f"Content-Length: {len(reply)}\r\n\r\n".encode() + reply)
            conn.close()
        except (OSError, ssl.SSLError, ValueError):
            pass

    def _ipp(self, body: bytes) -> bytes:
        op, rid = struct.unpack(">H", body[2:4])[0], struct.unpack(">I", body[4:8])[0]
        _, attrs, end = ipp.decode(body)
        head = struct.pack(">BBHI", 2, 0, 0, rid) + bytes([ipp.OPERATION]) + \
            ipp._attr(ipp.CHARSET, "attributes-charset", "utf-8") + \
            ipp._attr(ipp.LANGUAGE, "attributes-natural-language", "en")
        if op == ipp.GET_PRINTER_ATTRIBUTES:
            # like a real printer: answer only what was asked, and read a
            # repeated attribute name the strict way (first one only)
            wanted = set(requested(body)) if self.strict else {"all"}
            every = bool(wanted & {"all", "printer-description"})

            def give(tag, name, values):
                if not (every or name in wanted):
                    return b""
                return b"".join(ipp._attr(tag, name if i == 0 else "", v)
                                for i, v in enumerate(values))
            out = head + bytes([ipp.PRINTER])
            out += give(ipp.TEXT, "printer-make-and-model", [self.model])
            out += give(ipp.ENUM, "printer-state", [3])
            out += give(ipp.MIME, "document-format-supported", self.formats)
            if "image/pwg-raster" in self.formats:
                out += give(0x32, "pwg-raster-document-resolution-supported",
                            [struct.pack(">iib", r, r, 3) for r in self.pwg_res])
                out += give(ipp.KEYWORD, "pwg-raster-document-type-supported", self.pwg_types)
            if "image/urf" in self.formats:
                out += give(ipp.KEYWORD, "urf-supported", self.urf)
            return out + bytes([ipp.END])
        if op == ipp.PRINT_JOB:
            fmt = (attrs.get("document-format") or ["?"])[0]
            if fmt not in self.formats:
                return struct.pack(">BBHI", 2, 0, 0x040A, rid) + bytes([ipp.END])
            doc = body[end:]
            job = {"format": fmt, "data": doc, "name": (attrs.get("job-name") or [""])[0]}
            self.jobs.append(job)
            if self.out_dir:
                os.makedirs(self.out_dir, exist_ok=True)
                ext = {"image/jpeg": ".jpg", "application/pdf": ".pdf",
                       "image/pwg-raster": ".pwg", "image/urf": ".urf"}.get(fmt, ".bin")
                with open(os.path.join(self.out_dir, f"job{len(self.jobs)}{ext}"), "wb") as f:
                    f.write(doc)
            return head + bytes([ipp.JOB]) + ipp._attr(ipp.INTEGER, "job-id", len(self.jobs)) + \
                bytes([ipp.END])
        return struct.pack(">BBHI", 2, 0, 0x0501, rid) + bytes([ipp.END])


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 6310
    out = sys.argv[2] if len(sys.argv) > 2 else "fake-printer-pages"
    p = FakePrinter(port=port, out_dir=out)
    print(f"Fake printer on 127.0.0.1:{p.port}, pages saved to {out}/. Ctrl+C to stop.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        p.close()
