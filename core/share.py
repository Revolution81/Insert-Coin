"""
SHARE FILES: a small, temporary HTTPS page on your local network for
downloading history CSVs (and, only if you switch it on, paper wallets).

Built to keep the wallet safe:

- Download only. Nothing can be uploaded; the only change allowed is
  deleting a paper wallet file once you've saved it.
- An outbox, not the wallet: it lists two folders and serves files only by
  the names in those listings (no paths from the browser ever reach the
  filesystem). On the device it runs as its own user (deploy/czshare.service)
  that can read those folders and nothing else.
- Off unless you start it; stops when you leave the screen or after the
  time limit, whichever comes first.
- Local network only: connections from public internet addresses are
  refused, it listens only while the screen is open, and it never opens
  ports on your router.
- Login (admin / bitcoin by default, changeable in SETUP), with a lockout
  after 5 wrong tries that doubles each time.
- HTTPS with the device's own certificate. Browsers warn the first time
  (no public authority signs a gadget's certificate); compare the
  fingerprint the wallet shows.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import html
import ipaddress
import json
import os
import re
import secrets
import socket
import ssl
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core.version import VERSION

PORT = 8443
TIMEOUT_S = 10 * 60
DEFAULT_USER, DEFAULT_PASS = "admin", "bitcoin"
FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.(csv|txt|pdf)$")
MAX_TRIES = 5
TYPES = {"csv": "text/csv; charset=utf-8", "txt": "text/plain; charset=utf-8",
         "pdf": "application/pdf"}


# ------------------------------------------------------------ passwords
def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"pbkdf2${salt.hex()}${h.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, salt, want = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000)
        return hmac.compare_digest(got.hex(), want)
    except ValueError:
        return False


# ------------------------------------------------------------ network
def local_ip() -> str:
    """This device's address on the local network (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))              # TEST-NET: routing lookup only
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def is_local(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local


# ------------------------------------------------------------ certificate
def certificate(directory: str) -> tuple[str, str, str]:
    """(cert path, key path, SHA-256 fingerprint). Made once per device."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    os.makedirs(directory, mode=0o700, exist_ok=True)
    cert_p, key_p = os.path.join(directory, "share.crt"), os.path.join(directory, "share.key")
    if not (os.path.exists(cert_p) and os.path.exists(key_p)):
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Insert Coin")])
        now = dt.datetime.now(dt.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - dt.timedelta(days=1))
                .not_valid_after(now + dt.timedelta(days=3650))
                .sign(key, hashes.SHA256()))
        fd = os.open(key_p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key.private_bytes(serialization.Encoding.PEM,
                                      serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
        with open(cert_p, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
    der = x509.load_pem_x509_certificate(open(cert_p, "rb").read()).public_bytes(
        serialization.Encoding.DER)
    fp = hashlib.sha256(der).hexdigest().upper()
    return cert_p, key_p, fp


def short_fingerprint(fp: str) -> str:
    return " ".join(fp[i:i + 4] for i in range(0, 16, 4))


# ------------------------------------------------------------ the server
class ShareServer:
    """One sharing session. folders: {"history": dir, "paper": dir}; paper is
    left out unless include_paper."""

    def __init__(self, folders: dict, password_hash: str, user: str = DEFAULT_USER,
                 include_paper: bool = False, cert_dir: str = ".", port: int = PORT,
                 timeout_s: int = TIMEOUT_S, host: str = "0.0.0.0"):
        self.folders = {k: v for k, v in folders.items() if k != "paper" or include_paper}
        self.include_paper = include_paper
        self.user, self.password_hash = user, password_hash
        self.port, self.host, self.timeout_s = port, host, timeout_s
        self.cert, self.key, self.fingerprint = certificate(cert_dir)
        self.sessions: dict[str, float] = {}
        self.fails, self.locked_until, self.lockouts = 0, 0.0, 0
        self.started = 0.0
        self.downloads = 0
        self.httpd: ThreadingHTTPServer | None = None
        self._lock = threading.Lock()

    # -- lifecycle
    def start(self):
        handler = _make_handler(self)
        self.httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self.httpd.daemon_threads = True
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(self.cert, self.key)
        self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_address[1]
        self.started = time.monotonic()
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.25},
                         daemon=True).start()

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        self.sessions.clear()

    @property
    def running(self) -> bool:
        return self.httpd is not None

    def seconds_left(self) -> int:
        return max(0, int(self.timeout_s - (time.monotonic() - self.started)))

    def url(self) -> str:
        return f"https://{local_ip()}:{self.port}"

    # -- files
    def listing(self) -> dict[str, list[tuple[str, int, float]]]:
        out = {}
        for key, d in self.folders.items():
            files = []
            try:
                for n in sorted(os.listdir(d), reverse=True):
                    p = os.path.join(d, n)
                    if FILE_RE.match(n) and os.path.isfile(p) and not os.path.islink(p):
                        st = os.stat(p)
                        files.append((n, st.st_size, st.st_mtime))
            except FileNotFoundError:
                pass
            out[key] = files
        return out

    def resolve(self, folder: str, name: str) -> str | None:
        """Only names that are in the folder's own listing."""
        if folder not in self.folders:
            return None
        for n, _, _ in self.listing()[folder]:
            if n == name:
                return os.path.join(self.folders[folder], n)
        return None

    # -- login
    def login(self, user: str, password: str) -> tuple[str | None, str]:
        with self._lock:
            now = time.monotonic()
            if now < self.locked_until:
                return None, f"Too many tries. Wait {int(self.locked_until - now) + 1} s."
            ok = hmac.compare_digest(user.encode(), self.user.encode()) & \
                check_password(password, self.password_hash)
            if not ok:
                self.fails += 1
                if self.fails >= MAX_TRIES:
                    self.lockouts += 1
                    self.locked_until = now + 30 * (2 ** (self.lockouts - 1))
                    self.fails = 0
                return None, "Wrong username or password."
            self.fails = 0
            token = secrets.token_urlsafe(32)
            self.sessions[token] = now
            return token, ""

    def session_ok(self, token: str | None) -> bool:
        return bool(token) and token in self.sessions


def _make_handler(srv: ShareServer):
    class Handler(BaseHTTPRequestHandler):
        server_version = "InsertCoin"
        sys_version = ""

        def log_message(self, *a):          # keep the journal quiet
            pass

        # ---- helpers
        def _cookie(self) -> str | None:
            for part in self.headers.get("Cookie", "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "ic_session":
                    return v
            return None

        def _send(self, code, body: bytes, ctype="text/html; charset=utf-8", extra=()):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "same-origin")   # browsers then send Origin on our own forms
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; style-src 'unsafe-inline'; "
                             "form-action 'self'; frame-ancestors 'none'")
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _guard(self) -> bool:
            if not is_local(self.client_address[0]):
                self._send(403, b"Local network only.", "text/plain")
                return False
            if srv.seconds_left() <= 0:
                self._send(410, b"Sharing has ended.", "text/plain")
                return False
            return True

        def _form(self) -> dict:
            n = min(int(self.headers.get("Content-Length", "0") or 0), 4096)
            return {k: v[0] for k, v in urllib.parse.parse_qs(
                self.rfile.read(n).decode("utf-8", "replace")).items()}

        def _same_origin(self) -> bool:
            origin = self.headers.get("Origin")
            host = self.headers.get("Host", "")
            return origin is None or urllib.parse.urlparse(origin).netloc == host

        # ---- routes
        def do_GET(self):
            if not self._guard():
                return
            path = urllib.parse.urlparse(self.path).path
            logged_in = srv.session_ok(self._cookie())
            if path == "/" and not logged_in:
                return self._send(200, _page_login(""))
            if not logged_in:
                return self._send(303, b"", extra=[("Location", "/")])
            if path == "/":
                return self._send(200, _page_files(srv))
            m = re.match(r"^/f/(history|paper)/([^/]+)$", path)
            if m:
                name = urllib.parse.unquote(m.group(2))
                p = srv.resolve(m.group(1), name)
                if not p:
                    return self._send(404, b"Not found.", "text/plain")
                with open(p, "rb") as f:
                    data = f.read()
                srv.downloads += 1
                return self._send(200, data, TYPES[name.rsplit(".", 1)[1]], extra=[
                    ("Content-Disposition", f'attachment; filename="{name}"')])
            self._send(404, b"Not found.", "text/plain")

        def do_POST(self):
            if not self._guard():
                return
            if not self._same_origin():
                return self._send(403, b"Bad origin.", "text/plain")
            path = urllib.parse.urlparse(self.path).path
            form = self._form()
            if path == "/login":
                token, err = srv.login(form.get("user", ""), form.get("password", ""))
                if not token:
                    return self._send(401, _page_login(err))
                return self._send(303, b"", extra=[
                    ("Location", "/"),
                    ("Set-Cookie", f"ic_session={token}; Secure; HttpOnly; "
                                   "SameSite=Strict; Path=/")])
            if not srv.session_ok(self._cookie()):
                return self._send(303, b"", extra=[("Location", "/")])
            if path == "/logout":
                srv.sessions.pop(self._cookie() or "", None)
                return self._send(303, b"", extra=[("Location", "/"),
                                                   ("Set-Cookie", "ic_session=; Max-Age=0; Path=/")])
            if path == "/delete":
                p = srv.resolve("paper", form.get("name", ""))   # paper wallets only
                if p:
                    os.remove(p)
                return self._send(303, b"", extra=[("Location", "/")])
            self._send(404, b"Not found.", "text/plain")

        do_HEAD = do_GET

    return Handler


