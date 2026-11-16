"""
Inheritance wallets: policy checks, owner and heir spends verified by a
script interpreter (signatures + CHECKSEQUENCEVERIFY), the refresh, and
both setups through the UI.
"""
import glob
import os
import re

import pytest
from embit.psbt import PSBT
from embit.script import Witness, address_to_scriptpubkey
from embit.transaction import Transaction

from core import inherit
from core.config import Settings
from core.multisig import PolicyError, cosigner_key, parse_any
from core.signer import Signer
from core.wallet import WalletError, WalletService
from ui.flow import ConfirmSendScreen, HomeScreen
from ui.widgets import MessageScreen

from script_vm import ScriptFail, verify_tx
from test_app import app, create_wallet, enter, press, type_text  # noqa: F401
from test_core import ABANDON, DEST, fake_tx
from test_multisig import new_root, sparrowify
from test_qr import pump_until, use_camera

DELAY = 144


def fund(w, sats, height):
    _, addr = w.receive_address()
    tx = fake_tx(address_to_scriptpubkey(addr), sats)
    w.record_unconfirmed(tx.serialize())
    w.state["txs"][tx.txid().hex()]["height"] = height
    w.state["txs"][tx.txid().hex()].pop("local", None)
    return tx


def prevouts(signed_b64):
    p = PSBT.from_string(signed_b64)
    return [(p.utxo(i).script_pubkey.data, p.utxo(i).value) for i in range(len(p.inputs))]


def setup(tmp_path, role):
    s = Signer(str(tmp_path / f"s-{role}"))
    info = s.setup("1111", ABANDON, "signet")
    other = new_root()
    mine, theirs = info["multisig_key"], cosigner_key(other, "signet")
    owner, heir = (mine, theirs) if role == "owner" else (theirs, mine)
    entry = s.register_multisig(inherit.build(owner, heir, DELAY), "Family")
    assert entry["kind"] == "inherit" and entry["role"] == role
    w = WalletService(Settings(network="signet"), entry["descriptor"], None, entry["id"],
                      in_memory=True, role=role)
    return s, w, other


def sign(s, w, built):
    summary = s.describe(built["psbt"])
    signed = s.sign(built["psbt"], summary["digest"])
    return summary, signed, w.finalize(signed)


# ------------------------------------------------------------- policies
def test_policy_rules():
    a, b = new_root(), new_root()
    ka, kb = cosigner_key(a, "signet"), cosigner_key(b, "signet")
    desc = inherit.build(ka, kb, 52560)
    p = parse_any(sparrowify(desc).replace("/0/*", "/<0;1>/*"), "signet", ka)
    assert p.kind == "inherit" and p.delay == 52560 and p.cosigners == [ka[1:9], kb[1:9]]
    assert p.descriptor == desc
    with pytest.raises(PolicyError, match="isn't in"):
        parse_any(desc, "signet", cosigner_key(new_root(), "signet"))
    with pytest.raises(PolicyError, match="different"):
        inherit.parse(inherit.build(ka, ka, 100), "signet")
    with pytest.raises(PolicyError):
        inherit.build(ka, kb, 70000)                       # beyond BIP68's 16 bits
    assert inherit.describe_delay(52560) == "12 MONTHS"


# ---------------------------------------------------------------- owner
def test_owner_spends_any_time(tmp_path):
    s, w, _ = setup(tmp_path, "owner")
    fund(w, 200_000, 1000)
    w.tip_height = 1001                                    # fresh coin: heir far from it
    summary, signed, tx = sign(s, w, w.build(DEST, 50_000, 3))
    assert summary["wallet"] == "Family" and summary["spend"] == 50_000
    assert 2.6 < summary["fee_rate"] < 3.4
    parsed = Transaction.parse(tx.raw)
    assert len(parsed.vin[0].witness.items) == 2           # [owner sig, script]
    verify_tx(parsed, prevouts(signed))


