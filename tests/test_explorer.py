"""ADDRESS EXPLORER: lists, used/balance marks, labels, signer verification."""
from test_app import app, create_wallet, enter, press, settle  # noqa: F401
from test_core import fund

from ui.explorer_flow import AddressDetailScreen, address_list, explorer_menu
from ui.secure_flows import AddressScreen


def test_addresses_list_used_and_balance(app):  # noqa: F811
    create_wallet(app)
    w = app.wallet
    rows = w.addresses(0)
    assert len(rows) == 1 + app.settings.stop_gap                 # index 0 + gap
    assert [r["index"] for r in rows[:3]] == [0, 1, 2]
    assert not any(r["used"] for r in rows)
    addr = fund(w, 12_345)
    rows = w.addresses(0)
    first = next(r for r in rows if r["address"] == addr)
    assert first["used"] and first["sats"] == 12_345
    assert len(rows) == first["index"] + 1 + app.settings.stop_gap
    change = w.addresses(1)
    assert change[0]["address"] != rows[0]["address"]
    more = w.addresses(0, start=len(rows), count=20)
    assert more[0]["index"] == len(rows) and len(more) == 20


def test_explorer_screens_and_signer_check(app):  # noqa: F811
    create_wallet(app)
    explorer_menu(app)
    enter(app)                                       # RECEIVE ADDRESSES
    menu = app.visible
    n = len(menu.items)
    assert menu.items[-1][0] == "MORE ADDRESSES"
    menu.sel = n - 1
    enter(app)                                       # +20
    assert len(app.visible.items) == n + 20
    app.visible.sel = 3
    enter(app)
    assert isinstance(app.visible, AddressDetailScreen) and app.visible.index == 3
    shown = app.visible.addr
    assert shown == app.wallet.addresses(0)[3]["address"]
    press(app, "KEY_V")                              # Sir Signer derives it himself
    settle(app)
    assert isinstance(app.visible, AddressScreen) and app.visible.addr == shown
    enter(app)
    settle(app)
    assert isinstance(app.visible, AddressDetailScreen)    # matched: no MISMATCH


def test_change_addresses_verify_on_the_signer(app):  # noqa: F811
    create_wallet(app)
    address_list(app, 1)
    app.visible.sel = 2
    enter(app)
    d = app.visible
    assert d.branch == 1 and d.index == 2
    press(app, "KEY_V")
    settle(app)
    assert isinstance(app.visible, AddressScreen) and app.visible.addr == d.addr
    assert app.visible.label == "CHANGE #2"


def test_label_from_the_explorer(app):  # noqa: F811
    from test_app import type_text
    create_wallet(app)
    address_list(app, 0)
    app.visible.sel = 1
    enter(app)
    addr = app.visible.addr
    press(app, "KEY_L")
    type_text(app, "Donations")
    enter(app)
    assert app.wallet.label("addr", addr) == "Donations"
