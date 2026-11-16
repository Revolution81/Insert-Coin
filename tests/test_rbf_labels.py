"""
Fee bumping / cancelling (RBF) and labels, in the wallet and through the UI.
"""
import json
import math
import os

import pytest
from embit.script import address_to_scriptpubkey
from embit.transaction import Transaction

from core.wallet import WalletError, vsize
from ui.flow import ConfirmSendScreen, HomeScreen
from ui.qrscreens import AnimatedQRScreen
from ui.widgets import MenuScreen, TextInputScreen

from test_app import app, create_wallet, enter, press, type_text  # noqa: F401
from test_core import DEST, assert_valid_signatures, fake_tx, fund, rig  # noqa: F401
from test_qr import pump_until, use_camera


def send(signer, wallet, sats, rate, coins=None):
    built = wallet.build(DEST, sats, rate, coins)
    s = signer.describe(built["psbt"])
    signed = signer.sign(built["psbt"], s["digest"])
    tx = wallet.finalize(signed)
    wallet.record_unconfirmed(tx)
    return tx, built


def sign_replacement(signer, wallet, built):
    s = signer.describe(built["psbt"])
    signed = signer.sign(built["psbt"], s["digest"])
    tx = wallet.finalize(signed)
    assert_valid_signatures(signed, Transaction.parse(tx.raw))
    return tx, s


# --------------------------------------------------------------- wallet
def test_bump_keeps_payment_and_pays_more(rig):  # noqa: F811
    signer, wallet = rig
    old, built = send(signer, wallet, 30_000, 2)
    info = wallet.replaceable(old.txid)
    assert info and info["fee"] == built["fee"]
    bump = wallet.bump(old.txid, 10)
    new, s = sign_replacement(signer, wallet, bump)
    parsed = Transaction.parse(new.raw)
    dest = address_to_scriptpubkey(DEST)
    assert [o.value for o in parsed.vout if o.script_pubkey == dest] == [30_000]
    vs = vsize(len(parsed.vin), len(parsed.vout))
    assert bump["fee"] >= built["fee"] + math.ceil(vs)          # BIP125 rule 4
    assert bump["fee"] >= 10 * vs - 1
    # same coins as the original, so the two conflict
    assert {(i.txid, i.vout) for i in parsed.vin} >= \
        {(i.txid, i.vout) for i in Transaction.parse(old.raw).vin}
    wallet.record_unconfirmed(new, replaces=old.txid)
    assert old.txid not in wallet.state["txs"]
    assert wallet.balance()["total"] == 100_000 - 30_000 - bump["fee"]


def test_cancel_sends_everything_back(rig):  # noqa: F811
    signer, wallet = rig
    old, _ = send(signer, wallet, 30_000, 2)
    c = wallet.bump(old.txid, 8, cancel=True)
    new, s = sign_replacement(signer, wallet, c)
    assert s["spend"] == 0 and [o["kind"] for o in s["outputs"]] == ["change"]
    wallet.record_unconfirmed(new, replaces=old.txid)
    assert wallet.balance()["total"] == 100_000 - c["fee"]


def test_bump_adds_a_coin_when_needed(rig):  # noqa: F811
    signer, wallet = rig                     # one 100k coin from the fixture
    fund(wallet, 3_000)
    small = next(c for c in wallet.coins() if c.sats == 3_000)
    old, _ = send(signer, wallet, 2_700, 1, coins=[small.outpoint])
    assert len(Transaction.parse(old.raw).vout) == 1          # no room for change
    bump = wallet.bump(old.txid, 30)
    new, _ = sign_replacement(signer, wallet, bump)
    assert len(Transaction.parse(new.raw).vin) == 2


def test_replace_rules(rig):  # noqa: F811
    signer, wallet = rig
    incoming = wallet.history()[0].txid
    assert wallet.replaceable(incoming) is None              # not our inputs
    old, _ = send(signer, wallet, 30_000, 5)
    with pytest.raises(WalletError, match="beat"):
        wallet.bump(old.txid, 5)
    # once something spends its change, it can't be replaced
    send(signer, wallet, 10_000, 5)
    assert wallet.replaceable(old.txid) is None


# --------------------------------------------------------------- labels
def test_labels(rig):  # noqa: F811
    signer, wallet = rig
    incoming = wallet.history()[0].txid
    addr = wallet._tx(incoming).vout[0].script_pubkey.address(wallet.net)
    wallet.set_label("addr", addr, "Mom")
    assert wallet.tx_label(incoming) == "Mom"                # via the address it paid
    old, _ = send(signer, wallet, 30_000, 2)
    wallet.set_label("tx", old.txid, "Rent")
    new, _ = sign_replacement(signer, wallet, wallet.bump(old.txid, 9))
    wallet.record_unconfirmed(new, replaces=old.txid)
    assert wallet.label("tx", new.txid) == "Rent"             # label follows the bump
    lines = [json.loads(x) for x in wallet.export_labels().splitlines()]
    assert {"type": "addr", "ref": addr, "label": "Mom"} in lines
    wallet.set_label("addr", addr, "")
    assert wallet.label("addr", addr) == ""


