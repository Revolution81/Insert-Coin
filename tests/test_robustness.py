"""
Regressions from the bug hunt (tools/fuzz.py, tools/monkey.py): hostile or
damaged input must give a clean error, never crash the app.
"""
import os

import pytest

from core import cktap, qr, ur
from core.qr import Collector

from test_app import app, create_wallet, enter  # noqa: F401 - fixture


def collect(*frames):
    c = Collector()
    for f in frames:
        assert c.add(f) in (True, False)          # never raises
    return c


@pytest.mark.parametrize("text", [
    "cHNidP8BAHééé",                      # base64 PSBT with non-ASCII
    "cHNidP8BAH!!",                                    # base64 PSBT with junk
    "70736274ff01zz",                                  # hex PSBT with non-hex
    "70736274ff0",                                     # odd-length hex
])
def test_damaged_psbt_qr_is_a_clean_error(text):
    with pytest.raises(qr.QRFormatError):
        qr.classify(text)
    c = collect(text)
    assert c.result is None and c.error


def test_bbqr_negative_index_is_ignored():
    assert qr.bbqr_header("B$ZP03-2QIQWWOAA") is None
    frames = qr.bbqr_encode(b"x" * 300, "P", 40)
    c = collect(frames[0], "B$ZP" + frames[0][4:6] + "-2AAAA", *frames[1:])
    assert c.result is not None and c.result.data == b"x" * 300


def test_lightning_inside_animated_codes_is_a_clean_error():
    c = collect("B$2U0100" + qr._b32encode(b"lightning:lnbc1xyz"))
    assert c.result is None and "Lightning" in c.error
    c = collect("ur:bytes/" + ur.bytewords_encode(ur.cbor_encode(b"lnbc1xyz")))
    assert c.result is None and "Lightning" in c.error


@pytest.mark.parametrize("part", [
    [1, 3, 100, 12345, 2 ** 62],                # fragment is a giant number, not bytes
    [1, 2 ** 40, 100, 12345, b"abcdefghij"],    # absurd part count
    [1, 3, 2 ** 60, 12345, b"abcdefghij"],      # message length can't fit the parts
    [1, 3, 100, b"x", b"abcdefghij"],           # checksum isn't a number
])
def test_hostile_ur_parts_are_refused(part):
    seq = f"{part[0]}-{part[1]}"
    text = f"ur:crypto-psbt/{seq}/" + ur.bytewords_encode(ur.cbor_encode(part))
    with pytest.raises(ur.URError):
        ur.URDecoder().add(text)
    assert collect(text).result is None


def test_deeply_nested_ur_cbor_is_refused():
    with pytest.raises(ur.URError):
        ur.cbor_decode(b"\xc6" * 500 + b"\x01")
    with pytest.raises(ur.URError):
        ur.cbor_decode(b"\x81" * 500 + b"\x01")


def test_garbled_card_slots_are_a_card_error():
    for slots in ([0, 10, 3], "x", [0, -1], [b"", 1]):
        card = cktap.Card(lambda apdu: (b"", 0x9000))
        card.status = {"slots": slots}
        with pytest.raises(cktap.CardError):
            card.slot()


# ------------------------------------------------------------- crash net
def test_a_screen_bug_sends_you_home_not_down(app, tmp_path):  # noqa: F811
    import core.config
    from ui.flow import HomeScreen
    from ui.widgets import MessageScreen, Screen
    create_wallet(app)

    class Boom(Screen):
        def handle(self, ev, app):
            raise ZeroDivisionError("a bug in some screen")

    app.push(Boom())
    app.safely(app.feed, "KEY_ENTER", 1)
    assert isinstance(app.visible, MessageScreen) and app.visible.title == "OOPS"
    assert isinstance(app.stack[0], HomeScreen) and len(app.stack) == 2
    log = os.path.join(core.config.APP_DIR, "crash.log")
    assert "ZeroDivisionError" in open(log, encoding="utf-8").read()
    enter(app)
    assert isinstance(app.visible, HomeScreen)
    app.render(0)

    # a bug that keeps happening still restarts the app
    with pytest.raises(RuntimeError):
        for _ in range(5):
            app.push(Boom())
            app.safely(app.feed, "KEY_ENTER", 1)


def test_amounts_are_exact():
    from ui.flow import parse_amount
    assert parse_amount("0.29", "BTC") == 29_000_000
    assert parse_amount("0.00000001", "BTC") == 1
    assert parse_amount(".5", "BTC") == 50_000_000
    assert parse_amount("21000000", "BTC") == 2_100_000_000_000_000
    assert parse_amount("12345", "SATS") == 12345
    for bad, unit in (("0.000000015", "BTC"), ("1.2.3", "BTC"), (".", "BTC"), ("", "BTC"),
                      ("1.5", "SATS"), ("", "SATS")):
        assert parse_amount(bad, unit) is None


def test_menu_survives_its_list_shrinking():
    from ui.input import Event, Kind
    from ui.widgets import MenuScreen
    picked = []
    m = MenuScreen("X", [("A", lambda app: picked.append("A")), ("B", lambda app: picked.append("B"))])
    m.sel = 1
    m.items = m.items[:1]
    m.handle(Event(Kind.SELECT, None), None)
    assert picked == ["A"]
    m.items = []
    m.handle(Event(Kind.SELECT, None), None)
    m.handle(Event(Kind.NAV, "DOWN"), None)


@pytest.mark.parametrize("addr, network, expect", [
    ("0x742d35Cc6634C0532925a3b844Bc454e4438f44e", "bitcoin", "Ethereum"),
    ("ltc1qg82dgaq3pdu6ruaxdrkzqc8yzh3m4ckjajt3vq", "bitcoin", "Litecoin"),
    ("LVuDpNCSSj6pQ7t9Pv6d6sUkLKoqDEVUnJ", "bitcoin", "Litecoin"),
    ("bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a", "bitcoin", "Bitcoin Cash"),
    ("qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a", "bitcoin", "Bitcoin Cash"),
    ("DH5yaieqoZN36fDVciNyRueRGvGLR3mr7L", "bitcoin", "Dogecoin"),
    ("T9yED5xMV5ARV98BexN97aLZ1UUq7eKSxm", "bitcoin", "Tron"),
    ("addr1qx2fxv2umyhttkxyxp8x0dlpdt3k6cwng5pxj3jhsydzer3n0d3vllmyqwsx5wktcd8cc3sq835lu7drv2xwl2wywfgse35a3x",
     "bitcoin", "Cardano"),
    ("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4", "signet", "Mainnet address"),
    ("1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2", "signet", "Mainnet address"),
    ("tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx", "bitcoin", "Test-network"),
    ("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t5", "bitcoin", "Typo"),
    ("1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN3", "bitcoin", "Typo"),
    ("hello", "bitcoin", "Not a bitcoin address"),
])
def test_wrong_addresses_say_why(addr, network, expect):
    from core.wallet import why_not_bitcoin
    msg = why_not_bitcoin(addr, network)
    assert expect in msg and len(msg) <= 40, msg
