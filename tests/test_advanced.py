"""SETUP › ADVANCED: power-user tools hidden by default, shown when on, and
anything in use shown either way."""
from test_app import app, create_wallet, enter, settle  # noqa: F401
from ui.flow import ADVANCED_ITEMS, SETUP_GROUPS, SETUP_SUBGROUPS, settings_screen


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


def _sub(app, group, sub):
    _group(app, group)
    m = app.visible
    m.sel = _labels(m).index(sub)
    enter(app)
    settle(app)
    return _labels(app.visible)


def _everything(app):
    shown = set()
    for g, _ in SETUP_GROUPS:
        labels = _group(app, g)
        shown |= set(labels)
        for sub in SETUP_SUBGROUPS:
            if sub in labels:
                shown |= set(_sub(app, g, sub))
    return shown


def test_off_by_default_hides_the_tools(app):  # noqa: F811
    create_wallet(app)
    app.settings.advanced = False
    shown = _everything(app)
    assert not shown & ADVANCED_ITEMS
    assert {"PINS", "GAME PIN", "ONLINE CAP", "SHOW SEED WORDS", "WI-FI", "HARDWARE"} <= shown


def test_on_shows_them_and_the_switch_toggles(app):  # noqa: F811
    create_wallet(app)
    app.settings.advanced = False
    labels = _group(app, "DEVICE")
    m = app.visible
    m.sel = labels.index("ADVANCED")
    assert m.items[m.sel][2] == "OFF"
    enter(app)
    assert app.settings.advanced is True
    assert m.items[m.sel][0] == "ADVANCED" and m.items[m.sel][2] == "ON"   # cursor stays on it
    assert ADVANCED_ITEMS <= _everything(app)


def test_in_use_stays_visible(app):  # noqa: F811
    create_wallet(app)
    app.settings.advanced = False
    app.settings.qr_format = "bbqr"
    assert "QR FORMAT" in _group(app, "DEVICE")
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