# ------------------------------------------------------------ pages
STYLE = """
body{margin:0;background:#0e1024;color:#e4e6f7;font:15px/1.5 ui-monospace,Menlo,Consolas,monospace}
main{max-width:720px;margin:0 auto;padding:24px 16px}
h1{font-size:26px;letter-spacing:2px;color:#f8c830;text-shadow:3px 3px 0 #781020;margin:8px 0 4px}
h2{font-size:15px;color:#58c8f8;margin:28px 0 8px;letter-spacing:1px}
.sub{color:#9a9ec4;margin:0 0 18px}
.box{background:#1c2c8c;border:3px solid #e8e8f8;box-shadow:0 0 0 3px #08081a;padding:14px 16px;margin:10px 0}
.warn{border-color:#e84040}
label{display:block;margin:10px 0 4px}
input{width:100%;box-sizing:border-box;font:inherit;padding:10px;background:#08081a;color:#fff;border:2px solid #9898b8}
button,.btn{font:inherit;padding:8px 14px;background:#f8c830;color:#08081a;border:0;cursor:pointer;text-decoration:none;display:inline-block}
.del{background:#e84040;color:#fff}
.err{color:#ff8a8a}
table{width:100%;border-collapse:collapse}
td{padding:8px 4px;border-top:1px solid #3a4890;vertical-align:middle;word-break:break-all}
td.r{text-align:right;white-space:nowrap}
.foot{color:#9a9ec4;font-size:13px;margin-top:28px}
"""


