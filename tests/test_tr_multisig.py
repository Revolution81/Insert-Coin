"""Taproot multisig, tr(NUMS, sortedmulti_a(k, ...)): registered and checked
on the signer, built and finalized by the wallet, and every spend run
through the test interpreter (control block, Schnorr, CHECKSIGADD)."""
import pytest
from embit.psbt import PSBT
from embit.script import address_to_scriptpubkey
from embit.transaction import Transaction

from core import multisig as ms
from core.config import Settings
from core.signer import Signer, SignerError
from core.wallet import WalletService
from script_vm import ScriptFail, verify_tx
from test_core import ABANDON, DEST, fake_tx
from test_multisig import new_root
from test_app import app  # noqa: F401


@pytest.fixture
def tr(tmp_path, monkeypatch):
    monkeypatch.setenv("CZ_FORCE_ONLINE", "0")
    s = Signer(str(tmp_path / "s"))
    info = s.setup("1111", ABANDON, "signet")
    others = [new_root(), new_root()]
    keys = [info["multisig_key_tr"]] + [ms.cosigner_key(r, "signet", taproot=True) for r in others]
    entry = s.register_multisig(ms.build_descriptor(2, keys, taproot=True), "Tap")
    assert entry["taproot"] and entry["name"] == "Tap" and entry["k"] == 2
    w = WalletService(Settings(network="signet"), entry["descriptor"], None, entry["id"],
                      in_memory=True)
    _, addr = w.receive_address()
    assert addr.startswith("tb1p")
    w.record_unconfirmed(fake_tx(address_to_scriptpubkey(addr), 300_000).serialize())
    return s, w, others


def _prevouts(p):
    return [(p.utxo(i).script_pubkey.data, p.utxo(i).value) for i in range(len(p.inputs))]


def test_two_of_three_taproot_spend(tr):
    s, w, others = tr
    built = w.build(DEST, 80_000, 4)
    summary = s.describe(built["psbt"])
    assert summary["sigs_needed"] == 2 and summary["wallet"] == "Tap"
    assert sorted(o["kind"] for o in summary["outputs"]) == ["change", "send"]
    assert 3.6 < summary["fee_rate"] < 4.6
    mine = s.sign(built["psbt"], summary["digest"])
    assert w.signatures(mine) == (1, 2)
    with pytest.raises(Exception, match="more signature"):
        w.finalize(mine)
    theirs = PSBT.from_string(built["psbt"])
    assert theirs.sign_with(others[1]) == 1
    full = w.combine(mine, theirs.to_string())
    assert w.signatures(full) == (2, 2)
    tx = w.finalize(full)
    p = PSBT.from_string(full)
    verify_tx(Transaction.parse(tx.raw), _prevouts(p))


def test_wrong_signature_fails_the_interpreter(tr):
    s, w, others = tr
    built = w.build(DEST, 50_000, 4)
    p = PSBT.from_string(built["psbt"])
    p.sign_with(others[0])
    p.sign_with(others[1])
    tx = w.finalize(p.to_string())
    raw = bytearray(tx.raw)
    parsed = Transaction.parse(bytes(raw))
    items = parsed.vin[0].witness.items
    sig = [x for x in items if len(x) == 64][0]
    bad = bytearray(sig)
    bad[5] ^= 1
    parsed.vin[0].witness.items[items.index(sig)] = bytes(bad)
    with pytest.raises(ScriptFail):
        verify_tx(parsed, _prevouts(p))


def test_signer_refuses_a_foreign_leaf(tr):
    s, w, _ = tr
    built = w.build(DEST, 50_000, 4)
    p = PSBT.from_string(built["psbt"])
    cb, sc = next(iter(p.inputs[0].taproot_scripts.items()))
    bad = bytearray(sc)
    bad[5] ^= 1                                       # another key in the script
    p.inputs[0].taproot_scripts[cb] = bytes(bad)
    with pytest.raises(SignerError, match="isn't this wallet's"):
        s.describe(p.to_string())


def test_parse_rules():
    roots = [new_root() for _ in range(3)]
    keys = [ms.cosigner_key(r, "signet", taproot=True) for r in roots]
    good = ms.build_descriptor(2, keys, taproot=True)
    import re
    # apostrophes for "h" only inside the [origin] brackets: an xpub can end
    # in "h" too, and a blind replace broke one about 1 time in 20
    quoted = re.sub(r"\[[^\]]*\]", lambda m: m.group(0).replace("h", "'"), good)
    p = ms.parse_policy(quoted, "signet", ms.my_keys(roots[0], "signet"))
    assert p.taproot and p.k == 2 and p.n == 3 and p.label == "2-of-3 Taproot"
    # a real point (the generator's x), so the only problem is "not NUMS";
    # "02" * 32 is a valid x only about half the time and made this flaky
    other = good.replace(ms.NUMS, "79be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798")
    with pytest.raises(ms.PolicyError, match="unspendable"):
        ms.parse_policy(other, "signet")
    with pytest.raises(ms.PolicyError, match="isn't in that wallet"):
        ms.parse_policy(good, "signet", ms.my_keys(new_root(), "signet"))


def test_new_taproot_multisig_on_the_screens(app):  # noqa: F811
    from test_app import create_wallet, enter, settle
    from test_qr import pump_until, use_camera
    create_wallet(app)
    other = ms.cosigner_key(new_root(), "signet", taproot=True)
    from ui.multisig_flow import new_multisig
    new_multisig(app)
    app.visible.sel = [i[0] for i in app.visible.items].index("TAPROOT")
    enter(app)
    app.visible.sel = [i[0] for i in app.visible.items].index("2 OF 2")
    use_camera(app, [other])
    enter(app)
    pump_until(app, lambda: app.visible.title == "COSIGNER 2/2")
    enter(app)
    assert app.visible.title == "NAME IT"
    enter(app)                                       # "2-of-2 Tap"
    settle(app)
    assert app.visible.title == "CHECK SIGNERS"
    assert any("TAPROOT" in line.upper() for line in app.visible.lines)
    from test_app import press
    press(app, "KEY_Z")
    enter(app)                                       # the signer's SAVE
    settle(app)
    assert app.visible.title == "DESCRIPTOR"
    enter(app)
    assert app.wallet.ms_taproot and app.wallet.k == 2
    assert app.wallet.receive_address()[1].startswith("tb1p")
    app.render(0)
