"""SHARE FILES: the local-network download page, and attempts to misuse it."""
import http.cookiejar
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request

import pytest

from core import share

from test_app import app, create_wallet, enter, press, settle  # noqa: F401

CSV = "InsertCoin-history-73c5da0a-main-20261114-101500.csv"
PAPER = "tb1qljz0ue84246hfywa7cwk72gdg80awv43cmcjja.txt"


@pytest.fixture
def server(tmp_path):
    h, p = tmp_path / "h", tmp_path / "p"
    h.mkdir()
    p.mkdir()
    (h / CSV).write_text("date,status,amount_sats\n")
    (p / PAPER).write_text("SECRET KEY")
    (h / "notes.sh").write_text("not shared")
    made = []

    def make(include_paper=False, timeout_s=600):
        srv = share.ShareServer({"history": str(h), "paper": str(p)},
                                share.hash_password("bitcoin"), "admin", include_paper,
                                str(tmp_path / "cert"), port=0, timeout_s=timeout_s,
                                host="127.0.0.1")
        srv.start()
        made.append(srv)
        return srv, f"https://127.0.0.1:{srv.port}", p
    yield make
    for s in made:
        s.stop()


def browser():
    ctx = ssl._create_unverified_context()
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx),
                                       urllib.request.HTTPCookieProcessor(jar)), jar


def code(op, url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        r = op.open(req)
        return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def login(op, base, user="admin", pw="bitcoin"):
    return code(op, base + "/login", urllib.parse.urlencode({"user": user, "password": pw}).encode())


def test_default_login_and_download(server):
    srv, base, _ = server()
    op, jar = browser()
    st, body = code(op, base + "/")
    assert st == 200 and b"Username" in body                 # login page first
    body = code(op, base + f"/f/history/{CSV}")[1]            # not logged in: back to login
    assert b"date,status" not in body and b"Username" in body
    st, body = login(op, base)
    assert st == 200 and CSV.encode() in body and b"notes.sh" not in body
    cookie = next(iter(jar))
    assert cookie.secure and cookie.has_nonstandard_attr("HttpOnly")
    st, data = code(op, base + f"/f/history/{CSV}")
    assert st == 200 and data == b"date,status,amount_sats\n"
    assert srv.downloads == 1
    assert srv.fingerprint and len(share.short_fingerprint(srv.fingerprint)) == 19


def test_wrong_passwords_lock_out(server):
    srv, base, _ = server()
    op, _ = browser()
    for _ in range(share.MAX_TRIES):
        assert login(op, base, pw="hunter2")[0] == 401
    st, body = login(op, base)                                # right password, but locked
    assert st == 401 and b"Too many tries" in body
    srv.locked_until = 0
    assert login(op, base)[0] == 200


def test_paper_wallets_only_when_included(server):
    _, base, _ = server(include_paper=False)
    op, _ = browser()
    login(op, base)
    assert code(op, base + f"/f/paper/{PAPER}")[0] == 404
    _, base2, pdir = server(include_paper=True)
    op2, _ = browser()
    st, body = login(op2, base2)
    assert PAPER.encode() in body and b"private keys" in body
    assert code(op2, base2 + f"/f/paper/{PAPER}")[1] == b"SECRET KEY"
    assert code(op2, base2 + "/delete", urllib.parse.urlencode({"name": PAPER}).encode())[0] == 200
    assert not (pdir / PAPER).exists()


def test_attacks_are_refused(server, monkeypatch):
    srv, base, _ = server(include_paper=True)
    op, _ = browser()
    login(op, base)
    for path in ("/f/history/..%2f..%2fetc%2fpasswd", "/f/history/%2e%2e", "/f/history/notes.sh",
                 "/f/other/x.csv", f"/f/history/{CSV}%00.pdf", "/f/history/" + "a" * 300 + ".csv"):
        assert code(op, base + path)[0] == 404, path
    # history files can't be deleted, only paper wallets
    code(op, base + "/delete", urllib.parse.urlencode({"name": CSV}).encode())
    assert srv.resolve("history", CSV)
    # another website can't post on your behalf
    st, _ = code(op, base + "/delete", urllib.parse.urlencode({"name": PAPER}).encode(),
                 {"Origin": "https://evil.example"})
    assert st == 403 and srv.resolve("paper", PAPER)
    # no uploads: there is no such route
    assert code(op, base + "/upload", b"x")[0] == 404
    # public internet addresses are turned away
    monkeypatch.setattr(share, "is_local", lambda addr: False)
    assert code(browser()[0], base + "/")[0] == 403


def test_local_addresses():
    for ok in ("192.168.1.5", "10.0.0.2", "172.20.1.1", "127.0.0.1", "::1", "fe80::1",
               "::ffff:192.168.1.9"):
        assert share.is_local(ok), ok
    for bad in ("8.8.8.8", "100.64.0.1", "2001:4860::8888", "garbage"):
        assert not share.is_local(bad), bad


def test_time_limit(server):
    srv, base, _ = server(timeout_s=0)
    assert code(browser()[0], base + "/")[0] == 410


def test_passwords_are_hashed():
    h = share.hash_password("bitcoin")
    assert "bitcoin" not in h and share.check_password("bitcoin", h)
    assert not share.check_password("Bitcoin", h)


def test_share_screens(app, monkeypatch):  # noqa: F811
    from ui.flow import settings_screen
    from ui.share_flow import SharingScreen
    create_wallet(app)
    made = []

    def ctl(cert_dir):
        c = share.InProcess(cert_dir, port=0, host="127.0.0.1")
        made.append(c)
        return c
    monkeypatch.setattr(share, "controller", ctl)
    settings_screen(app, at="SHARE FILES")
    settle(app)
    m = app.visible
    m.sel = [i[0] for i in m.items].index("SHARE FILES")
    enter(app)
    menu = app.visible
    vals = {i[0]: i[2] for i in menu.items if len(i) > 2}
    assert vals["LOGIN"] == "admin" and vals["PASSWORD"] == "bitcoin"
    assert vals["PAPER WALLETS"] == "LEAVE OUT" and vals["TIME LIMIT"] == "10 MIN"
    menu.sel = 0
    enter(app)                                                 # vault mode: refused
    assert app.visible.title == "GO ONLINE FIRST"
    press(app, "KEY_ENTER")
    monkeypatch.setenv("CZ_SIM_SHARE_ANY", "1")
    enter(app)
    scr = app.visible
    assert isinstance(scr, SharingScreen) and made and made[0].srv.running
    app.render(0)
    assert scr.frames[0].startswith("https://") and "PASS bitcoin" in scr.lines
    port = made[0].srv.port
    press(app, "KEY_ENTER")                                    # stop
    assert app.visible.title == "SHARING STOPPED" and made[0].srv is None
    import socket
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1).close()


def test_a_real_browser_can_log_in(server, tmp_path):
    """Chromium, as people will use it: the login form must work (an Origin
    of "null" from a too-strict referrer policy once broke this)."""
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("no browser here")
    srv, base, _ = server()
    js = tmp_path / "b.js"
    js.write_text("""
const {chromium}=require('playwright');(async()=>{const b=await chromium.launch();
const c=await b.newContext({ignoreHTTPSErrors:true});const p=await c.newPage();
await p.goto('%s/');await p.fill('#u','admin');await p.fill('#p','bitcoin');
await p.click('button');await p.waitForLoadState();console.log(await p.content());
await b.close()})()""" % base)
    try:
        out = subprocess.run(["node", str(js)], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("browser didn't run")
    if out.returncode != 0:
        pytest.skip("playwright not available")
    assert "TRANSACTION HISTORY" in out.stdout and CSV in out.stdout
