"""
The trusted path: the signer owns the screen and keyboard while it asks for
your PIN, shows seed words or asks you to confirm a payment.
"""
import os
import socket
import threading
import time

import pytest
from PIL import Image

from core.screen_link import FRAME, ScreenClient, ScreenServer
from core.signer_service import RemoteError
from core.trusted import security_words
from ui import theme as t
from ui.console import Busy, Cancelled, Console
from ui.flow import HomeScreen, LockedScreen, ReceiveScreen
from ui.secure_flows import AddressScreen, SecurePinScreen
from ui.widgets import MessageScreen, PinScreen, ShowWordsScreen

from test_app import app, create_wallet, enter, press, settle, type_text  # noqa: F401
from test_core import DEST, fund

GOLD = t.PAL["gold"]
INK = t.PAL["ink"]


def corner(img):
    return img.getpixel((0, 0))


def test_corner_is_the_signers(app):  # noqa: F811
    create_wallet(app)
    # the app tries to fake the shield: paint the whole frame gold
    real = HomeScreen.render
    HomeScreen.render = lambda self, a, f: Image.new("RGB", (t.W, t.H), GOLD)
    try:
        img = app.render(0)
        assert corner(img) == INK                   # overwritten by the signer
        assert img.getpixel((t.STAMP_W + 5, 5)) == GOLD
    finally:
        HomeScreen.render = real
    press(app, "KEY_L")                             # lock: the signer asks for the PIN
    assert app.console.stack
    assert corner(app.render(0)) == GOLD


def test_keys_on_the_signer_never_reach_the_app(app, monkeypatch):  # noqa: F811
    create_wallet(app)
    seen = []
    real_feed = app.tr.feed
    monkeypatch.setattr(app.tr, "feed", lambda n, v: (seen.append(n) if v else None, real_feed(n, v))[1])
    press(app, "KEY_L")
    seen.clear()
    type_text(app, "1234")
    enter(app)
    assert isinstance(app.visible, HomeScreen)
    # (the release of the last OK may land after the signer let go: harmless)
    assert not [k for k in seen if k[-1].isdigit() or k == "KEY_ENTER"], seen


def test_security_words_live_with_the_signer(app, tmp_path):  # noqa: F811
    create_wallet(app)
    words = security_words(str(tmp_path / "signer"))
    assert os.path.exists(tmp_path / "signer" / "security_words")
    assert app.console.words == words and all(words)
    press(app, "KEY_L")
    scr = app.visible
    assert isinstance(scr, SecurePinScreen) and scr.words == words
    app.render(0)
    # the app's own folder never holds them
    for root, _, files in os.walk(os.environ.get("CZ_DATA", str(tmp_path))):
        if "signer" in root:
            continue
        for f in files:
            with open(os.path.join(root, f), "rb") as fh:
                assert words[0].encode() + b" " + words[1].encode() not in fh.read()


def test_seed_words_shown_only_by_the_signer(app):  # noqa: F811
    words = create_wallet(app)
    results = []
    app.prompt("words_prompt", lambda app, r: results.append(r))
    settle(app)
    assert isinstance(app.visible, PinScreen) and app.console.stack
    type_text(app, "1234")
    enter(app)
    scr = app.visible
    assert isinstance(scr, ShowWordsScreen) and scr.words == words
    for _ in range(3):
        enter(app)
    assert results == [None]                        # the app gets nothing back
    assert not app.console.stack and scr.words == []


def test_cancel_and_busy(app):  # noqa: F811
    create_wallet(app)
    fund(app.wallet, 100_000)
    built = app.wallet.build(DEST, 10_000, 2)
    errors = []

    def call():
        try:
            app.signer.sign_prompt(psbt=built["psbt"])
        except RemoteError as e:
            errors.append(e.kind)
    th = threading.Thread(target=call)
    th.start()
    while not app.console.stack:
        app.pump()
        time.sleep(0.002)
    settle(app)
    with pytest.raises(Busy):                       # one prompt at a time
        app.console.session(lambda c: None)
    press(app, "KEY_BACKSPACE")                     # back out of the confirm screen
    th.join(5)
    assert errors == ["Cancelled"]
    assert app.outbox_count() == 0


