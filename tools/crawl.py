"""
Systematic crawler: opens every menu item it can reach (to a depth), on a
funded wallet with the simulated chip, fingerprint and card, and reports any
crash, swallowed traceback or frozen screen. Skips WIPE.

    python3 tools/crawl.py [depth]
"""
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import monkey  # noqa: E402  (sets up the simulator environment)
from monkey import PIN, settle  # noqa: E402

SKIP = ("WIPE",)


def items_of(screen):
    from ui.flow import HomeScreen
    if isinstance(screen, HomeScreen):
        return list(HomeScreen.ITEMS)
    items = getattr(screen, "items", None)
    if isinstance(items, list) and all(isinstance(i, tuple) and i for i in items):
        return [str(i[0]) for i in items]
    return []


def press(app, k):
    app.feed(k, 1)
    app.feed(k, 0)
    settle(app)


def answer_signer(app, budget=30):
    """Get through signer prompts: type the PIN, otherwise back out."""
    for _ in range(budget):
        if app.console is None or not app.console.stack:
            return
        top = type(app.console.stack[-1]).__name__
        if "Pin" in top:
            for c in PIN:
                press(app, f"KEY_{c}")
            press(app, "KEY_ENTER")
        else:
            press(app, "KEY_ESC")


def goto(app, path):
    from ui.flow import HomeScreen
    press(app, "KEY_ESC")
    answer_signer(app)
    app.home()
    if not isinstance(app.visible, HomeScreen):
        raise monkey.Problem(f"couldn't get home (on {monkey.screen_name(app)})")
    for idx in path:
        scr = app.visible
        if not items_of(scr):
            return False
        scr.sel = idx
        press(app, "KEY_ENTER")
        answer_signer(app)
        for f in (0, 1):
            app.render(f)
    return True


def main():
    depth = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    swallowed = []

    def catch(*a, **k):
        swallowed.append(traceback.format_exc())
    traceback.print_exc = catch
    import app as appmod
    appmod.traceback.print_exc = catch
    import tempfile
    import core.config
    from app import App
    from core.signer_service import SignerClient
    from test_app import create_wallet
    from test_core import fund
    import test_app
    test_app.settle = settle
    tmp = tempfile.mkdtemp(prefix="czcrawl-")
    core.config.APP_DIR = os.path.join(tmp, "app")
    app = App(signer=SignerClient(inproc=True, data_dir=os.path.join(tmp, "signer")),
              sync_jobs=True)
    app.settings.network = "signet"
    app.outbox_dir = os.path.join(tmp, "outbox")
    app.start()
    create_wallet(app, PIN)
    fund(app.wallet, 100_000)
    app.refresh_balance()

    problems, visited = [], set()
    frontier = [()]
    t0 = time.time()
    while frontier:
        path = frontier.pop(0)
        before = len(swallowed)
        try:
            ok = goto(app, path)
            name = monkey.screen_name(app)
            labels = items_of(app.visible) if ok else []
        except Exception as e:  # noqa: BLE001
            problems.append((path, repr(e), traceback.format_exc()[-1500:]))
            app = app  # keep going from home
            continue
        if len(swallowed) > before:
            problems.append((path, "background job crashed", swallowed[-1][-1500:]))
        visited.add(name)
        if len(path) < depth:
            for i, lab in enumerate(labels):
                if not any(s in lab.upper() for s in SKIP):
                    frontier.append(path + (i,))
    print(f"opened {len(visited)} distinct screens in {time.time() - t0:.0f}s")
    print(sorted(visited))
    for path, err, tb in problems:
        print("=" * 70)
        print("path", path, err)
        print(tb)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
