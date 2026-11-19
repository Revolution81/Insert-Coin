"""SETUP > AUTO LOCK, and SHARE FILES keeping the wallet awake (0.8.17)."""
import time

import pytest

from core import share
from ui.flow import HomeScreen, LockedScreen, auto_lock_menu
from ui.widgets import PinScreen
from test_app import app, create_wallet, enter, press, settle, type_text  # noqa: F401


def _choose(app, label):
    menu = app.visible
    menu.sel = [i[0] for i in menu.items].index(label)
    enter(app)


def _idle(app, seconds):
    app.last_input = time.monotonic() - seconds
    app.check_idle()


def test_default_is_two_minutes_and_idle_locks(app):  # noqa: F811
    create_wallet(app)
    assert app.signer.status()["lock_after"] == 120 and app.settings.auto_lock_s == 120
    _idle(app, 60)
    assert isinstance(app.visible, HomeScreen)
    _idle(app, 130)
    assert isinstance(app.visible, (LockedScreen, PinScreen))


def test_longer_needs_the_pin_shorter_doesnt(app):  # noqa: F811
    create_wallet(app)
    auto_lock_menu(app)
    _choose(app, "10 MIN")                                   # longer: the signer asks
    assert isinstance(app.visible, PinScreen) and "10 min" in app.visible.note
    type_text(app, "1234")
    enter(app)
    settle(app)
    assert app.signer.status()["lock_after"] == 600 and app.settings.auto_lock_s == 600
    assert app.visible.title == "AUTO LOCK"
    enter(app)
    _idle(app, 300)
    assert isinstance(app.visible, HomeScreen)               # 5 min idle: still open
    auto_lock_menu(app)
    _choose(app, "5 MIN")                                    # shorter: no PIN
    assert app.signer.status()["lock_after"] == 300
    assert not isinstance(app.visible, PinScreen)


def test_custom_minutes_and_off(app):  # noqa: F811
    create_wallet(app)
    auto_lock_menu(app)
    _choose(app, "CUSTOM")
    type_text(app, "0")
    enter(app)
    assert "1 to 1440" in app.visible.error                  # 0 isn't a time
    press(app, "KEY_BACKSPACE")
    type_text(app, "45")
    enter(app)
    assert isinstance(app.visible, PinScreen)
    type_text(app, "1234")
    enter(app)
    settle(app)
    assert app.signer.status()["lock_after"] == 45 * 60
    enter(app)
    auto_lock_menu(app)
    vals = {i[0]: i[2] for i in app.visible.items}
    assert vals["CUSTOM"] == "45 MIN"
    _choose(app, "OFF")
    type_text(app, "1234")
    enter(app)
    settle(app)
    assert app.signer.status()["lock_after"] == 0
    enter(app)
    _idle(app, 10 * 3600)
    assert isinstance(app.visible, HomeScreen)               # OFF: never locks by itself
    signer = app.signer.local._s
    signer._last_use -= 10 * 3600
    assert app.signer.status()["unlocked"]                  # nor does the signer


def test_the_app_cant_lengthen_it_without_the_pin(app):  # noqa: F811
    from core.signer_service import RemoteError
    create_wallet(app)
    with pytest.raises(RemoteError, match="needs the PIN"):
        app.signer.shorten_auto_lock(seconds=3600)
    with pytest.raises(RemoteError, match="needs the PIN"):
        app.signer.shorten_auto_lock(seconds=0)
    with pytest.raises(RemoteError):
        app.signer.shorten_auto_lock(seconds=5)              # under a minute
    assert app.signer.status()["lock_after"] == 120


def test_signer_counts_every_key_but_never_revives_a_forgotten_seed(app):  # noqa: F811
    create_wallet(app)
    signer = app.signer.local._s
    signer._last_use -= 100                                  # 100 s in the menus
    press(app, "KEY_X")                                      # an ordinary menu key
    assert time.monotonic() - signer._last_use < 5           # counted as "in use"
    signer._last_use -= 200                                  # past the 2 minutes
    press(app, "KEY_X")
    assert not signer.status()["unlocked"]                   # forgotten, not revived


def test_sharing_keeps_the_wallet_awake(app, monkeypatch):  # noqa: F811
    from ui.share_flow import SharingScreen
    create_wallet(app)
    monkeypatch.setattr(share, "controller",
                        lambda d: share.InProcess(d, port=0, host="127.0.0.1"))
    scr = SharingScreen()
    app.push(scr)
    app.render(0)
    signer = app.signer.local._s
    for _ in range(3):                                       # 3 x 5 minutes, no keys
        _idle(app, 300)
        signer._last_use -= 100
        scr._kept = 0
        app.render(0)
        assert app.visible is scr and scr.ctl and scr.ctl.srv.running
        assert signer.status()["unlocked"]
    press(app, "KEY_ENTER")                                  # stop sharing
    assert app.visible.title == "SHARING STOPPED"
    _idle(app, 60)
    assert not isinstance(app.visible, (LockedScreen, PinScreen))   # counts from the end


def test_setup_groups_hold_every_setting_once(app):  # noqa: F811
    from ui.flow import SETUP_DIRECT, SETUP_GROUPS, settings_screen
    create_wallet(app)
    settings_screen(app)
    settle(app)
    top = [i[0] for i in app.visible.items]
    assert top == [g for g, _ in SETUP_GROUPS] + SETUP_DIRECT and top[-1] == "WIPE DEVICE"
    seen = []
    for g, labels in SETUP_GROUPS:
        app.visible.sel = top.index(g)
        enter(app)
        assert app.visible.title == g and [i[0] for i in app.visible.items] == labels
        seen += labels
        app.pop()
        assert app.visible.title == "SETUP"
    assert len(seen) == len(set(seen)) == 29