def test_idle_prompt_cancels_itself(tmp_path):
    from core.signer import Signer
    from core.trusted import Guarded
    c = Console(Guarded(Signer(str(tmp_path))), idle_s=0.01)
    out = []

    def run():
        try:
            c.session(lambda c: c.push(MessageScreen("HI", ["x"])))
        except Cancelled:
            out.append("cancelled")
    th = threading.Thread(target=run)
    th.start()
    while not c.stack:
        c.pump()
    time.sleep(0.05)
    c.pump()
    th.join(5)
    assert out == ["cancelled"] and not c.stack


def test_verify_receive_address_on_the_signer(app):  # noqa: F811
    create_wallet(app)
    press(app, "KEY_R")
    shown = app.visible.addr
    press(app, "KEY_V")
    assert isinstance(app.visible, AddressScreen) and app.visible.addr == shown
    app.render(0)
    enter(app)
    assert isinstance(app.visible, ReceiveScreen)
    # an app that shows the wrong address gets caught
    app.visible.addr = "tb1qattackerxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    press(app, "KEY_V")
    enter(app)
    assert app.visible.title == "MISMATCH!"


def test_wipe_through_the_signer(app):  # noqa: F811
    create_wallet(app)
    app.prompt("wipe_prompt", lambda app, _: app.reset())
    settle(app)
    assert app.visible.title == "WIPE DEVICE?" and app.console.stack
    press(app, "KEY_Z")
    enter(app)
    type_text(app, "1234")
    enter(app)
    assert app.visible.title == "WELCOME"
    assert app.signer.status()["has_seed"] is False


def test_soft_locked_screen_unlocks_via_signer(app):  # noqa: F811
    create_wallet(app)
    app.lock(full=True)
    settle(app)
    press(app, "KEY_ESC")                           # back out: stays locked
    assert isinstance(app.visible, LockedScreen)
    enter(app)
    type_text(app, "1234")
    enter(app)
    assert isinstance(app.visible, HomeScreen)


# ---------------------------------------------------------------- the wire
def test_screen_link_round_trip():
    a, b = socket.socketpair()
    srv = ScreenServer(path="unused")
    srv.attach(a)
    cli = ScreenClient(sock=b)
    assert cli.poll(1) == [("secure", False)]
    img = Image.new("RGB", (t.W, t.H), (1, 2, 3))
    cli.show(img)
    for _ in range(100):
        if srv.take_fresh():
            break
        time.sleep(0.01)
    assert srv.frame().getpixel((10, 10)) == (1, 2, 3)
    srv.send_key("KEY_A", 1)
    srv.send_mode(True)
    got = []
    for _ in range(10):
        got += cli.poll(0.2)
        if len(got) >= 2:
            break
    assert got == [("key", "KEY_A", 1), ("secure", True)]


def test_screen_link_drops_garbage():
    a, b = socket.socketpair()
    srv = ScreenServer(path="unused")
    srv.attach(a)
    b.sendall(b"X" + b"\0" * 100)                   # not a frame
    b.settimeout(2)
    data = b""
    try:
        while True:
            chunk = b.recv(100)
            if not chunk:
                break
            data += chunk
    except socket.timeout:
        pytest.fail("signer kept a connection that sent garbage")
    assert srv.frame() is None
    assert FRAME == t.W * t.H * 3


def test_konami_code_on_unlock_shows_the_secret(app):  # noqa: F811
    from ui.egg import EggScreen
    create_wallet(app)
    app.lock(full=True)
    settle(app)
    enter(app)                                      # the signer's UNLOCK screen
    before = app.signer.status()
    type_text(app, "ffxxzczcba")                    # UP UP DOWN DOWN LEFT RIGHT LEFT RIGHT B A
    enter(app)                                      # START
    assert isinstance(app.visible, EggScreen)
    app.visible.render(app, 0)
    after = app.signer.status()                     # no try spent, nothing opened
    assert {k: v for k, v in after.items() if k != "cap"} == \
        {k: v for k, v in before.items() if k != "cap"}
    press(app, "KEY_A")                             # any key: back to the PIN
    assert app.visible.title == "UNLOCK"
    type_text(app, "1234")
    enter(app)
    assert isinstance(app.visible, HomeScreen)


def test_konami_code_cant_be_a_pin(tmp_path):
    import pytest
    from core.vault import Vault, VaultError
    v = Vault(str(tmp_path))
    with pytest.raises(VaultError):
        v.create("FFXXZCZCBA", "abandon " * 11 + "about", "signet")