def _page(title: str, body: str) -> bytes:
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{title}</title><style>{STYLE}</style></head><body><main>"
            f"<h1>INSERT COIN</h1>{body}"
            f"<p class='foot'>Insert Coin {VERSION} · download only · stops when you leave "
            f"the SHARE FILES screen</p></main></body></html>").encode()


def _page_login(err: str) -> bytes:
    e = f"<p class='err'>{html.escape(err)}</p>" if err else ""
    return _page("Insert Coin: log in", f"""
<p class='sub'>File sharing</p>
<form class='box' method='post' action='/login'>{e}
<label for='u'>Username</label><input id='u' name='user' autocomplete='username' autofocus>
<label for='p'>Password</label><input id='p' name='password' type='password' autocomplete='current-password'>
<p><button type='submit'>LOG IN</button></p></form>""")


def _size(n: int) -> str:
    return f"{n / 1024:.1f} KB" if n >= 1024 else f"{n} B"


def _page_files(srv: ShareServer) -> bytes:
    lst = srv.listing()

    def table(folder, files, deletable=False):
        if not files:
            return "<p class='sub'>Nothing here yet.</p>"
        rows = []
        for n, size, mtime in files:
            q = urllib.parse.quote(n)
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
            d = (f"<form method='post' action='/delete' style='display:inline'>"
                 f"<input type='hidden' name='name' value='{html.escape(n)}'>"
                 f"<button class='del' type='submit'>DELETE</button></form>") if deletable else ""
            rows.append(f"<tr><td><a href='/f/{folder}/{q}' style='color:#f8c830'>"
                        f"{html.escape(n)}</a><br><small>{when} · {_size(size)}</small></td>"
                        f"<td class='r'><a class='btn' href='/f/{folder}/{q}'>SAVE</a> {d}</td></tr>")
        return "<table>" + "".join(rows) + "</table>"

    paper = ""
    if srv.include_paper:
        paper = (f"<h2>PAPER WALLETS</h2><div class='box warn'><p class='err'>These files hold "
                 f"private keys: anyone with them can take the coins. Save them, then DELETE "
                 f"them here.</p>{table('paper', lst.get('paper', []), True)}</div>")
    mins = srv.seconds_left() // 60
    return _page("Insert Coin: files", f"""
<p class='sub'>Sharing ends in about {mins} min.</p>
<h2>TRANSACTION HISTORY</h2><div class='box'>{table('history', lst.get('history', []))}
<p class='sub'>Spreadsheet files (CSV): each row has the TXID and a link to track it.</p></div>
{paper}
<form method='post' action='/logout'><p><button type='submit'>LOG OUT</button></p></form>""")


