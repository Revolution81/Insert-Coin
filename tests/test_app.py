"""
Drive the whole app with simulated key presses: create a wallet, receive,
send (Vault mode -> outbox), lock/unlock, restore. Jobs run inline.
"""
import os

import pytest

from app import App
from core.signer_service import SignerClient
from ui.flow import ConfirmSendScreen, HomeScreen, LockedScreen, ReceiveScreen
from ui.widgets import MessageScreen, PinScreen, ShowWordsScreen, WordEntryScreen

from test_core import DEST, fund


def settle(app, timeout=10.0):
    """Let background work finish: the signer's prompts run on their own
    thread, like on the device. Stops when nothing is running, or when the
    signer's screen is up waiting for keys."""
    import time
    end = time.monotonic() + timeout
    while True:
        app.pump()
        if app.idle():
            app.pump()
            if app.idle():
                return
        if time.monotonic() > end:
            raise TimeoutError("app didn't settle")
        time.sleep(0.002)


def press(app, *names):
    for n in names:
        app.feed(n, 1)
        app.feed(n, 0)
        settle(app)


def type_text(app, s):
    for ch in s:
        if ch == " ":
            press(app, "KEY_SPACE")
        elif ch.isupper():
            press(app, "KEY_LEFTSHIFT", f"KEY_{ch}")
        elif ch == ".":
            press(app, "KEY_DOT")
        else:
            press(app, f"KEY_{ch.upper()}")


def enter(app):
    press(app, "KEY_ENTER")


@pytest.fixture
def app(tmp_path, monkeypatch):
    import core.config
    monkeypatch.setattr(core.config, "APP_DIR", str(tmp_path / "app"))   # fresh settings
    a = App(signer=SignerClient(inproc=True, data_dir=str(tmp_path / "signer")),
            sync_jobs=True)
    a.settings.network = "signet"
    a.outbox_dir = str(tmp_path / "outbox")
    a.start()
    return a


def create_wallet(app, pin="1234"):
    enter(app)                                    # CREATE NEW WALLET
    enter(app)                                    # 12 WORDS
    assert isinstance(app.visible, ShowWordsScreen)
    words = list(app.visible.words)
    for _ in range(3):
        enter(app)                                # page through 3 pages
    assert isinstance(app.visible, WordEntryScreen)
    for p in app.visible.positions:
        type_text(app, words[p - 1] + " ")
    enter(app)                                    # NO PASSPHRASE
    type_text(app, pin)
    enter(app)
    type_text(app, pin)
    enter(app)
    assert isinstance(app.visible, MessageScreen) and app.visible.title == "READY!"
    enter(app)
    assert isinstance(app.visible, HomeScreen)
    return words


def render_all(app):
    for f in (0, 1):
        img = app.render(f)
        assert img.size == (320, 170)


def test_create_receive_send_lock(app):
    create_wallet(app)
    render_all(app)

    press(app, "KEY_R")
    assert isinstance(app.visible, ReceiveScreen) and app.visible.addr.startswith("tb1q")
    render_all(app)
    press(app, "KEY_BACKSPACE")

    fund(app.wallet, 100_000)
    app.refresh_balance()
    assert app.balance["total"] == 100_000

    press(app, "KEY_S")                            # send
    type_text(app, DEST)
    enter(app)
    type_text(app, "30000")
    enter(app)
    render_all(app)
    enter(app)                                     # NORMAL fee
    assert isinstance(app.visible, ConfirmSendScreen)
    render_all(app)
    s = app.visible.s
    assert s["spend"] == 30_000 and s["cap_ok"]
    enter(app)                                     # cursor starts on NO
    assert isinstance(app.visible, HomeScreen)         # cancelled, nothing signed
    assert app.outbox_count() == 0

    press(app, "KEY_S")
    type_text(app, DEST)
    enter(app)
    type_text(app, "30000")
    enter(app)
    enter(app)
    press(app, "KEY_Z")                            # move to YES
    enter(app)
    assert app.visible.title == "SIGNED TX"            # Vault mode: saved + shown as QR
    assert app.outbox_count() == 1
    fee = s["fee"]
    assert app.balance["total"] == 100_000 - 30_000 - fee
    enter(app)

    press(app, "KEY_L")                            # lock: the signer asks for the PIN
    assert isinstance(app.visible, PinScreen) and app.console.stack
    press(app, "KEY_ESC")                          # backing out leaves it locked
    assert isinstance(app.visible, LockedScreen) and not app.console.stack
    enter(app)                                     # OK: ask again
    assert isinstance(app.visible, PinScreen)
    type_text(app, "9999")
    enter(app)
    assert "Wrong PIN" in app.visible.error and app.visible.tries[0] == 9
    type_text(app, "1234")
    enter(app)
    assert isinstance(app.visible, HomeScreen)


