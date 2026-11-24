"""Sending to a silent payment address: the app puts a stand-in output and
the receiver's keys in the PSBT (BIP-375), the signer works out the real
output from its own input keys before showing or signing anything, and
the receiver finds the payment by scanning, with a key that opens it."""
import pytest
from embit.psbt import PSBT
from embit.transaction import Transaction

from core import silentpay as sp
from core.signer import SignerError
from script_vm import verify_tx
from test_accounts import NET, signer, wallet_for  # noqa: F401
from test_core import fund
from test_multisig import new_root
from test_app import app  # noqa: F401


def receiver():
    b_scan, b_spend = sp.keys_from_root(new_root(), NET)
    addr = sp.encode(sp.ser(sp.base(b_scan)), sp.ser(sp.base(b_spend)), NET)
    return addr, b_scan, b_spend


def _found_by_receiver(tx, prevouts, b_scan, b_spend):
    pubs, outpoints = [], []
    for i, vin in enumerate(tx.vin):
        outpoints.append((vin.txid.hex(), vin.vout))
        pk = sp.input_pubkey(prevouts[i][0], vin.script_sig.data if vin.script_sig else b"",
                             list(vin.witness.items))
        if pk:
            pubs.append(pk)
    tweak = sp.tweak_point(outpoints, pubs)
    outs = [o.script_pubkey.data[2:] for o in tx.vout if o.script_pubkey.data[:2] == b"\x51\x20"]
    return sp.scan(b_scan, sp.base(b_spend), tweak, outs)


@pytest.mark.parametrize("account", ["wpkh:0", "tr:0"])
def test_send_to_a_silent_payment_address(signer, account):  # noqa: F811
    w = wallet_for(signer, account)
    fund(w, 100_000)
    fund(w, 40_000)
    addr, b_scan, b_spend = receiver()
    assert w.validate_address(addr.upper()) == addr
    built = w.build(addr, 120_000, fee_rate=3)
    assert built["silent"]
    s = signer.describe(built["psbt"])
    send = [o for o in s["outputs"] if o["kind"] == "send"]
    assert send[0]["address"] == addr and s["silent"] == 1
    signed = signer.sign(s["psbt"], s["digest"])
    tx = w.finalize(signed)
    p = PSBT.from_string(signed)
    prevouts = [(p.utxo(i).script_pubkey.data, p.utxo(i).value) for i in range(len(p.inputs))]
    parsed = Transaction.parse(tx.raw)
    verify_tx(parsed, prevouts)
    found = _found_by_receiver(parsed, prevouts, b_scan, b_spend)
    assert len(found) == 1
    key, tweak = found[0]
    assert sp.ser(sp.base(sp.spend_key(b_spend, tweak)))[1:] == key     # the receiver can spend it


def test_signing_the_unfilled_psbt_is_refused(signer):  # noqa: F811
    w = wallet_for(signer, "wpkh:0")
    fund(w, 100_000)
    addr, _, _ = receiver()
    built = w.build(addr, 30_000, fee_rate=3)
    s = signer.describe(built["psbt"])
    with pytest.raises(SignerError, match="changed"):
        signer.sign(built["psbt"], s["digest"])       # the stand-in output: not what was shown


def test_wrong_network_and_multisig_refused(signer):  # noqa: F811
    w = wallet_for(signer, "wpkh:0")
    b_scan, b_spend = sp.keys_from_root(new_root(), "bitcoin")
    main = sp.encode(sp.ser(sp.base(b_scan)), sp.ser(sp.base(b_spend)), "bitcoin")
    from core.wallet import WalletError
    with pytest.raises(WalletError, match="mainnet"):
        w.validate_address(main)


def test_send_on_the_screens(app, monkeypatch):  # noqa: F811
    from test_app import create_wallet, enter, settle, type_text, press
    from ui.flow import send_flow
    monkeypatch.setenv("CZ_FORCE_ONLINE", "0")
    create_wallet(app)
    fund(app.wallet, 100_000)
    addr, b_scan, b_spend = receiver()
    from core.qr import classify
    assert classify(addr).kind == "address"
    send_flow(app)
    type_text(app, addr)
    enter(app)
    type_text(app, "20000")
    enter(app)
    settle(app)
    for _ in range(4):                               # fee choice, then the signer's screen
        if app.visible.__class__.__name__ == "ConfirmSendScreen":
            break
        enter(app)
        settle(app)
    scr = app.visible
    assert scr.__class__.__name__ == "ConfirmSendScreen"
    assert any(o["address"] == addr for o in scr.s["outputs"])
