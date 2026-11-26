"""
Secure chip and fingerprint add-ons, against the protocol-level simulators.
"""
import pytest

import core.hw.atecc as atecc
import core.hw.finger as finger
from core.hw.atecc import ChipError, SimulatedChip, crc16, packet, parse_reply
from core.signer import Signer
from core.signer_service import RemoteError
from core.vault import ChipMissing, WrongPin
from ui.flow import HomeScreen
from ui.hardware_flow import EnrollScreen, chip_state, finger_state, status_icons
from ui.widgets import MenuScreen, PinScreen

from test_app import app, create_wallet, enter, press, settle, type_text  # noqa: F401
from ui.flow import LockedScreen
from core.signer import SignerError
from test_core import ABANDON


@pytest.fixture(autouse=True)
def fresh_sims(monkeypatch):
    atecc._SIMS.clear()
    atecc._OPEN.clear()
    finger._SIM = None
    yield


# ------------------------------------------------------------ chip protocol
def test_packet_and_crc():
    assert packet(0x30, 0, 0).hex() == "030730000000035d"     # Info, from the datasheet
    good = bytes([4, 0x00]) + crc16(bytes([4, 0x00]))
    assert parse_reply(good) == b"\x00"
    with pytest.raises(ChipError, match="CRC"):
        parse_reply(good[:-1] + b"\x00")
    bad = bytes([4, 0x0F]) + crc16(bytes([4, 0x0F]))
    with pytest.raises(ChipError, match="execution"):
        parse_reply(bad)


def test_detects_trustgo_and_blank(monkeypatch):
    monkeypatch.setenv("CZ_SE", "sim")
    _, st = atecc.open_chip()
    assert st["state"] == "ready" and st["slot"] == 0 and st["address"] == 0x35
    monkeypatch.setenv("CZ_SE", "sim-blank")
    _, st = atecc.open_chip()
    assert st["state"] == "blank"


# --------------------------------------------------------------- vault + chip
def test_vault_bound_to_chip(tmp_path, monkeypatch):
    monkeypatch.setenv("CZ_SE", "sim")
    s = Signer(str(tmp_path))
    s.setup("1111", ABANDON, "signet")
    s.set_duress("1111", "2222")
    st = s.bind_chip("1111")
    assert st["in_use"] and s.vault.info()["secure_chip"]
    sim = atecc._SIMS["tng"]
    before = sim.calls
    s.lock()
    s.unlock("1111")
    assert sim.calls - before <= 3                 # one ECDH per unlock (+ cheap Info checks)
    with pytest.raises(WrongPin):
        s.unlock("2222")                           # duress was cleared by binding

    monkeypatch.setenv("CZ_SE", "none")            # chip unplugged
    tries = s.vault.info()["tries_left"]
    with pytest.raises(ChipMissing):
        s.unlock("1111")
    assert s.vault.info()["tries_left"] == tries   # a missing chip doesn't cost a try

    monkeypatch.setenv("CZ_SE", "sim")
    atecc._SIMS["tng"] = SimulatedChip(seed=b"another chip".ljust(32, b"\0"))
    with pytest.raises(ChipMissing, match="different"):
        s.unlock("1111")


def test_copied_vault_useless_without_the_chip(tmp_path, monkeypatch):
    """The vault file plus the PIN aren't enough: Argon2 alone gives the wrong key."""
    monkeypatch.setenv("CZ_SE", "sim")
    s = Signer(str(tmp_path))
    s.setup("1111", ABANDON, "signet")
    s.bind_chip("1111")
    import json
    body = json.load(open(s.vault.path))
    from core.vault import Vault
    plain = Vault(str(tmp_path))                    # attacker's copy: no chip factory
    body_no_se = dict(body)
    body_no_se.pop("se")
    key = Vault._key(plain, "1111", body_no_se)     # Argon2-only key
    assert plain._try_key(key, body) is None


def test_unbind(tmp_path, monkeypatch):
    monkeypatch.setenv("CZ_SE", "sim")
    s = Signer(str(tmp_path))
    s.setup("1111", ABANDON, "signet")
    s.bind_chip("1111")
    s.unbind_chip("1111")
    monkeypatch.setenv("CZ_SE", "none")
    s.lock()
    assert s.unlock("1111")["fingerprint"] == "73c5da0a"


# ---------------------------------------------------------------- fingerprint
def test_finger_frame_checksum():
    assert finger.frame(b"\x35").hex() == "ef01ffffffff010003350039"


def enroll(app, who="me"):
    sim = finger.simulator()
    scr = EnrollScreen()
    app.push(scr)
    for _ in range(EnrollScreen.PRESSES):
        sim.finger = who
        app.pump()
        sim.finger = None
        app.pump()


