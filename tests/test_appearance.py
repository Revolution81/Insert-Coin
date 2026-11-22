"""SETUP › APPEARANCE: balance digits and box color."""
from ui import theme as t
from ui.appearance import ColorScreen
from ui.flow import HomeScreen, coins_screen, settings_screen
from ui.widgets import MenuScreen

from test_app import app, create_wallet, enter, press, settle  # noqa: F401
from test_core import fund


def open_appearance(app):  # noqa: F811
    settings_screen(app, at="APPEARANCE")
    settle(app)
    m = app.visible
    m.sel = [i[0] for i in m.items].index("APPEARANCE")
    enter(app)
    assert isinstance(app.visible, MenuScreen) and app.visible.title == "APPEARANCE"
    return app.visible


def test_balance_digits_choice(app):  # noqa: F811
    create_wallet(app)
    fund(app.wallet, 2_150_000)
    app.refresh_balance()
    assert app.settings.amount_style == "big"              # all big by default
    app.home()
    big = app.render(0).tobytes()
    m = open_appearance(app)
    m.sel = 0
    enter(app)                                             # BALANCE DIGITS -> SMALL SATS
    assert app.settings.amount_style == "split"
    assert dict((i[0], i[2]) for i in app.visible.items)["BALANCE DIGITS"] == "SMALL SATS"
    app.home()
    assert app.render(0).tobytes() != big


def test_bright_colors_are_toned_down():
    try:
        t.set_box_color((255, 255, 255))
        r, g, b = t.PAL["win_top"]
        assert 0.299 * r + 0.587 * g + 0.114 * b <= t.MAX_BOX_LUMA + 1
        t.set_box_color((200, 0, 40))
        assert t.PAL["win_top"] == (200, 0, 40)
        t.set_box_color(None)
        assert t.PAL["win_top"] == t.DEFAULT_BOX and t.PAL["win_bot"] == (16, 28, 104)
    finally:
        t.set_box_color(None)


def _shown(menu, label):
    return dict((i[0], i[2] if len(i) > 2 else "") for i in menu.items)[label]


def test_box_color_presets_and_custom(app):  # noqa: F811
    from ui.appearance import NAMES, PRESETS
    create_wallet(app)
    m = open_appearance(app)
    m.sel = 1
    assert _shown(m, "BOX COLOR") == "CLASSIC"
    press(app, "KEY_RIGHT")                                # EMERALD, applied straight away
    assert app.visible is m and m.sel == 1
    assert _shown(m, "BOX COLOR") == "EMERALD"
    assert t.PAL["win_top"] == dict(PRESETS)["EMERALD"]
    assert app.settings.box_color == list(dict(PRESETS)["EMERALD"])
    enter(app)                                             # OK on a preset: the next one
    assert _shown(m, "BOX COLOR") == "GRAPE"
    press(app, "KEY_LEFT")
    press(app, "KEY_LEFT")                                 # back to CLASSIC
    assert _shown(m, "BOX COLOR") == "CLASSIC" and app.settings.box_color is None
    assert t.PAL["win_top"] == t.DEFAULT_BOX
    press(app, "KEY_LEFT")                                 # wraps round to CUSTOM
    assert _shown(m, "BOX COLOR") == "CUSTOM" == NAMES[-1]
    for name in PRESETS:                                   # every preset keeps text readable
        r, g, b = name[1]
        assert 0.299 * r + 0.587 * g + 0.114 * b <= t.MAX_BOX_LUMA

    # OK on CUSTOM opens the sliders
    enter(app)
    assert isinstance(app.visible, ColorScreen)
    for _ in range(3):
        press(app, "KEY_RIGHT")                            # red +24, previewed live
    app.render(0)
    assert t.PAL["win_top"][0] == t.DEFAULT_BOX[0] + 24
    press(app, "KEY_BACKSPACE")                            # DEL: nothing kept
    assert app.visible is m and app.settings.box_custom is None
    assert t.PAL["win_top"] == t.DEFAULT_BOX

    enter(app)
    press(app, "KEY_DOWN")
    press(app, "KEY_DOWN")                                 # blue
    for _ in range(40):
        press(app, "KEY_LEFT")                             # 0
    press(app, "KEY_UP")
    press(app, "KEY_UP")                                   # red
    for _ in range(40):
        press(app, "KEY_RIGHT")                            # 255
    enter(app)                                             # OK: keep it
    assert app.visible is m and m.sel == 1 and _shown(m, "BOX COLOR") == "CUSTOM"
    assert app.settings.box_color == app.settings.box_custom == [255, 88, 0]

    # trying presets doesn't lose your mix: it comes back on CUSTOM
    press(app, "KEY_RIGHT")                                # -> CLASSIC
    assert _shown(m, "BOX COLOR") == "CLASSIC"
    press(app, "KEY_LEFT")                                 # -> CUSTOM again
    assert app.settings.box_color == [255, 88, 0]

    # ESC from the sliders puts the saved color back, not the preview
    enter(app)
    press(app, "KEY_DOWN")
    press(app, "KEY_RIGHT")
    app.render(0)
    press(app, "KEY_ESC")
    assert isinstance(app.visible, HomeScreen)
    saved = t.PAL["win_top"]
    t.set_box_color((255, 88, 0))
    assert saved == t.PAL["win_top"]


