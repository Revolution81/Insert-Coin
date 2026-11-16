"""
Monkey tester: drives the whole app (signer in-process) with random key
presses and reports every crash, swallowed traceback or frozen screen.

    python3 tools/monkey.py [seeds] [steps]

Each seed makes a fresh wallet, gives it test coins, then presses keys at
random. When the signer asks for a PIN it usually types the right one, so the
walk gets past the locked doors. Any failure prints the seed and the last keys
so it can be replayed.
"""
import collections
import os
import random
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("CZ_DATA", tempfile.mkdtemp(prefix="czmonkey-"))
for k, v in {"CZ_SIM": "1", "CZ_ARGON_T": "1", "CZ_ARGON_M_KIB": "1024", "CZ_FORCE_ONLINE": "0",
             "CZ_SE": "sim", "CZ_FINGER": "sim", "CZ_NFC": "sim-satscard", "CZ_BACKUP_DIRS": ""}.items():
    os.environ.setdefault(k, v)

import core.config  # noqa: E402
from app import App  # noqa: E402
from core.signer_service import SignerClient  # noqa: E402

PIN = "1234"
NAV = ["KEY_UP", "KEY_DOWN", "KEY_LEFT", "KEY_RIGHT"]
KEYS = (NAV * 6 + ["KEY_ENTER"] * 8 + ["KEY_BACKSPACE"] * 3 + ["KEY_ESC"] * 1
        + ["KEY_TAB"] + [f"KEY_{c}" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"]
        + ["KEY_SPACE", "KEY_DOT", "KEY_HOME", "KEY_END", "KEY_PAGEDOWN"])


class Problem(Exception):
    pass


def settle(app, timeout=20.0):
    end = time.monotonic() + timeout
    while True:
        app.pump()
        if app.idle():
            app.pump()
            if app.idle():
                return
        if time.monotonic() > end:
            raise Problem("frozen: app never settled")
        time.sleep(0.001)


def screen_name(app):
    if app.console is not None and app.console.stack:
        s = app.console.stack[-1]
        return "signer:" + type(s).__name__ + ":" + str(getattr(s, "title", ""))[:24]
    s = app.visible
    return type(s).__name__ + ":" + str(getattr(s, "title", ""))[:24]


def run(seed, steps, swallowed):
    rnd = random.Random(seed)
    tmp = tempfile.mkdtemp(prefix=f"czm{seed}-")
    core.config.APP_DIR = os.path.join(tmp, "app")
    app = App(signer=SignerClient(inproc=True, data_dir=os.path.join(tmp, "signer")),
              sync_jobs=True)
    app.settings.network = "signet"
    app.outbox_dir = os.path.join(tmp, "outbox")
    app.start()
    from test_app import create_wallet
    from test_core import fund
    import test_app
    test_app.settle = settle
    create_wallet(app, PIN)
    for amt in (100_000, 25_000, 7_000):
        fund(app.wallet, amt)
    app.refresh_balance()
    keys = collections.deque(maxlen=40)
    seen = collections.Counter()
    queue = []
    for i in range(steps):
        name = screen_name(app)
        seen[name] += 1
        if not queue:
            if "Pin" in name and rnd.random() < 0.8:
                queue = [f"KEY_{c}" for c in PIN] + ["KEY_ENTER"]
            elif rnd.random() < 0.03:
                queue = ["KEY_ESC"]       # go home now and then, to explore wider
            else:
                queue = [rnd.choice(KEYS)]
        k = queue.pop(0)
        keys.append(k)
        before = len(swallowed)
        try:
            app.feed(k, 1)
            app.feed(k, 0)
            settle(app)
            for f in (0, 1):
                img = app.render(f)
                if img.size != (320, 170):
                    raise Problem(f"bad frame size {img.size}")
        except Exception as e:  # noqa: BLE001
            tb = traceback.format_exc()
            return {"seed": seed, "step": i, "screen": name, "keys": list(keys),
                    "error": repr(e), "tb": tb}, seen
        if len(swallowed) > before:
            return {"seed": seed, "step": i, "screen": name, "keys": list(keys),
                    "error": "background job crashed", "tb": swallowed[-1]}, seen
    return None, seen


def main():
    seeds = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    steps = int(sys.argv[2]) if len(sys.argv) > 2 else 1500
    swallowed = []
    real = traceback.print_exc

    def catch(*a, **k):
        swallowed.append(traceback.format_exc())
    traceback.print_exc = catch
    import app as appmod
    appmod.traceback.print_exc = catch
    problems, allseen = [], collections.Counter()
    for s in range(seeds):
        t0 = time.time()
        prob, seen = run(s, steps, swallowed)
        allseen.update(seen)
        print(f"seed {s}: {'PROBLEM ' + prob['error'] if prob else 'ok'}  "
              f"({len(seen)} screens, {time.time() - t0:.0f}s)", flush=True)
        if prob:
            problems.append(prob)
    traceback.print_exc = real
    print(f"\n{len(allseen)} distinct screens visited")
    for p in problems:
        print("=" * 70)
        print(f"seed {p['seed']} step {p['step']} on {p['screen']}: {p['error']}")
        print("last keys:", " ".join(k[4:] for k in p["keys"]))
        print(p["tb"][-2500:])
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