def test_refresh_restarts_the_timer(tmp_path):
    s, w, _ = setup(tmp_path, "owner")
    fund(w, 120_000, 1000)
    fund(w, 30_000, 1050)
    w.tip_height = 1000 + DELAY + 5
    t = w.timer()
    assert t == {"unlocked": 120_000, "locked": 30_000, "soonest": 44, "unknown": False}
    built = w.refresh(2)
    summary, signed, tx = sign(s, w, built)
    assert summary["spend"] == 0 and [o["kind"] for o in summary["outputs"]] == ["change"]
    parsed = Transaction.parse(tx.raw)
    assert len(parsed.vin) == 2 and len(parsed.vout) == 1
    verify_tx(parsed, prevouts(signed))


# ----------------------------------------------------------------- heir
def test_heir_waits_then_spends(tmp_path):
    s, w, _ = setup(tmp_path, "heir")
    fund(w, 90_000, 2000)
    w.tip_height = 2000 + DELAY - 11                      # 10 confirmations short
    with pytest.raises(WalletError, match="nothing unlocked yet: next in ~10 blocks"):
        w.build(DEST, 10_000, 2)
    assert w.balance()["spendable"] == 0 and w.balance()["total"] == 90_000

    w.tip_height = 2000 + DELAY - 1                        # exactly DELAY confirmations
    built = w.build(DEST, 10_000, 2)
    assert {i.sequence for i in PSBT.from_string(built["psbt"]).tx.vin} == {DELAY}
    summary, signed, tx = sign(s, w, built)
    parsed = Transaction.parse(tx.raw)
    items = parsed.vin[0].witness.items
    assert len(items) == 4 and items[2] == b""             # [sig, pubkey, empty, script]
    verify_tx(parsed, prevouts(signed))


def test_heir_signature_fails_csv_when_too_early(tmp_path):
    s, w, _ = setup(tmp_path, "heir")
    fund(w, 90_000, 2000)
    w.tip_height = 2000 + DELAY
    built = w.build(DEST, 10_000, 2)
    psbt = PSBT.from_string(built["psbt"])
    psbt.inputs[0].sequence = DELAY - 1                    # try to sneak in early
    summary = s.describe(psbt.to_string())
    signed = s.sign(psbt.to_string(), summary["digest"])
    with pytest.raises(WalletError, match="time-lock"):
        w.finalize(signed)
    p = PSBT.from_string(signed)
    tx = Transaction.parse(p.tx.serialize())
    pub, sig = next(iter(p.inputs[0].partial_sigs.items()))
    tx.vin[0].witness = Witness([sig, pub.sec(), b"", p.inputs[0].witness_script.data])
    with pytest.raises(ScriptFail, match="CSV"):
        verify_tx(tx, prevouts(signed))


def test_heir_cannot_use_the_owner_path(tmp_path):
    s, w, _ = setup(tmp_path, "heir")
    fund(w, 90_000, 2000)
    w.tip_height = 2000 + DELAY
    _, signed, tx = sign(s, w, w.build(DEST, 10_000, 2))
    p = PSBT.from_string(signed)
    t = Transaction.parse(tx.raw)
    pub, sig = next(iter(p.inputs[0].partial_sigs.items()))
    t.vin[0].witness = Witness([sig, p.inputs[0].witness_script.data])   # heir sig as owner
    with pytest.raises(ScriptFail):
        verify_tx(t, prevouts(signed))


def test_heir_offline_without_height(tmp_path):
    s, w, _ = setup(tmp_path, "heir")
    fund(w, 90_000, 2000)
    w.tip_height = None
    with pytest.raises(WalletError, match="Online"):
        w.build(DEST, 10_000, 2)