def test_signer_screens_keep_classic_blue(app):  # noqa: F811
    create_wallet(app)
    app.settings.box_color = [200, 0, 40]
    from ui.appearance import apply
    apply(app.settings)
    press(app, "KEY_L")                                    # lock: the signer asks for the PIN
    settle(app)
    assert app.console is not None and app.console.stack
    img = app.render(0)
    colors = {c for _, c in img.getcolors(1 << 16)}
    assert t.DEFAULT_BOX in colors and (200, 0, 40) not in colors
    assert t.PAL["win_top"] == (200, 0, 40)                # the app's color is untouched


def test_coins_say_pending(app):  # noqa: F811
    create_wallet(app)
    fund(app.wallet, 50_000)                               # fresh coins: not yet in a block
    app.refresh_balance()
    coins_screen(app)
    settle(app)
    tags = [i[2] for i in app.visible.items]
    assert len(tags) == 1 and tags[0].startswith("PENDING tb1q")


def _at(menu, label):
    return [i[0] for i in menu.items].index(label)


def test_setup_toggles_change_in_place(app):  # noqa: F811
    create_wallet(app)
    for label, attr in (("TOR", "use_tor"), ("QR FORMAT", "qr_format")):
        app.home()
        settings_screen(app, at=label)
        settle(app)
        menu = app.visible
        menu.sel = _at(menu, label)
        before = getattr(app.settings, attr)
        press(app, "KEY_RIGHT")
        assert app.visible is menu and menu.sel == _at(menu, label)   # cursor stays put
        assert getattr(app.settings, attr) != before
        press(app, "KEY_LEFT")
        assert getattr(app.settings, attr) == before
        enter(app)                                                  # OK flips it too
        assert getattr(app.settings, attr) != before and menu.sel == _at(menu, label)
        enter(app)
    # the value on screen follows, with arrows on the selected one
    menu.sel = _at(menu, "QR FORMAT")              # DISPLAY & SOUND
    shown = dict((i[0], i[2] if len(i) > 2 else '') for i in menu.items)["QR FORMAT"]
    assert shown == app.settings.qr_format.upper()
    app.render(0)
    # plain items ignore LEFT/RIGHT: only UP/DOWN move
    app.home()
    settings_screen(app, at="SERVER")
    menu = app.visible
    menu.sel = _at(menu, "SERVER")
    press(app, "KEY_RIGHT")
    assert menu.items[menu.sel][0] == "SERVER"
    press(app, "KEY_DOWN")
    assert menu.items[menu.sel][0] == "TOR"


