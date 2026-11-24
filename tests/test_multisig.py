"""
Multisig: policy checks, the signer's registry and its defenses, and a full
2-of-3 spend through the UI where the second signature arrives by QR.
"""
import base64
import glob
import os

import pytest
from embit import bip32, bip39, ec
from embit.networks import NETWORKS as N
from embit.psbt import PSBT
from embit.script import address_to_scriptpubkey
from embit.transaction import Transaction

from core.config import Settings
from core.multisig import PolicyError, build_descriptor, cosigner_key, parse_key, parse_policy
from core.qr import Collector
from core.signer import Signer, SignerError
from core.ur import ur_encode
from core.wallet import WalletService
from ui.camera import decode
from ui.flow import ConfirmSendScreen, HomeScreen
from ui.qrscreens import AnimatedQRScreen

from test_app import app, create_wallet, enter, press, type_text  # noqa: F401
from test_core import ABANDON, DEST, fake_tx
from test_qr import pump_until, use_camera


def sparrowify(desc: str) -> str:
    """How Sparrow writes it: ' for hardened (inside [origin] only), /0/*, checksum."""
    import re
    d = re.sub(r"\[[^\]]*\]", lambda m: m.group(0).replace("h", "'"), desc)
    return d.replace("/<0;1>/*", "/0/*") + "#qwertyui"


def new_root():
    return bip32.HDKey.from_seed(bip39.mnemonic_to_seed(bip39.mnemonic_from_bytes(os.urandom(16))),
                                 version=N["test"]["xprv"])


@pytest.fixture
def ms(tmp_path, monkeypatch):
    monkeypatch.setenv("CZ_FORCE_ONLINE", "0")
    s = Signer(str(tmp_path / "s"))
    info = s.setup("1111", ABANDON, "signet")
    others = [new_root(), new_root()]
    keys = [info["multisig_key"]] + [cosigner_key(r, "signet") for r in others]
    entry = s.register_multisig(build_descriptor(2, keys), "Family")
    w = WalletService(Settings(network="signet"), entry["descriptor"], None, entry["id"],
                      in_memory=True)
    _, addr = w.receive_address()
    w.record_unconfirmed(fake_tx(address_to_scriptpubkey(addr), 300_000).serialize())
    return s, w, others, info, entry


def verify_witness(signed_b64, raw):
    psbt, tx = PSBT.from_string(signed_b64), Transaction.parse(raw)
    for i, vin in enumerate(tx.vin):
        ws = psbt.inputs[i].witness_script
        h = tx.sighash_segwit(i, ws, psbt.utxo(i).value)
        sigs = vin.witness.items[1:-1]
        assert vin.witness.items[0] == b"" and vin.witness.items[-1] == ws.data
        for sig in sigs:
            assert any(pub.verify(ec.Signature.parse(sig[:-1]), h)
                       for pub in psbt.inputs[i].partial_sigs)
        return len(sigs)


# ------------------------------------------------------------ policies
def test_parse_policy_rules():
    roots = [new_root() for _ in range(3)]
    keys = [cosigner_key(r, "signet") for r in roots]
    desc = build_descriptor(2, keys)
    p = parse_policy(desc, "signet", keys[0])
    assert (p.k, p.n) == (2, 3)
    sparrow = sparrowify(desc)
    assert parse_policy(sparrow, "signet", keys[0]).descriptor == p.descriptor
    with pytest.raises(PolicyError, match="isn't in"):
        parse_policy(desc, "signet", cosigner_key(new_root(), "signet"))
    with pytest.raises(PolicyError, match="twice"):
        parse_policy(build_descriptor(2, [keys[0], keys[0], keys[1]]), "signet")
    with pytest.raises(PolicyError, match="sortedmulti"):
        parse_policy(desc.replace("sortedmulti", "multi"), "signet")
    with pytest.raises(PolicyError, match="network"):
        parse_policy(desc, "bitcoin")
    xprv = roots[0].derive("m/48h/1h/0h/2h").to_string()
    with pytest.raises(PolicyError, match="PRIVATE"):
        parse_key(f"[{roots[0].my_fingerprint.hex()}/48h/1h/0h/2h]{xprv}", "signet")


# --------------------------------------------------------------- signer
def test_two_of_three_spend(ms):
    s, w, others, _, _ = ms
    built = w.build(DEST, 80_000, 4)
    summary = s.describe(built["psbt"])
    assert summary["wallet"] == "Family" and summary["sigs_needed"] == 2
    assert sorted(o["kind"] for o in summary["outputs"]) == ["change", "send"]
    assert 3.8 < summary["fee_rate"] < 4.5
    mine = s.sign(built["psbt"], summary["digest"])
    assert w.signatures(mine) == (1, 2)
    theirs = PSBT.from_string(built["psbt"])
    assert theirs.sign_with(others[0]) == 1
    full = w.combine(mine, theirs.to_string())
    tx = w.finalize(full)
    assert verify_witness(full, tx.raw) == 2
    from script_vm import verify_tx
    p = PSBT.from_string(full)
    verify_tx(Transaction.parse(tx.raw),
              [(p.utxo(i).script_pubkey.data, p.utxo(i).value) for i in range(len(p.inputs))])


def test_registry_is_encrypted_and_per_seed(ms, tmp_path):
    s, _, _, _, entry = ms
    files = glob.glob(str(tmp_path / "s" / "ms-*.reg"))
    assert len(files) == 1
    raw = open(files[0], "rb").read()
    assert b"tpub" not in raw and b"Family" not in raw
    s.lock()
    s.unlock("1111")
    assert [e["id"] for e in s.list_multisig()] == [entry["id"]]
    s.set_duress("1111", "2222")
    s.lock()
    s.unlock("2222")                                   # decoy seed: its own (empty) registry
    assert s.list_multisig() == []