# ------------------------------------------------------------------- UI
def vault_send(app, sats="30000"):  # noqa: F811
    press(app, "KEY_S")
    type_text(app, DEST)
    enter(app)
    type_text(app, sats)
    enter(app)
    enter(app)
    press(app, "KEY_Z")
    enter(app)
    assert isinstance(app.visible, AnimatedQRScreen)
    enter(app)


def test_bump_from_history(app):  # noqa: F811
    create_wallet(app)
    fund(app.wallet, 100_000)
    vault_send(app)
    old_txid = os.listdir(app.outbox_dir)[0][:-4]
    press(app, "KEY_H")
    sel = next(i for i, it in enumerate(app.visible.items) if it[0].startswith("-"))
    app.visible.sel = sel
    enter(app)
    assert app.visible.title == "TRANSACTION" and app.visible.items[0][0] == "BUMP FEE"
    app.render(0)
    enter(app)                                  # BUMP FEE
    assert app.visible.title == "BUMP FEE"
    enter(app)                                  # MINIMUM
    assert isinstance(app.visible, ConfirmSendScreen) and app.visible.title == "REPLACE TX"   # a fee bump says so, in Vault mode too
    app.render(0)
    press(app, "KEY_Z")
    enter(app)
    outbox = os.listdir(app.outbox_dir)
    assert len(outbox) == 1 and outbox[0][:-4] != old_txid   # replaced in the outbox too
    assert old_txid not in app.wallet.state["txs"]


def test_cancel_from_history(app):  # noqa: F811
    create_wallet(app)
    fund(app.wallet, 100_000)
    vault_send(app)
    press(app, "KEY_H")
    app.visible.sel = next(i for i, it in enumerate(app.visible.items) if it[0].startswith("-"))
    enter(app)
    press(app, "KEY_X")                         # CANCEL TX
    enter(app)
    press(app, "KEY_Z")                         # YES on the warning
    enter(app)
    assert isinstance(app.visible, ConfirmSendScreen) and app.visible.s["spend"] == 0
    app.render(0)
    press(app, "KEY_Z")
    enter(app)
    total = app.wallet.balance()["total"]
    assert 98_000 < total < 100_000             # all back, minus the fee


def test_receive_label_shows_in_history(app):  # noqa: F811
    create_wallet(app)
    press(app, "KEY_R")
    press(app, "KEY_L")
    assert isinstance(app.visible, TextInputScreen)
    type_text(app, "Mom")
    enter(app)
    app.render(0)
    addr = app.visible.addr
    app.wallet.record_unconfirmed(fake_tx(address_to_scriptpubkey(addr), 5_000).serialize())
    press(app, "KEY_ESC")
    press(app, "KEY_H")
    assert app.visible.items[0][2] == "Mom"


def test_bip21_label_saved_on_send(app):  # noqa: F811
    create_wallet(app)
    fund(app.wallet, 100_000)
    use_camera(app, [f"bitcoin:{DEST}?amount=0.0002&label=Coffee%20Shop"])
    press(app, "KEY_Q")
    pump_until(app, lambda: isinstance(app.visible, TextInputScreen) and app.visible.title == "AMOUNT")
    enter(app)
    enter(app)
    press(app, "KEY_Z")
    enter(app)
    txid = os.listdir(app.outbox_dir)[0][:-4]
    assert app.wallet.label("tx", txid) == "Coffee Shop"
    assert app.wallet.label("addr", DEST) == "Coffee Shop"


def test_sim_key_mapping():
    pygame = pytest.importorskip("pygame")
    import sim
    assert sim.key_name(pygame.K_f, pygame) == "KEY_F"
    assert sim.key_name(pygame.K_RETURN, pygame) == "KEY_ENTER"
    assert sim.key_name(pygame.K_LALT, pygame) == "KEY_FN"
    assert sim.key_name(pygame.K_RALT, pygame) == "KEY_RIGHTALT"
    assert sim.key_name(pygame.K_7, pygame) == "KEY_7"
    assert sim.key_name(pygame.K_PERIOD, pygame) == "KEY_DOT"
    assert sim.key_name(pygame.K_F5, pygame) is None


def test_sim_radios_never_touch_the_pc(monkeypatch):
    import core.radios as r
    monkeypatch.setattr(r, "SIM", True)
    calls = []
    monkeypatch.setattr(r.subprocess, "run", lambda *a, **k: calls.append(a))
    r.online_mode()
    assert os.environ["CZ_FORCE_ONLINE"] == "1"
    r.vault_mode()
    assert os.environ["CZ_FORCE_ONLINE"] == "0"
    assert r.wifi_scan()[0]["ssid"] == "Simulated Wi-Fi"
    r.wifi_connect("x", "y")
    assert calls == []