def test_coming_back_from_server_keeps_the_cursor(app):  # noqa: F811
    from test_app import type_text
    create_wallet(app)
    settings_screen(app, at="SERVER")
    settle(app)
    app.visible.sel = _at(app.visible, "SERVER")
    enter(app)
    type_text(app, "example.org")
    enter(app)
    assert app.visible.title == "ELECTRUM"            # no port: not a server, stays put
    press(app, "KEY_RIGHTALT", "KEY_S")               # sym + S types ':'
    type_text(app, "50002")
    enter(app)
    menu = app.visible
    assert menu.title == "NETWORK" and menu.items[menu.sel][0] == "SERVER"
    assert dict((i[0], i[2] if len(i) > 2 else '') for i in menu.items)["SERVER"] == "example.org"


def test_welcome_network_left_right(app):  # noqa: F811
    app.start()
    menu = app.visible
    menu.sel = 2
    before = app.settings.network
    press(app, "KEY_RIGHT")
    after = app.settings.network
    assert after != before and app.visible is menu and menu.sel == 2
    assert dict((i[0], i[2] if len(i) > 2 else '') for i in menu.items)["NETWORK"] == after.upper()
    press(app, "KEY_LEFT")
    assert app.settings.network == before


def test_appearance_digits_in_place(app):  # noqa: F811
    create_wallet(app)
    m = open_appearance(app)
    press(app, "KEY_RIGHT")
    assert app.visible is m and m.sel == 0 and app.settings.amount_style == "split"
    assert _shown(m, "BALANCE DIGITS") == "SMALL SATS"


def test_clock_12h_setting(app):  # noqa: F811
    import time as _t
    from ui import theme as t
    from ui.flow import settings_screen
    assert t.clock_text(False, _t.struct_time((2026, 11, 18, 21, 56, 0, 2, 322, 0))) == "21:56"
    assert t.clock_text(True, _t.struct_time((2026, 11, 18, 21, 56, 0, 2, 322, 0))) == "9:56PM"
    assert t.clock_text(True, _t.struct_time((2026, 11, 18, 0, 5, 0, 2, 322, 0))) == "12:05AM"
    assert t.clock_text(True, _t.struct_time((2026, 11, 18, 12, 0, 0, 2, 322, 0))) == "12:00PM"
    create_wallet(app)
    settings_screen(app, at="CLOCK")
    settle(app)
    row = next(i for i in app.visible.items if i[0] == "CLOCK")
    assert row[2] == "12H"                         # 12-hour by default
    enter(app)                                     # flips it to 24-hour
    settle(app)
    row = next(i for i in app.visible.items if i[0] == "CLOCK")
    assert row[2] == "24H" and app.signer.status()["clock_12h"] is False
    assert app._clock_12h() is False


def test_clock_12h_reaches_sync_line(app, monkeypatch):  # noqa: F811
    import time as _t
    create_wallet(app)
    app.clock_12h = True
    class _W:  # noqa: N801
        last_sync = _t.mktime((2026, 11, 18, 22, 8, 0, 0, 0, -1)); tip_height = 976519
    monkeypatch.setattr(type(app), "online", property(lambda self: True))
    app.wallet, app.net_error, app.syncing = _W(), "", False
    assert app.net_line().startswith("SYNCED 10:08PM")


def test_volume_in_setup(app):  # noqa: F811
    """SETUP > DISPLAY & SOUND > VOLUME: RIGHT louder, LEFT quieter, saved."""
    from ui import sound
    from ui.flow import settings_screen
    from core.config import Settings
    try:
        sound.set_volume(3)
        create_wallet(app)
        settings_screen(app, at="VOLUME")
        settle(app)
        row = next(i for i in app.visible.items if i[0] == "VOLUME")
        assert row[2] == "3/10"
        press(app, "KEY_C")                           # RIGHT
        settle(app)
        assert sound.volume() == 4 and Settings.load().volume == 4
        row = next(i for i in app.visible.items if i[0] == "VOLUME")
        assert row[2] == "4/10"
        press(app, "KEY_Z"); press(app, "KEY_Z")           # LEFT twice
        settle(app)
        assert sound.volume() == 2
    finally:
        sound.set_volume(None)
