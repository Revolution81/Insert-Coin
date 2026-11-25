"""SETUP › ADVANCED: power-user tools hidden by default, shown when on, and
anything in use shown either way."""
from test_app import app, create_wallet, enter, settle  # noqa: F401
from ui.flow import ADVANCED_ITEMS, SETUP_GROUPS, settings_screen


def _labels(menu):
    return [i[0] for i in menu.items]


def _group(app, name):
    app.home()
    settings_screen(app)
    settle(app)
    m = app.visible
    m.sel = _labels(m).index(name)
    enter(app)
    settle(app)
    return _labels(app.visible)


def _everything(app):
    return {lb for g, _ in SETUP_GROUPS for lb in _group(app, g)}


def test_off_by_default_hides_the_tools(app):  # noqa: F811
    create_wallet(app)
    app.settings.advanced = False
    shown = _everything(app)
    assert not shown & ADVANCED_ITEMS
    assert {"PINS", "SHOW SEED WORDS", "WI-FI", "WALLETS", "HARDWARE"} <= shown


def test_on_shows_them_and_the_switch_toggles(app):  # noqa: F811
    create_wallet(app)
    app.settings.advanced = False
    app.home()
    settings_screen(app)
    settle(app)
    m = app.visible
    m.sel = _labels(m).index("ADVANCED")
    assert m.items[m.sel][2] == "OFF"
    enter(app)
    assert app.settings.advanced is True and app.visible.items[m.sel][2] == "ON"
    assert ADVANCED_ITEMS <= _everything(app)


def test_in_use_stays_visible(app):  # noqa: F811
    create_wallet(app)
    app.settings.advanced = False
    app.settings.qr_format = "bbqr"
    assert "QR FORMAT" in _group(app, "DISPLAY & SOUND")
    assert "TEMPORARY SEED" not in _group(app, "TOOLS")


def test_new_multisig_is_segwit_only_when_off(app):  # noqa: F811
    from ui.multisig_flow import new_multisig
    create_wallet(app)
    app.settings.advanced = False
    new_multisig(app)
    assert app.visible.title == "SEGWIT"               # straight to 2 OF 3 / 2 OF 2 / 3 OF 5
    app.home()
    app.settings.advanced = True
    new_multisig(app)
    assert app.visible.title == "NEW MULTISIG"