def test_enroll_quick_unlock_and_soft_lock(app, monkeypatch):  # noqa: F811
    monkeypatch.setenv("CZ_FINGER", "sim")
    monkeypatch.setenv("CZ_SE", "sim")
    app.refresh_hardware(look_i2c=True)
    create_wallet(app, pin="1234")
    assert finger_state(app) == "ready" and chip_state(app) == "ready"

    enroll(app)
    assert app.visible.title == "FINGER SAVED" and app.hw["finger"]["saved"] == 1
    enter(app)

    # SETUP > HARDWARE > FINGERPRINT > QUICK UNLOCK > 4 HOURS
    from ui.hardware_flow import finger_menu
    finger_menu(app)
    app.visible.sel = 1
    enter(app)
    app.visible.sel = 1
    enter(app)
    type_text(app, "1234")
    enter(app)
    assert app.settings.finger_unlock and finger_state(app) == "on"
    assert app.signer.status()["quick_hours"] == 4
    app.home()
    assert [c for _, c in status_icons(app)] == ["grey", "cyan"]    # chip ready (unused), finger on

    press(app, "KEY_L")                                   # soft lock: no PIN prompt yet
    assert isinstance(app.visible, LockedScreen) and app.visible.prompt == "FINGER, OR OK FOR PIN"
    app.render(0)
    sim = finger.simulator()
    sim.finger = "stranger"
    app.pump()
    assert isinstance(app.visible, LockedScreen)              # wrong finger: stays locked
    sim.finger = "me"
    app.pump()
    sim.finger = None
    assert isinstance(app.visible, HomeScreen)

    app.lock(full=True)  # full lock: the seed is gone

    settle(app)
    assert app.visible.prompt == "ENTER PIN"
    sim.finger = "me"
    app.pump()
    assert isinstance(app.visible, PinScreen)
    sim.finger = None
    type_text(app, "1234")
    enter(app)
    assert isinstance(app.visible, HomeScreen)


def test_hardware_menu_and_chip_binding_ui(app, monkeypatch):  # noqa: F811
    monkeypatch.setenv("CZ_SE", "sim")
    app.refresh_hardware(look_i2c=True)
    create_wallet(app, pin="1234")
    from ui.hardware_flow import hardware_menu
    hardware_menu(app)
    rows = {it[0]: it[2] for it in app.visible.items if len(it) > 2}
    assert rows["M5 SECURE CHIP"] == "GROVE" and rows["SECURE CHIP"] == "NONE"
    assert rows["FINGERPRINT"] == "NONE" and rows["NFC"] == "NONE"
    assert app.hw["grove"] == "chip"                      # side port left in I2C for it
    app.render(0)
    enter(app)                                            # M5 SECURE CHIP
    enter(app)                                            # USE IT
    press(app, "KEY_Z")
    enter(app)                                            # YES
    type_text(app, "1234")
    enter(app)
    assert app.visible.title == "CHIP IN USE"
    assert chip_state(app) == "in_use"
    assert [c for _, c in status_icons(app)] == ["gold"]
    enter(app)
    assert [it for it in app.visible.items if it[0] == "M5 SECURE CHIP"][0][3] == "gold"
    monkeypatch.setenv("CZ_SE", "none")
    app.refresh_hardware()
    assert chip_state(app) == "missing" and [c for _, c in status_icons(app)] == ["red"]
    from ui.hardware_flow import hardware_menu as hm
    hm(app)
    assert [it for it in app.visible.items if it[0] == "M5 SECURE CHIP"][0][2] == "MISSING"
    app.pop()
    app.lock(full=True)
    settle(app)
    type_text(app, "1234")
    enter(app)
    assert isinstance(app.visible, PinScreen) and "chip" in app.visible.error


def test_blank_chip_shows_info(app, monkeypatch):  # noqa: F811
    monkeypatch.setenv("CZ_SE", "sim-blank")
    app.refresh_hardware()
    create_wallet(app)
    from ui.hardware_flow import chip_menu, hardware_menu
    hardware_menu(app)
    rows = {it[0]: it[2] for it in app.visible.items if len(it) > 2}
    assert rows["SECURE CHIP"] == "GPIO" and rows["M5 SECURE CHIP"] == "NONE"
    chip_menu(app, m5=False)
    assert isinstance(app.visible, MenuScreen) and app.visible.items[0][0] == "ABOUT BLANK CHIPS"
    with pytest.raises(SignerError):
        app.signer.local.bind_chip(pin="1234")