def test_change_only_counts_inside_the_same_wallet(ms):
    s, w, others, info, entry = ms
    single = WalletService(Settings(network="signet"), info["descriptor"],
                           info["change_descriptor"], info["fingerprint"], in_memory=True)
    _, a = single.receive_address()
    single.record_unconfirmed(fake_tx(address_to_scriptpubkey(a), 100_000).serialize())
    # single-sig money into the multisig: that's a payment, not change
    _, ms_addr = w.receive_address()
    built = single.build(ms_addr, 40_000, 2)
    out = s.describe(built["psbt"])
    assert out["spend"] == 40_000 and out["wallet"] == "Single-sig"


def test_unregistered_multisig_refused(ms, tmp_path):
    s, w, _, _, entry = ms
    built = w.build(DEST, 10_000, 2)
    s.remove_multisig(entry["id"])
    with pytest.raises(SignerError, match="not from this wallet"):
        s.describe(built["psbt"])


# ------------------------------------------------------------------- UI
def scan_back(scr):
    c = Collector()
    for i in range(len(scr.frames)):
        for t in decode(scr._qr_img(i).convert("L")):
            c.add(t)
    return c.result


def test_create_two_of_three_and_spend_in_the_ui(app):  # noqa: F811
    create_wallet(app)
    others = [new_root(), new_root()]
    keys = [cosigner_key(r, "signet") for r in others]

    press(app, "KEY_W")
    assert app.visible.title == "WALLETS"
    app.visible.sel = next(i for i, it in enumerate(app.visible.items) if it[0] == "MY COSIGNER KEY")
    enter(app)
    assert decode(app.visible._qr_img(0).convert("L")) == [app.public["multisig_key"]]
    enter(app)

    app.visible.sel = next(i for i, it in enumerate(app.visible.items) if it[0] == "NEW MULTISIG")
    enter(app)
    enter(app)                                         # SEGWIT
    use_camera(app, [keys[0]])
    enter(app)                                         # 2 OF 3
    pump_until(app, lambda: app.visible.title == "COSIGNER 2/3")
    use_camera(app, [keys[1]])
    enter(app)
    pump_until(app, lambda: app.visible.title == "COSIGNER 3/3")
    enter(app)
    assert app.visible.title == "NAME IT"
    for _ in range(6):
        press(app, "KEY_BACKSPACE")
    type_text(app, "Family")
    enter(app)
    assert app.visible.title == "CHECK SIGNERS"
    assert any("THIS DEVICE" in line for line in app.visible.lines)
    press(app, "KEY_Z")
    enter(app)                                         # SAVE
    assert isinstance(app.visible, AnimatedQRScreen) and app.visible.title == "DESCRIPTOR"
    assert scan_back(app.visible).text.startswith("wsh(sortedmulti(2,")
    enter(app)
    assert isinstance(app.visible, HomeScreen) and app.wallet_name == "Family"
    app.render(0)

    _, addr = app.wallet.receive_address()
    assert len(addr) == 62                             # P2WSH address
    app.wallet.record_unconfirmed(fake_tx(address_to_scriptpubkey(addr), 250_000).serialize())
    app.refresh_balance()

    press(app, "KEY_S")
    type_text(app, DEST)
    enter(app)
    type_text(app, "60000")
    enter(app)
    enter(app)
    assert isinstance(app.visible, ConfirmSendScreen)
    app.render(0)
    press(app, "KEY_Z")
    enter(app)
    scr = app.visible
    assert scr.title == "NEEDS SIGNATURES"
    psbt_b64 = base64.b64encode(scan_back(scr).data).decode()
    enter(app)
    assert app.wallet.state.get("pending_psbt")
    app.render(0)

    # The second cosigner (e.g. Sparrow + a hardware wallet) signs and shows UR
    theirs = PSBT.from_string(psbt_b64)
    assert theirs.sign_with(others[1]) == 1
    use_camera(app, ur_encode(theirs.serialize()))
    press(app, "KEY_Q")
    pump_until(app, lambda: getattr(app.visible, "title", "") == "FULLY SIGNED")
    outbox = glob.glob(os.path.join(app.outbox_dir, "*.hex"))
    assert len(outbox) == 1 and not app.wallet.state.get("pending_psbt")
    raw = bytes.fromhex(open(outbox[0]).read())
    merged = app.wallet.combine(psbt_b64, theirs.to_string())
    assert verify_witness(merged, raw) == 2
    enter(app)

    # back to single-sig with W
    press(app, "KEY_W")
    enter(app)                                         # SINGLE-SIG
    assert app.wallet_id is None and not app.wallet.multisig


def test_import_sparrow_descriptor(app):  # noqa: F811
    create_wallet(app)
    keys = [app.public["multisig_key"]] + [cosigner_key(new_root(), "signet") for _ in range(2)]
    sparrow = sparrowify(build_descriptor(2, keys))
    press(app, "KEY_W")
    app.visible.sel = next(i for i, it in enumerate(app.visible.items) if it[0] == "IMPORT DESCRIPTOR")
    use_camera(app, [sparrow])
    enter(app)
    pump_until(app, lambda: getattr(app.visible, "title", "") == "NAME IT")
    enter(app)                                         # keep the default name
    assert app.visible.title == "CHECK SIGNERS"
    press(app, "KEY_Z")
    enter(app)
    enter(app)                                         # close descriptor QR
    assert app.wallet_name == "Imported" and app.wallet.multisig
