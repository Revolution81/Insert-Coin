"""PAPER WALLETS > PRINT: IPP over TLS, discovery, pinning, the service (0.8.19)."""
import os
import random
import socket
import struct
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import pytest  # noqa: E402

from core import ipp, mdns, paper, printing  # noqa: E402
from fake_printer import FakePrinter  # noqa: E402
from test_app import app  # noqa: E402,F401


@pytest.fixture
def wallet_dir(tmp_path):
    d = tmp_path / "paper"
    w = paper.build(paper.random_key(), "signet")
    paper.save(str(d), w)
    return d, w


@pytest.fixture
def printer():
    p = FakePrinter()
    yield p
    p.close()


def svc_for(d, tmp_path):
    return printing.PrintService(str(d), str(tmp_path / "state"), allow_loopback=True)


# ------------------------------------------------------------------ IPP wire format
def test_ipp_round_trip_and_hostile_replies():
    req = ipp.encode_request(ipp.GET_PRINTER_ATTRIBUTES, 7, "ipps://10.0.0.5:631/ipp/print",
                             [(ipp.KEYWORD, "requested-attributes", "printer-state")])
    status, attrs, end = ipp.decode(req)
    assert attrs["printer-uri"] == ["ipps://10.0.0.5:631/ipp/print"] and end == len(req)
    rng = random.Random(5)
    for _ in range(3000):                     # junk from a printer: an error, never a crash
        junk = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 80)))
        try:
            ipp.decode_response(junk)
        except ipp.PrinterError:
            pass


def test_only_local_ip_addresses():
    assert ipp.local_ip("192.168.1.40") == "192.168.1.40"
    assert ipp.local_ip("10.1.2.3") == "10.1.2.3"
    for bad in ("8.8.8.8", "printer.example.com", "hp-printer.local", "127.0.0.1",
                "0.0.0.0", "224.0.0.251", "", "1.1.1.1:631"):
        with pytest.raises(ipp.PrinterError):
            ipp.local_ip(bad)
    assert ipp.local_ip("127.0.0.1", allow_loopback=True)      # the simulator's fake printer
    for bad in ("../../etc", "ipp/print?x=1", "a" * 80):
        with pytest.raises(ipp.PrinterError):
            ipp.check_path(bad)


# ------------------------------------------------------------------ discovery
def _record(name, rtype, rdata):
    return mdns._name(name) + struct.pack(">HHIH", rtype, 1, 120, len(rdata)) + rdata


def test_mdns_answers_become_printers():
    inst = "Office Laser._ipps._tcp.local"
    txt = b"".join(bytes([len(x)]) + x for x in (b"ty=Brother HL-L2350", b"rp=ipp/print"))
    pkt = struct.pack(">HHHHHH", 0, 0x8400, 0, 4, 0, 0)
    pkt += _record("_ipps._tcp.local", mdns.PTR, mdns._name(inst))
    pkt += _record(inst, mdns.SRV, struct.pack(">HHH", 0, 0, 631) + mdns._name("brother.local"))
    pkt += _record(inst, mdns.TXT, txt)
    pkt += _record("brother.local", mdns.A, socket.inet_aton("192.168.1.40"))
    plain = struct.pack(">HHHHHH", 0, 0x8400, 0, 3, 0, 0)
    plain += _record("_ipp._tcp.local", mdns.PTR, mdns._name("Old Inkjet._ipp._tcp.local"))
    plain += _record("Old Inkjet._ipp._tcp.local", mdns.SRV,
                     struct.pack(">HHH", 0, 0, 631) + mdns._name("ink.local"))
    plain += _record("ink.local", mdns.A, socket.inet_aton("192.168.1.41"))
    found = mdns.printers_from(mdns.parse(pkt) + mdns.parse(plain))
    assert found[0] == {"name": "Brother HL-L2350", "host": "192.168.1.40", "port": 631,
                        "path": "ipp/print", "encrypted": True}
    assert found[1]["host"] == "192.168.1.41" and found[1]["encrypted"] is False
    loop = struct.pack(">HHHHHH", 0, 0, 0, 1, 0, 0) + b"\xc0\x0c"   # a name pointing at itself
    with pytest.raises(ValueError):
        mdns.parse(loop)
    rng = random.Random(3)
    for _ in range(2000):
        junk = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 60)))
        try:
            mdns.parse(junk)
        except (ValueError, struct.error, IndexError):
            pass


