"""
Memory-leak check: run each busy part of the wallet in a long loop and watch
whether memory keeps climbing once it's warmed up. Prints a table; exits 1
if anything grows more than its allowance.

    python3 tools/leakcheck.py [scale]       (scale 1 = quick, 5 = thorough)
"""
from __future__ import annotations

import gc
import os
import random
import socket
import sys
import tempfile
import threading
import time
import tracemalloc

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tests"))
# Some build environments patch Pillow with a (bounded) text-mask cache that
# would read as growth here; the device runs stock Pillow, so measure that.
os.environ.setdefault("SANDBOX_PILLOW_KEEP_TEXT_MASKS", "0")
os.environ.setdefault("CZ_SIM", "1")
os.environ.setdefault("CZ_SOUND", "0")
os.environ.setdefault("CZ_ARGON_T", "1")
os.environ.setdefault("CZ_ARGON_M_KIB", "1024")
os.environ.setdefault("CZ_DATA", tempfile.mkdtemp(prefix="czleak-"))

SCALE = int(sys.argv[1]) if len(sys.argv) > 1 else 1
ALLOW_KIB = 256          # growth allowed between the warm and the final reading


def measure(name, setup, step, n):
    """Warm up for n/4 steps, then read memory at 3 points over n steps.
    Leaks grow steadily; caches level off."""
    ctx = setup()
    for _ in range(max(1, n // 4)):
        step(ctx)
    gc.collect()
    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[0]
    marks = []
    for k in range(3):
        for _ in range(n // 3):
            step(ctx)
        gc.collect()
        marks.append((tracemalloc.get_traced_memory()[0] - base) // 1024)
    tracemalloc.stop()
    teardown = ctx.get("teardown") if isinstance(ctx, dict) else None
    if teardown:
        teardown()
    growth = marks[-1]
    steady = marks[-1] - marks[0]
    ok = steady <= ALLOW_KIB
    print(f"{name:34s} {n:7d} steps  KiB after: {marks[0]:6d} {marks[1]:6d} {marks[2]:6d}"
          f"   {'ok' if ok else 'GROWING'}")
    return ok, growth


# ------------------------------------------------------------------ the parts
def app_setup():
    import core.config
    from app import App
    from core.signer_service import SignerClient
    import test_app
    d = tempfile.mkdtemp()
    core.config.APP_DIR = os.path.join(d, "app")
    a = App(signer=SignerClient(inproc=True, data_dir=os.path.join(d, "signer")),
            sync_jobs=True)
    a.settings.network = "signet"
    a.outbox_dir = os.path.join(d, "outbox")
    a.start()
    test_app.create_wallet(a)
    from monkey import KEYS
    return {"app": a, "rnd": random.Random(3), "keys": KEYS, "i": 0}


def app_step(ctx):
    from monkey import settle
    a, rnd = ctx["app"], ctx["rnd"]
    k = rnd.choice(ctx["keys"])
    if k in ("KEY_ESC",) or ctx["i"] % 40 == 0:
        a.home()
    try:
        a.feed(k, 1)
        a.feed(k, 0)
        settle(a, 2)
        a.render(ctx["i"])
        if a.signer.console.stack and ctx["i"] % 7 == 0:
            a.signer.console.cancel()
            settle(a, 2)
    except Exception:  # noqa: BLE001 - the monkey tester hunts crashes; this hunts growth
        a.home()
    ctx["i"] += 1


def signer_setup():
    os.environ["CZ_FORCE_ONLINE"] = "0"           # Vault mode: no online cap in the way
    from test_core import ABANDON, DEST, NET, fund
    from core.config import Settings
    from core.signer import Signer
    from core.wallet import WalletService
    s = Signer(tempfile.mkdtemp())
    info = s.setup("2468", ABANDON, NET)
    w = WalletService(Settings(network=NET), info["descriptor"], info["change_descriptor"],
                      info["fingerprint"], in_memory=True)
    fund(w, 10_000_000)
    psbt = w.build(DEST, 5_000, fee_rate=5)["psbt"]
    return {"s": s, "psbt": psbt}


def signer_step(ctx):
    s = ctx["s"]
    d = s.describe(ctx["psbt"])
    s.sign(ctx["psbt"], d["digest"])
    s.status()


def qr_setup():
    from core import qr
    data = os.urandom(3000)
    return {"frames": qr.bbqr_encode(data, "P"), "qr": qr}


def qr_step(ctx):
    c = ctx["qr"].Collector()
    for f in ctx["frames"]:
        c.add(f)


def prompt_setup():
    import core.config
    from core.signer_service import SignerClient
    import test_app  # noqa: F401
    d = tempfile.mkdtemp()
    core.config.APP_DIR = os.path.join(d, "app")
    sc = SignerClient(inproc=True, data_dir=os.path.join(d, "signer"), sync_jobs=True)
    sc.local.setup("2468", __import__("test_core").ABANDON, "signet")
    return {"sc": sc}


def prompt_step(ctx):
    sc = ctx["sc"]
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", _try(sc.words_prompt)), daemon=True)
    t.start()
    while t.is_alive():
        sc.console.pump()                  # what the signer's screen loop does
        sc.console.cancel()
        time.sleep(0.001)
    sc.console.pump()


def _try(fn):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        return e


def share_setup():
    import http.client
    import ssl
    from core import share
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "h"))
    open(os.path.join(d, "h", "InsertCoin-history-a-20261101-000000.csv"), "w").write("x,y\n")
    srv = share.ShareServer({"history": os.path.join(d, "h")}, share.hash_password("bitcoin"),
                            cert_dir=d, port=0, host="127.0.0.1", timeout_s=3600)
    srv.start()
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return {"srv": srv, "port": srv.port, "tls": ctx, "http": http.client,
            "teardown": srv.stop}


def share_step(ctx):
    c = ctx["http"].HTTPSConnection("127.0.0.1", ctx["port"], context=ctx["tls"], timeout=5)
    c.request("GET", "/")
    c.getresponse().read()
    c.close()


def print_setup():
    from core import paper, printing
    from fake_printer import FakePrinter
    d = tempfile.mkdtemp()
    w = paper.build(paper.random_key(), "signet")
    paper.save(d, w)
    p = FakePrinter()
    svc = printing.PrintService(d, os.path.join(d, "st"), allow_loopback=True)
    svc.trust("127.0.0.1", p.port, svc.check("127.0.0.1", p.port)["fingerprint"])
    return {"svc": svc, "p": p, "addr": w["address"], "teardown": p.close}


def print_step(ctx):
    ctx["svc"].print(ctx["addr"], "127.0.0.1", ctx["p"].port)
    ctx["p"].jobs.clear()


def electrum_setup():
    from core import electrum
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        conn, _ = srv.accept()
        f = conn.makefile("rwb")
        import json
        for line in f:
            req = json.loads(line)
            f.write((json.dumps({"id": req["id"], "result": ["x" * 200]}) + "\n").encode())
            f.flush()
    threading.Thread(target=serve, daemon=True).start()
    c = electrum.ElectrumClient.__new__(electrum.ElectrumClient)
    c._sock = socket.create_connection(("127.0.0.1", port))
    c._buf, c._id = b"", 0
    return {"c": c}


def electrum_step(ctx):
    ctx["c"].call("blockchain.scripthash.get_history", "00" * 32)


PARTS = [
    ("app: random keys, every screen", app_setup, app_step, 3000),
    ("signer: check + sign a payment", signer_setup, signer_step, 300),
    ("QR: collect a multi-part PSBT", qr_setup, qr_step, 600),
    ("signer screen: open and cancel", prompt_setup, prompt_step, 300),
    ("SHARE FILES: page requests", share_setup, share_step, 300),
    ("PRINT: send a paper wallet", print_setup, print_step, 60),
    ("Electrum: request and reply", electrum_setup, electrum_step, 2000),
]


def main() -> int:
    bad = 0
    print(f"leak check, scale {SCALE}\n")
    for name, setup, step, n in PARTS:
        try:
            ok, _ = measure(name, setup, step, n * SCALE)
        except Exception as e:  # noqa: BLE001
            print(f"{name:34s} could not run: {type(e).__name__}: {e}")
            ok = False
        bad += not ok
    print(f"\n{'no leaks found' if not bad else f'{bad} part(s) growing'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
