"""WIPE PIN, LOGIN COUNTDOWN and the SPENDING POLICY (0.8.20)."""
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("CZ_ARGON_T", "1")
os.environ.setdefault("CZ_ARGON_M_KIB", "1024")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from core import policy as pol  # noqa: E402
from core import signer as signer_mod  # noqa: E402
from core.signer import SignerError  # noqa: E402
from core.vault import Vault, VaultError, WipePinUsed, WrongPin  # noqa: E402
from test_core import ABANDON, DEST, NET, rig  # noqa: E402,F401
from test_app import app, create_wallet, enter, press, settle, type_text  # noqa: E402,F401

PIN = "2468"


_amounts = iter(range(60_000, 70_000, 7))


def send(signer, wallet, sats, code=None, dest=DEST):
    """A payment from a fresh coin each time (a real wallet moves on to new
    coins after broadcasting; the same coins again would be a fee bump)."""
    from test_core import fund
    amount = next(_amounts) + sats
    fund(wallet, amount)
    coin = next(c for c in wallet.coins() if c.sats == amount)
    b = wallet.build(dest, sats, fee_rate=5, coins=[coin.outpoint])
    s = signer.describe(b["psbt"])
    return s, (lambda: signer.sign(b["psbt"], s["digest"], code=code))


def now_code(secret, offset=0):
    return pol.totp(secret, int(time.time() // pol.STEP) + offset)


# ================================================================ wipe PIN
def test_wipe_pin_erases_everything_and_hides_itself(tmp_path):
    v = Vault(str(tmp_path / "v"))
    v.create("1111", ABANDON, NET)
    before = json.load(open(v.path))
    assert len(before["w"]) == len(json.load(open(v.path))["w"])      # always there
    v.set_wipe_pin("1111", "9999")
    after = json.load(open(v.path))
    assert set(before) == set(after) and len(before["w"]) == len(after["w"])
    assert v.unlock("1111").wipe_set
    with pytest.raises(WipePinUsed):
        v.unlock("9999")
    assert not v.exists() and not os.path.exists(v.tries_path)


def test_wipe_pin_must_be_its_own_pin(tmp_path):
    v = Vault(str(tmp_path / "v"))
    v.create("1111", ABANDON, NET)
    with pytest.raises(VaultError):
        v.set_wipe_pin("1111", "1111")
    v.set_duress("1111", "2222", ABANDON)
    with pytest.raises(VaultError, match="in use"):
        v.set_wipe_pin("1111", "2222")                                    # the duress PIN
    v.set_wipe_pin("1111", "3333")
    with pytest.raises(VaultError, match="in use"):
        v.change_pin("1111", "3333")
    with pytest.raises(VaultError, match="in use"):
        v.set_duress("1111", "3333", ABANDON)
    v.clear_wipe_pin("1111")
    assert not v.unlock("1111").wipe_set
    with pytest.raises(WrongPin):
        v.unlock("3333")                                                   # just a wrong PIN now
    assert v.exists()


def test_wipe_pin_through_the_screens_starts_like_new(app):  # noqa: F811
    from ui.flow import settings_screen
    from ui.title import TitleScreen
    create_wallet(app)
    app.settings.sound = False
    app.settings.save()
    settings_screen(app, at="WIPE PIN")
    settle(app)
    enter(app)                                       # WIPE PIN
    enter(app)                                       # SET ONE UP
    for pin in ("1234", "8888", "8888"):
        type_text(app, pin)
        enter(app)
    assert app.visible.title == "WIPE PIN ON"
    enter(app)
    assert app.signer.status()["wipe_pin"]
    app.lock(full=True)
    settle(app)
    type_text(app, "8888")
    enter(app)
    settle(app)
    assert isinstance(app.visible, TitleScreen)      # a first boot, nothing said
    assert not app.signer.status()["has_seed"]
    assert app.settings.sound is True                # settings back to new


# ================================================================ countdown
def test_countdown_holds_the_wallet_shut(rig, monkeypatch):  # noqa: F811
    signer, _ = rig
    with pytest.raises(SignerError):
        signer.set_countdown(PIN, 30)                 # under a minute
    signer.set_countdown(PIN, 300)
    signer.lock()
    clock = [1000.0]
    monkeypatch.setattr(signer_mod, "_boot_clock", lambda: clock[0])
    r = signer.unlock(PIN)
    assert r == {"countdown": 300} and not signer.status()["unlocked"]
    with pytest.raises(SignerError, match="left"):
        signer.finish_login()
    clock[0] += 299
    assert signer.login_wait() == 1
    clock[0] += 1
    assert signer.finish_login()["fingerprint"]
    assert signer.status()["unlocked"]
    signer.lock()
    signer.unlock(PIN)
    monkeypatch.setattr(signer_mod, "_boot_id", lambda: "rebooted")
    assert signer.login_wait() == -1                   # a reboot starts it over
    with pytest.raises(SignerError):
        signer.finish_login()


def test_the_app_cant_skip_the_countdown(app):  # noqa: F811
    from core.signer_service import RemoteError
    create_wallet(app)
    for method in ("finish_login", "set_countdown", "set_policy", "set_bypass", "set_totp",
                   "set_wipe_pin", "check_pin", "reveal_words"):
        with pytest.raises((RemoteError, AttributeError)):
            getattr(app.signer, method)()


def test_countdown_screen(app, monkeypatch):  # noqa: F811
    from ui.flow import HomeScreen
    from ui.security_flow import countdown_menu
    create_wallet(app)
    countdown_menu(app)
    enter(app)                                        # 5 MIN
    type_text(app, "1234")
    enter(app)
    settle(app)
    assert app.signer.status()["countdown_s"] == 300
    clock = [5000.0]
    monkeypatch.setattr(signer_mod, "_boot_clock", lambda: clock[0])
    app.lock(full=True)
    settle(app)
    type_text(app, "1234")
    enter(app)
    settle(app)
    assert app.console.top.title == "LOGIN COUNTDOWN"
    for k in ("KEY_ENTER", "KEY_X", "KEY_SPACE"):     # no key skips it
        press(app, k)
    assert app.console.top.title == "LOGIN COUNTDOWN"
    clock[0] += 300
    app.render(0)
    settle(app)
    assert isinstance(app.visible, HomeScreen)


# ================================================================ policy
def test_per_payment_and_per_day(rig):  # noqa: F811
    signer, wallet = rig
    signer.set_policy(PIN, "per_tx", 20_000)
    s, _ = send(signer, wallet, 25_000)
    assert "PER-PAYMENT" in s["blocked"] and not s["cap_ok"]
    s, sign = send(signer, wallet, 15_000)
    assert not s["blocked"]
    signer.set_policy(PIN, "per_day", 30_000)
    s, sign = send(signer, wallet, 15_000)
    sign()
    s, sign = send(signer, wallet, 15_000)
    assert "TODAY" in s["blocked"]
    with pytest.raises(SignerError, match="spending policy"):
        sign()


def test_cooldown(rig, monkeypatch):  # noqa: F811
    signer, wallet = rig
    signer.set_policy(PIN, "cooldown_s", 3600)
    s, sign = send(signer, wallet, 5_000)
    sign()
    s, _ = send(signer, wallet, 5_000)
    assert "COOLDOWN" in s["blocked"]
    later = time.time() + 3601
    monkeypatch.setattr(signer_mod.time, "time", lambda: later)
    s, _ = send(signer, wallet, 5_000)
    assert not s["blocked"]


def test_allow_list(rig):  # noqa: F811
    signer, wallet = rig
    with pytest.raises(SignerError, match="add an address"):
        signer.set_policy(PIN, "allow_on", True)
    with pytest.raises(SignerError, match="network"):
        signer.set_policy(PIN, "allow_add", "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4")  # mainnet
    other = "tb1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3q0sl5k7"
    signer.set_policy(PIN, "allow_add", other)
    signer.set_policy(PIN, "allow_on", True)
    s, _ = send(signer, wallet, 5_000)
    assert "ALLOW-LIST" in s["blocked"]
    signer.set_policy(PIN, "allow_add", DEST)
    s, sign = send(signer, wallet, 5_000)
    assert not s["blocked"] and sign()
    assert pol.public(pol.load(signer.policy_path))["allow"] == [other, DEST]


def test_max_fee(rig):  # noqa: F811
    signer, wallet = rig
    signer.set_policy(PIN, "max_fee", 3)
    s, _ = send(signer, wallet, 5_000)
    assert "FEE" in s["blocked"]


def test_phone_2fa(rig, monkeypatch):  # noqa: F811
    signer, wallet = rig
    secret = pol.new_secret()
    with pytest.raises(SignerError, match="wrong 2FA"):
        signer.set_totp(PIN, secret, "000000")
    signer.set_totp(PIN, secret, now_code(secret))
    assert "secret" not in json.dumps(signer.policy_info()) and secret not in json.dumps(
        signer.policy_info())
    s, sign = send(signer, wallet, 5_000)
    assert s["needs_code"]
    with pytest.raises(SignerError, match="code needed"):
        sign()
    s, sign = send(signer, wallet, 5_000, code="123456")
    with pytest.raises(SignerError, match="wrong 2FA"):
        sign()
    future = now_code(secret, 1)
    s, sign = send(signer, wallet, 5_000, code=future)
    assert sign()
    s, sign = send(signer, wallet, 5_000, code=future)
    with pytest.raises(SignerError, match="wrong 2FA"):
        sign()                                        # a used code can't be replayed
    signer.set_policy(PIN, "totp_above", 50_000)
    s, sign = send(signer, wallet, 5_000)
    assert not s["needs_code"] and sign()             # small payment: no code
    monkeypatch.setattr(pol.time, "time", lambda: pol.CLOCK_FLOOR - 100)
    with pytest.raises(pol.PolicyError, match="clock"):
        pol.verify_code(secret, now_code(secret), 0)


def test_bypass_pin_locks_the_policy_and_the_seed(rig):  # noqa: F811
    signer, wallet = rig
    signer.set_policy(PIN, "per_tx", 10_000)
    signer.set_bypass(PIN, "7777")
    info = signer.policy_info()
    assert info["locked"] and "hash" not in json.dumps(info)
    with pytest.raises(WrongPin):
        signer.set_policy(PIN, "per_tx", 0)           # PIN alone isn't enough
    with pytest.raises(WrongPin):
        signer.set_policy(PIN, "per_tx", 0, bypass_pin="1111")
    assert signer.status()["tries_left"] == 8         # wrong bypass PINs count
    signer.set_policy(PIN, "per_tx", 0, bypass_pin="7777")
    for blocked in (lambda: signer.reveal_words(PIN), lambda: signer.child_seed(PIN, 12, 0),
                    lambda: signer.set_cap(PIN, 1_000_000)):
        with pytest.raises(SignerError, match="locked"):
            blocked()
    signer.set_cap(PIN, 0)                            # tightening is fine
    signer.check_pin(PIN)
    with pytest.raises(SignerError, match="differ"):
        signer.set_bypass(PIN, PIN, bypass_pin="7777")
    signer.set_bypass(PIN, "6666", bypass_pin="7777")
    signer.clear_bypass(PIN, "6666")
    assert not signer.policy_info()["locked"]
    assert signer.reveal_words(PIN)


def test_wrong_bypass_pins_wipe_like_wrong_pins(rig):  # noqa: F811
    from core.vault import Wiped
    signer, _ = rig
    signer.set_bypass(PIN, "7777")
    with pytest.raises(Wiped):
        for _ in range(20):
            try:
                signer.set_policy(PIN, "per_tx", 0, bypass_pin="0000")
            except WrongPin:
                pass
    assert not signer.vault.exists() and not os.path.exists(signer.policy_path)


def test_policy_menu_renders_and_changes_through_the_signer(app):  # noqa: F811
    from ui.security_flow import policy_menu
    create_wallet(app)
    policy_menu(app)
    settle(app)
    m = app.visible
    assert [i[0] for i in m.items][:3] == ["PER PAYMENT", "PER DAY", "COOLDOWN"]
    app.render(0)
    enter(app)                                         # PER PAYMENT
    type_text(app, "50000")
    enter(app)
    settle(app)
    assert app.console.top.title == "SPENDING POLICY"  # the signer asks
    type_text(app, "1234")
    enter(app)
    settle(app)
    assert app.signer.policy_info()["per_tx"] == 50_000
    vals = {i[0]: i[2] for i in app.visible.items}
    assert vals["PER PAYMENT"] == "50,000"


def test_after_a_wipe_the_welcome_screen_still_works(app, monkeypatch):  # noqa: F811
    monkeypatch.setenv("CZ_SIM", "1")
    create_wallet(app)
    app.signer.local.wipe_all()
    app.factory_reset(title=False)
    assert app.settings.network == "signet"
    menu = app.visible
    menu.sel = [i[0] for i in menu.items].index("NETWORK")
    press(app, "KEY_RIGHT")                                  # used to crash on 'bitcoin'
    app.settings.network = "bitcoin"                          # even an odd saved value
    press(app, "KEY_RIGHT")
    assert app.settings.network != "bitcoin"


def test_fee_bump_isnt_counted_twice_or_held_by_cooldown(rig):  # noqa: F811
    signer, wallet = rig
    signer.set_policy(PIN, "per_day", 20_000)
    signer.set_policy(PIN, "cooldown_s", 3600)
    b1 = wallet.build(DEST, 15_000, fee_rate=5)
    s1 = signer.describe(b1["psbt"])
    signer.sign(b1["psbt"], s1["digest"])
    b2 = wallet.build(DEST, 15_000, fee_rate=20)              # same coins, higher fee
    s2 = signer.describe(b2["psbt"])
    assert s2["replaces_signed"] and not s2["blocked"], s2["blocked"]
    signer.sign(b2["psbt"], s2["digest"])
    s3, _ = send(signer, wallet, 1_000)                        # new coins: new money
    assert "COOLDOWN" in s3["blocked"] or "TODAY" in s3["blocked"]
    signer.set_policy(PIN, "per_day", 0)
    s3, _ = send(signer, wallet, 1_000)
    assert "COOLDOWN" in s3["blocked"]


def test_a_damaged_policy_fails_closed(rig):  # noqa: F811
    signer, wallet = rig
    signer.set_bypass(PIN, "7777")
    with open(signer.policy_path, "w") as f:
        f.write("{not json")
    assert signer.policy_info()["locked"] and signer.policy_info()["damaged"]
    s, _ = send(signer, wallet, 1_000)
    assert "DAMAGED" in s["blocked"]
    with pytest.raises(SignerError, match="locked"):
        signer.reveal_words(PIN)
    with pytest.raises(SignerError, match="damaged"):
        signer.set_policy(PIN, "per_tx", 0, bypass_pin="7777")


def test_bypass_pin_cant_double_as_another_pin(rig):  # noqa: F811
    signer, _ = rig
    signer.set_bypass(PIN, "7777")
    with pytest.raises(SignerError, match="in use"):
        signer.set_wipe_pin(PIN, "7777")
    with pytest.raises(SignerError, match="in use"):
        signer.change_pin(PIN, "7777")