# ------------------------------------------------------------------ the service
def test_prints_only_to_a_trusted_printer(wallet_dir, printer, tmp_path):
    d, w = wallet_dir
    svc = svc_for(d, tmp_path)
    assert svc.files() == [w["address"]]
    with pytest.raises(printing.PrintError, match="trust"):
        svc.print(w["address"], "127.0.0.1", printer.port)
    assert printer.jobs == []
    info = svc.check("127.0.0.1", printer.port)
    assert info["model"] == "Fake LaserJet 1000" and not info["trusted"]
    svc.trust("127.0.0.1", printer.port, info["fingerprint"])
    assert svc.check("127.0.0.1", printer.port)["trusted"]
    r = svc.print(w["address"], "127.0.0.1", printer.port)
    assert r["job"] == 1 and printer.jobs[0]["format"] == "image/jpeg"
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(printer.jobs[0]["data"]))
    assert img.size == (2550, 3300)                       # US Letter at 300 dpi


def test_changed_certificate_sends_nothing(wallet_dir, printer, tmp_path, monkeypatch):
    d, w = wallet_dir
    svc = svc_for(d, tmp_path)
    svc.trust("127.0.0.1", printer.port, svc.check("127.0.0.1", printer.port)["fingerprint"])
    printer.new_certificate()                             # a swapped printer, or a middleman
    made = []
    monkeypatch.setattr(paper, "jpeg", lambda w: made.append(1) or b"x")
    with pytest.raises(ipp.PrinterError, match="certificate changed"):
        svc.print(w["address"], "127.0.0.1", printer.port)
    assert printer.jobs == [] and made == []              # the page was never even made
    info = svc.check("127.0.0.1", printer.port)
    assert info["changed"] and not info["trusted"]


def test_pdf_only_printer_gets_pdf_and_no_format_is_refused(wallet_dir, tmp_path):
    d, w = wallet_dir
    p = FakePrinter(formats=("application/pdf",), chunked=True)
    try:
        svc = svc_for(d, tmp_path)
        svc.trust("127.0.0.1", p.port, svc.check("127.0.0.1", p.port)["fingerprint"])
        svc.print(w["address"], "127.0.0.1", p.port)
        assert p.jobs[0]["format"] == "application/pdf" and p.jobs[0]["data"].startswith(b"%PDF")
    finally:
        p.close()
    q = FakePrinter(formats=("image/pwg-raster",))
    try:
        svc.trust("127.0.0.1", q.port, svc.check("127.0.0.1", q.port)["fingerprint"])
        with pytest.raises(printing.PrintError, match="neither JPEG nor PDF"):
            svc.print(w["address"], "127.0.0.1", q.port)
    finally:
        q.close()


def test_unencrypted_printer_is_refused(wallet_dir, tmp_path):
    d, w = wallet_dir
    p = FakePrinter(tls=False)
    try:
        with pytest.raises(ipp.PrinterError, match="encrypted"):
            svc_for(d, tmp_path).check("127.0.0.1", p.port)
        assert p.jobs == []
    finally:
        p.close()


def test_only_real_paper_wallets_by_name(wallet_dir, printer, tmp_path):
    d, w = wallet_dir
    svc = svc_for(d, tmp_path)
    svc.trust("127.0.0.1", printer.port, svc.check("127.0.0.1", printer.port)["fingerprint"])
    (d / "notes.txt").write_text("hello")
    other = paper.build(paper.random_key(), "signet")
    (d / f"{other['address']}.txt").write_text(paper.text_file(w))     # wrong key inside
    for bad in ("../../etc/passwd", "notes", other["address"], "", "a" * 200):
        with pytest.raises(printing.PrintError):
            svc.print(bad, "127.0.0.1", printer.port)
    assert printer.jobs == []
    with pytest.raises(printing.PrintError):
        svc.handle("print", {"address": w["address"], "host": "127.0.0.1",
                             "port": printer.port, "evil": 1})
    with pytest.raises(printing.PrintError):
        svc.handle("_wallet", {"address": w["address"]})