# ------------------------------------------------------------------- UI
def create_inheritance(app, role, other_key, name="Family"):
    press(app, "KEY_W")
    app.visible.sel = next(i for i, it in enumerate(app.visible.items) if it[0] == "NEW INHERITANCE")
    enter(app)
    app.visible.sel = 0 if role == "owner" else 1
    use_camera(app, [other_key])
    enter(app)
    pump_until(app, lambda: getattr(app.visible, "title", "") == "HEIR WAITS")
    assert app.visible.items[0][0] == "10 BLOCKS"              # test-network presets first
    app.visible.sel = 1                                       # 1 DAY = 144 blocks
    enter(app)
    assert app.visible.title == "NAME IT"
    for _ in range(12):
        press(app, "KEY_BACKSPACE")
    type_text(app, name)
    enter(app)
    assert app.visible.title == "CHECK SIGNERS" and app.console.stack   # on the signer
    lines = " | ".join(app.visible.lines)
    app.render(0)
    press(app, "KEY_Z")
    enter(app)                                            # SAVE
    enter(app)                                            # close the descriptor QR
    assert isinstance(app.visible, HomeScreen) and app.wallet.kind == "inherit"
    return lines


def home_status(app):
    from ui.inherit_flow import home_lines
    return home_lines(app)[1]


def test_owner_setup_and_refresh_in_the_ui(app):  # noqa: F811
    create_wallet(app)
    heir_key = cosigner_key(new_root(), "signet")
    lines = create_inheritance(app, "owner", heir_key)
    assert re.search(r"OWNER \w{8}  THIS DEVICE", lines) and f"HEIR  {heir_key[1:9]}" in lines
    assert app.wallet.role == "owner" and app.wallet.delay == 144
    w = app.wallet
    fund(w, 150_000, 500)
    w.tip_height = 500 + 30
    app.refresh_balance()
    assert home_status(app)[0].startswith("REFRESH SOON")
    app.render(0)
    w.tip_height = 500 + 200
    assert home_status(app) == ("HEIR CAN SPEND NOW: REFRESH (W)", "red")

    press(app, "KEY_W")
    app.visible.sel = next(i for i, it in enumerate(app.visible.items) if it[0] == "Family")
    enter(app)
    assert app.visible.items[1][0] == "REFRESH TIMER"
    app.visible.sel = 1
    enter(app)
    assert isinstance(app.visible, ConfirmSendScreen) and app.visible.s["spend"] == 0
    app.render(0)
    press(app, "KEY_Z")
    enter(app)
    out = glob.glob(os.path.join(app.outbox_dir, "*.hex"))
    assert len(out) == 1
    tx = Transaction.parse(bytes.fromhex(open(out[0]).read()))
    assert len(tx.vin[0].witness.items) == 2


def test_heir_setup_and_spend_in_the_ui(app):  # noqa: F811
    create_wallet(app)
    owner_key = cosigner_key(new_root(), "signet")
    create_inheritance(app, "heir", owner_key, name="Dad")
    w = app.wallet
    assert w.role == "heir"
    fund(w, 80_000, 700)
    w.tip_height = 760
    app.refresh_balance()
    assert home_status(app) == ("NEXT UNLOCK IN ~83 BLOCKS", "cyan")   # 61 of 144 confs

    def send():
        press(app, "KEY_S")
        type_text(app, DEST)
        enter(app)
        type_text(app, "20000")
        enter(app)
        enter(app)
    send()
    assert isinstance(app.visible, MessageScreen) and "nothing unlocked" in " ".join(app.visible.lines)
    app.home()
    w.tip_height = 700 + 144
    assert home_status(app) == ("UNLOCKED 80,000 SATS", "green")
    send()
    assert isinstance(app.visible, ConfirmSendScreen)
    press(app, "KEY_Z")
    enter(app)
    out = glob.glob(os.path.join(app.outbox_dir, "*.hex"))
    tx = Transaction.parse(bytes.fromhex(open(out[0]).read()))
    assert tx.vin[0].sequence == 144 and len(tx.vin[0].witness.items) == 4
    prev = Transaction.from_string(w.state["txs"][tx.vin[0].txid.hex()]["hex"]).vout[tx.vin[0].vout]
    verify_tx(tx, [(prev.script_pubkey.data, prev.value)])