# ------------------------------------------------------------ control
RUN_DIR = "/run/czshare"
HELPER = "/usr/local/sbin/cz-share"


def folders() -> dict:
    """Where the files are: on the device, history on the card's PC side and
    paper wallets in a folder only root and this service can read; on a PC,
    the simulator's data folder."""
    from core import config
    if os.environ.get("CZ_SIM") or not os.path.isdir("/boot/firmware"):
        root = os.path.dirname(config.APP_DIR)
        return {"history": os.path.join(config.APP_DIR, "exports"),
                "paper": os.path.join(root, "signer", "paper")}
    return {"history": "/boot/firmware/InsertCoin-exports",
            "paper": "/var/lib/czwallet/paper"}       # never the card: see tools/copy_backup.py


class InProcess:
    """Simulator and tests: the server runs inside this process."""

    def __init__(self, cert_dir: str, port: int = PORT, host: str = "0.0.0.0"):
        self.cert_dir, self.port, self.host = cert_dir, port, host
        self.srv: ShareServer | None = None

    def start(self, user, password_hash, include_paper, timeout_s):
        self.stop()
        self.srv = ShareServer(folders(), password_hash, user, include_paper, self.cert_dir,
                               self.port, timeout_s, self.host)
        self.srv.start()

    def stop(self):
        if self.srv:
            self.srv.stop()
            self.srv = None

    def status(self) -> dict | None:
        s = self.srv
        if not s or not s.running:
            return None
        if s.seconds_left() <= 0:
            self.stop()
            return None
        return {"url": s.url(), "fingerprint": s.fingerprint, "left": s.seconds_left(),
                "downloads": s.downloads}


class Service:
    """The device: deploy/czshare.service, started and stopped through the
    root helper cz-share; it reports through /run/czshare/status.json."""

    def start(self, user, password_hash, include_paper, timeout_s):
        import subprocess
        cfg = json.dumps({"user": user, "password_hash": password_hash,
                          "include_paper": bool(include_paper), "timeout_s": int(timeout_s)})
        subprocess.run(["sudo", "-n", HELPER, "start"], input=cfg.encode(),
                       capture_output=True, timeout=30, check=True)

    def stop(self):
        import subprocess
        subprocess.run(["sudo", "-n", HELPER, "stop"], capture_output=True, timeout=30)

    def status(self) -> dict | None:
        try:
            with open(os.path.join(RUN_DIR, "status.json")) as f:
                st = json.load(f)
        except (OSError, ValueError):
            return None
        left = int(st.get("ends", 0) - time.time())
        return None if left <= 0 else {**st, "left": left}


def controller(cert_dir: str):
    if os.environ.get("CZ_SIM") or not os.path.exists(HELPER):
        return InProcess(cert_dir)
    return Service()