def test_the_socket_server_and_client(wallet_dir, printer, tmp_path):
    from print_service import serve
    d, w = wallet_dir
    svc = svc_for(d, tmp_path)
    path = os.path.join(tempfile.mkdtemp(), "p.sock")
    calls = [("files", {}), ("check", {"host": "127.0.0.1", "port": printer.port}),
             ("print", {"address": "../x", "host": "127.0.0.1", "port": printer.port})]

    def run():
        for _ in calls:
            serve(svc, path, once=True)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    c = printing.Client(path)
    for _ in range(50):
        if os.path.exists(path):
            break
        time.sleep(0.05)
    assert c.call("files") == [w["address"]]
    time.sleep(0.1)
    assert c.call("check", host="127.0.0.1", port=printer.port)["model"] == "Fake LaserJet 1000"
    time.sleep(0.1)
    with pytest.raises(printing.PrintError, match="no such paper wallet"):
        c.call("print", address="../x", host="127.0.0.1", port=printer.port)
    t.join(5)


# ------------------------------------------------------------------ the screens
def test_print_screens_end_to_end(app, tmp_path, monkeypatch):  # noqa: F811
    from test_app import create_wallet, enter, press, settle, type_text

    def type_ip(a, text):
        ip, port = text.split(":")
        type_text(a, ip)
        press(a, "KEY_RIGHTALT", "KEY_S")                     # sym + S types ':'
        type_text(a, port)
    import app as app_mod
    from ui.print_flow import print_menu
    a = app
    create_wallet(a)
    d = Path(paper_dir := str(tmp_path / "paper"))
    w = paper.build(paper.random_key(), "signet")
    paper.save(paper_dir, w)
    p = FakePrinter()
    try:
        monkeypatch.setattr(printing, "controller", lambda: printing.InProcess(
            printing.PrintService(str(d), str(tmp_path / "pstate"), allow_loopback=True)))
        monkeypatch.setattr(printing.mdns, "browse", lambda timeout=2.5: [])
        monkeypatch.setattr(app_mod, "network_is_up", lambda: False)
        print_menu(a)
        assert a.visible.title == "VAULT MODE"                 # Wi-Fi needed
        a.pop()
        monkeypatch.setattr(app_mod, "network_is_up", lambda: True)
        print_menu(a)
        settle(a)
        assert a.visible.title == "PRINT WHICH?"
        enter(a)
        settle(a)
        assert a.visible.title == "PRINTER" and a.visible.items[0][0] == "TYPE ADDRESS"
        enter(a)
        type_ip(a, f"127.0.0.1:{p.port}")
        enter(a)
        settle(a)
        assert a.visible.title == "NEW PRINTER"
        assert ipp.short_fp(ipp.fingerprint(b"")) not in a.visible.lines
        a.visible.sel = 0
        enter(a)                                               # TRUST
        settle(a)
        assert a.visible.title == "PRINT PRIVATE KEY?" and a.visible.sel == 1   # starts on CANCEL
        enter(a)                                               # CANCEL: nothing sent
        assert p.jobs == []
        a.home()
        print_menu(a)
        settle(a)
        enter(a)
        settle(a)
        enter(a)
        type_ip(a, f"127.0.0.1:{p.port}")
        enter(a)
        settle(a)
        assert a.visible.title == "PRINT PRIVATE KEY?", (a.visible.title, getattr(a.visible, "lines", None))  # trusted now: straight to the warning
        a.visible.sel = 0
        enter(a)
        settle(a)
        assert a.visible.title == "SENT TO PRINTER", a.visible.lines
        assert len(p.jobs) == 1
    finally:
        p.close()
