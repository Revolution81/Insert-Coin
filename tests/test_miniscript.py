"""Miniscript wallets (wsh and Taproot trees): registered on the signer when
this device's key is in them, checked input by input, signed; the wallet
that made them finalizes. Here the tests finalize by hand and run every
spend through the test interpreter."""
import pytest
from embit.psbt import PSBT
from embit.script import Script, address_to_scriptpubkey
from embit.transaction import Transaction
from embit.script import Witness

from core import multisig as ms
from core.config import Settings
from core.signer import Signer, SignerError
from core.wallet import WalletService, WalletError
from script_vm import verify_tx
from test_core import ABANDON, DEST, fake_tx
from test_multisig import new_root
from test_app import app  # noqa: F401


def key(root, purpose_path="48h/1h/0h/2h"):
    from embit.networks import NETWORKS as N
    xpub = root.derive("m/" + purpose_path).to_public().to_string(version=N["test"]["xpub"])
    return f"[{root.my_fingerprint.hex()}/{purpose_path}]{xpub}/<0;1>/*"


def setup(tmp_path, monkeypatch, make_desc):
    monkeypatch.setenv("CZ_FORCE_ONLINE", "0")
    s = Signer(str(tmp_path / "s"))
    s.setup("1111", ABANDON, "signet")
    me = s._root
    others = [new_root(), new_root()]
    desc = make_desc(key(me), key(others[0]), key(others[1]))
    entry = s.register_multisig(desc, "Mini")
    assert entry["kind"] == "miniscript" and entry["name"] == "Mini"
    w = WalletService(Settings(network="signet"), entry["descriptor"], None, entry["id"],
                      in_memory=True)
    _, addr = w.receive_address()
    w.record_unconfirmed(fake_tx(address_to_scriptpubkey(addr), 300_000).serialize())
    return s, w, me, others


def _prevouts(p):
    return [(p.utxo(i).script_pubkey.data, p.utxo(i).value) for i in range(len(p.inputs))]


def test_wsh_two_of_two_with_a_recovery_key(tmp_path, monkeypatch):
    s, w, me, others = setup(tmp_path, monkeypatch, lambda a, b, c:
                             f"wsh(or_d(multi(2,{a},{b}),and_v(v:pk({c}),older(1000))))")
    built = w.build(DEST, 80_000, 4)
    summary = s.describe(built["psbt"])
    assert summary["wallet"] == "Mini"
    assert sorted(o["kind"] for o in summary["outputs"]) == ["change", "send"]
    mine = PSBT.from_string(s.sign(built["psbt"], summary["digest"]))
    assert w.signatures(mine.to_string()) == (1, 2)             # never "complete" here
    with pytest.raises(WalletError, match="wallet that made"):
        w.finalize(mine.to_string())
    mine.sign_with(others[0])
    tx = Transaction.parse(mine.tx.serialize())
    for i, inp in enumerate(mine.inputs):                       # finalize by hand
        ws = inp.witness_script.data
        order = [ws[j + 1:j + 34] for j in range(len(ws) - 33) if ws[j] == 0x21][:2]
        by = {p.sec(): sig for p, sig in inp.partial_sigs.items()}
        tx.vin[i].witness = Witness([b""] + [by[k] for k in order] + [ws])
    verify_tx(tx, _prevouts(mine))


def test_taproot_tree_leaf_and_key_path(tmp_path, monkeypatch):
    s, w, me, others = setup(tmp_path, monkeypatch, lambda a, b, c:
                             f"tr({b},{{pk({a}),and_v(v:pk({c}),older(10))}})")
    built = w.build(DEST, 50_000, 4)
    summary = s.describe(built["psbt"])
    p = PSBT.from_string(s.sign(built["psbt"], summary["digest"]))
    tx = Transaction.parse(p.tx.serialize())
    for i, inp in enumerate(p.inputs):                          # our leaf: pk(this device)
        (pub, leaf), sig = next(iter(inp.taproot_sigs.items()))
        for cb, sc in inp.taproot_scripts.items():
            if pub.xonly() in sc and len(sc) == 35:
                tx.vin[i].witness = Witness([sig, bytes(sc[:-1]), bytes(cb)])
    verify_tx(tx, _prevouts(p))
    # the internal key's owner spends by key path (another wallet, here embit)
    q = PSBT.from_string(built["psbt"])
    assert q.sign_with(others[0]) >= 1
    tx2 = Transaction.parse(q.tx.serialize())
    for i, inp in enumerate(q.inputs):
        tx2.vin[i].witness = inp.final_scriptwitness
    verify_tx(tx2, _prevouts(q))


def test_signer_checks(tmp_path, monkeypatch):
    s, w, me, others = setup(tmp_path, monkeypatch, lambda a, b, c:
                             f"tr({b},{{pk({a}),and_v(v:pk({c}),older(10))}})")
    built = w.build(DEST, 50_000, 4)
    p = PSBT.from_string(built["psbt"])
    cb, sc = next(iter(p.inputs[0].taproot_scripts.items()))
    bad = bytearray(sc)
    bad[3] ^= 1
    p.inputs[0].taproot_scripts[cb] = bytes(bad)
    with pytest.raises(SignerError, match="isn't this wallet's"):
        s.describe(p.to_string())
    strangers = [key(new_root()) for _ in range(2)]
    with pytest.raises(Exception, match="isn't in that wallet"):
        s.register_multisig(f"wsh(or_d(pk({strangers[0]}),and_v(v:pk({strangers[1]}),older(5))))")
    with pytest.raises(Exception, match="twice"):
        s.register_multisig(f"wsh(or_d(pk({key(me)}),and_v(v:pk({key(me)}),older(5))))")


def test_import_on_the_screens(app):  # noqa: F811
    from test_app import create_wallet, enter, press, settle
    from ui.multisig_flow import _confirm_and_register
    create_wallet(app)
    me = app.signer._local.signer._root
    desc = f"wsh(or_d(pk({key(me)}),and_v(v:pk({key(new_root())}),older(4032))))"
    desc = desc.replace("or_d(pk(", "or_d(multi(1,").replace(")),and_v", "),and_v", 1)
    _confirm_and_register(app, desc, "Imported")
    settle(app)
    assert app.visible.title == "NAME IT"
    enter(app)
    settle(app)
    assert app.visible.title == "CHECK SIGNERS"
    text = " ".join(app.visible.lines)
    assert "miniscript" in text and "THIS DEVICE" in text and "older(4032)" in text
    app.render(0)
    press(app, "KEY_Z")
    enter(app)
    settle(app)
    enter(app)                                       # the descriptor QR
    assert app.wallet.kind == "miniscript"
    assert app.wallet.receive_address()[1].startswith("tb1q")