def test_history_coins_settings_render(app):
    create_wallet(app)
    fund(app.wallet, 50_000)
    app.refresh_balance()
    for keys in (("KEY_H",), ("KEY_M",)):
        press(app, *keys)
        render_all(app)
        press(app, "KEY_ESC")
        assert isinstance(app.visible, HomeScreen)
    # C is the right arrow in menus, so COINS is reached by moving to it
    from ui.flow import CoinsScreen
    app.visible.sel = 0
    press(app, "KEY_C", "KEY_C", "KEY_ENTER")
    assert isinstance(app.visible, CoinsScreen) and app.visible.coins[0].sats == 50_000
    enter(app)                                     # pick the coin
    assert app.visible.chosen
    render_all(app)
    press(app, "KEY_ESC")
    # SETUP via the menu: move to it with arrows (D/Z/X/C)
    app.visible.sel = 0
    press(app, "KEY_X", "KEY_C", "KEY_C")          # down, right, right -> SETUP
    enter(app)
    render_all(app)
    assert app.visible.title == "SETUP"


def test_spend_cap_via_settings(app, monkeypatch):
    create_wallet(app)
    press(app, "KEY_X", "KEY_C", "KEY_C")
    enter(app)                                     # SETUP
    enter(app)                                     # SECURITY
    app.visible.sel = [it[0] for it in app.visible.items].index("ONLINE CAP")
    enter(app)                                     # SPEND CAP
    for _ in range(5):
        press(app, "KEY_BACKSPACE")
    type_text(app, "25000")
    enter(app)                                     # the signer asks for the PIN
    assert isinstance(app.visible, PinScreen) and "25,000" in app.visible.note
    type_text(app, "1234")
    enter(app)
    assert app.visible.title == "SECURITY"
    assert app.visible.items[app.visible.sel][0] == "ONLINE CAP"
    assert app.signer.status()["cap"]["cap"] == 25000


def test_restore_known_words(app):
    press(app, "KEY_X")                            # RESTORE FROM WORDS
    enter(app)
    enter(app)                                     # 12 WORDS
    for w in ["abandon"] * 11 + ["about"]:
        type_text(app, w[:4] + " ")                # 4 letters + SPACE takes the word
    enter(app)                                     # NO PASSPHRASE
    type_text(app, "4321")
    enter(app)
    type_text(app, "4321")
    enter(app)
    enter(app)
    assert isinstance(app.visible, HomeScreen)
    assert app.fingerprint == "73c5da0a"           # BIP32 fingerprint of the test vector


def test_bad_checksum_rejected(app):
    press(app, "KEY_X")
    enter(app)
    enter(app)
    for w in ["abandon"] * 12:
        type_text(app, w[:4] + " ")
    assert isinstance(app.visible, WordEntryScreen)
    assert "Checksum" in app.visible.error


def test_send_everything_typed_as_an_amount_offers_fee_from_amount(app):  # noqa: F811
    from test_core import DEST, fund
    create_wallet(app)
    fund(app.wallet, 2_000)
    app.refresh_balance()
    press(app, "KEY_S")
    type_text(app, DEST)
    enter(app)
    type_text(app, "2000")
    enter(app)
    render_all(app)
    enter(app)                                     # NORMAL fee: 2,000 + fee won't fit
    render_all(app)
    assert app.visible.title == "FEE FROM AMOUNT?"
    press(app, "KEY_Z")                            # YES
    enter(app)
    render_all(app)
    assert isinstance(app.visible, ConfirmSendScreen)
    s = app.visible.s
    assert s["spend"] == 2_000 - s["fee"] and s["fee"] > 0


def test_merge_coins_from_the_coins_screen(app):  # noqa: F811
    from test_core import fund
    create_wallet(app)
    for amt in (40_000, 9_000, 3_000):
        fund(app.wallet, amt)
    app.refresh_balance()
    press(app, "KEY_C")                            # home grid: RECEIVE
    press(app, "KEY_C")                            # COINS
    enter(app)
    assert app.visible.title == "COINS"
    render_all(app)
    enter(app)                                     # pick one coin only
    press(app, "KEY_M")
    assert app.visible.title == "MERGE"            # needs two or more
    enter(app)
    enter(app)                                     # unpick: none chosen = all
    press(app, "KEY_M")
    assert app.visible.title == "MERGE FEE"
    enter(app)                                     # cheapest
    assert app.visible.title == "MERGE COINS?"
    render_all(app)
    press(app, "KEY_Z")
    enter(app)                                     # YES -> the signer's confirm screen
    assert isinstance(app.visible, ConfirmSendScreen)
    s = app.visible.s
    assert s["spend"] == 0 and s["inputs"] == 3 and len(s["outputs"]) == 1
    render_all(app)
    press(app, "KEY_Z")
    enter(app)                                     # sign: Vault mode keeps it in the Outbox
    assert app.outbox_count() == 1
    assert app.balance["total"] == 52_000 - s["fee"]


def test_app_mode_exit_to_menu(app, monkeypatch):  # noqa: F811
    import ui.flow as flow
    from ui.flow import settings_screen
    monkeypatch.setattr(flow, "APP_MODE", True)
    create_wallet(app)
    settings_screen(app)
    labels = [i[0] for i in app.visible.items]
    assert labels[-2:] == ["EXIT TO MENU", "WIPE DEVICE"]
    app.visible.sel = labels.index("EXIT TO MENU")
    enter(app)
    assert app.visible.title == "EXIT?"
    press(app, "KEY_Z")
    enter(app)
    assert app.exit_requested and not app.signer.status()["unlocked"]


def test_no_exit_item_outside_app_mode(app):  # noqa: F811
    from ui.flow import settings_screen
    create_wallet(app)
    settings_screen(app)
    assert "EXIT TO MENU" not in [i[0] for i in app.visible.items]
