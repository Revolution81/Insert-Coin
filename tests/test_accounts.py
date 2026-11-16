"""Single-sig accounts and Taproot: BIP84 and BIP86, any account number."""
import pytest
from embit import bip32, bip39
from embit.psbt import PSBT
from embit.transaction import Transaction

from core import accounts as accts
from core import seed as seedlib
from core.config import Settings
from core.signer import Signer, SignerError
from core.wallet import WalletService

from test_core import ABANDON, DEST, NET, fund
from script_vm import ScriptFail, verify_tx


@pytest.fixture
def signer(tmp_path, monkeypatch):
    monkeypatch.setenv("CZ_FORCE_ONLINE", "0")
    s = Signer(str(tmp_path / "signer"))
    s.setup("2468", ABANDON, NET)
    return s


def wallet_for(signer, account):
    info = signer.account_info(account)
    return WalletService(Settings(network=NET), info["descriptor"], info["change_descriptor"],
                         info["fingerprint"], in_memory=True)


def spend(signer, w, sats=30_000, rate=3):
    built = w.build(DEST, sats, fee_rate=rate)
    summary = signer.describe(built["psbt"])
    signed = signer.sign(built["psbt"], summary["digest"])
    tx = w.finalize(signed)
    p = PSBT.from_string(signed)
    verify_tx(Transaction.parse(tx.raw),
              [(p.utxo(i).script_pubkey.data, p.utxo(i).value) for i in range(len(p.inputs))])
    return summary, tx


def test_bip86_vectors():
    """BIP86's own test vectors (mainnet, 'abandon ... about')."""
    root = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(ABANDON))
    recv, change = seedlib.public_descriptors(root, "bitcoin", 0, "tr")
    from embit.descriptor import Descriptor
    from embit.networks import NETWORKS
    r, c = Descriptor.from_string(recv), Descriptor.from_string(change)
    main = NETWORKS["main"]
    assert r.derive(0).address(main) == "bc1p5cyxnuxmeuwuvkwfem96lqzszd02n6xdcjrs20cac6yqjjwudpxqkedrcr"
    assert r.derive(1).address(main) == "bc1p4qhjn9zdvkux4e44uhx8tc55attvtyu358kutcqkudyccelu0was9fqzwh"
    assert c.derive(0).address(main) == "bc1p3qkhfews2uk44qtvauqyr2ttdsw7svhkl9nkm9s9c3x4ax5h60wqwruhk7"


def test_account_ids():
    assert accts.parse(None) == ("wpkh", 0)
    assert accts.parse("tr:12") == ("tr", 12)
    for bad in ("tr", "tr:-1", "tr:1000", "sh:0", "tr:1:2", "tr:x", 5):
        with pytest.raises(accts.AccountError):
            accts.parse(bad)
    assert accts.label("wpkh:0") == "Main" and accts.label("tr:3") == "Taproot #3"


def test_account_info_is_public_only(signer):
    info = signer.account_info("tr:2")
    assert info["descriptor"].startswith("tr([") and "/86'/1'/2']tpub" in info["descriptor"]
    assert "prv" not in info["descriptor"]
    main = signer.account_info("wpkh:0")
    assert main["descriptor"] == signer.public_info()["descriptor"]
    with pytest.raises(SignerError):
        signer.account_info("sh:0")


@pytest.mark.parametrize("account", ["tr:0", "tr:5", "wpkh:3"])
def test_spend_from_any_account(signer, account):
    w = wallet_for(signer, account)
    fund(w, 100_000)
    if account.startswith("tr"):
        assert w.receive_address()[1].startswith("tb1p")
    summary, tx = spend(signer, w)
    assert summary["spend"] == 30_000
    assert [o["kind"] for o in summary["outputs"]].count("change") == 1
    assert summary["wallet_id"] == account
    assert summary["wallet"] == accts.label(account)
    # the fee is sized for this input type: never under the asked rate
    t = Transaction.parse(tx.raw)
    full = len(t.serialize())
    for v in t.vin:
        v.witness.items = []
    base = len(t.serialize()) - 2                     # no marker/flag
    vbytes = (base * 4 + (full - base)) / 4
    assert 3.0 <= summary["fee"] / vbytes < 3.6


def test_taproot_signature_is_really_checked(signer):
    w = wallet_for(signer, "tr:0")
    fund(w, 100_000)
    built = w.build(DEST, 20_000, fee_rate=2)
    summary = signer.describe(built["psbt"])
    signed = signer.sign(built["psbt"], summary["digest"])
    tx = Transaction.parse(w.finalize(signed).raw)
    p = PSBT.from_string(signed)
    prev = [(p.utxo(0).script_pubkey.data, p.utxo(0).value)]
    sig = bytearray(tx.vin[0].witness.items[0])
    sig[5] ^= 1
    from embit.script import Witness
    tx.vin[0].witness = Witness([bytes(sig)])
    with pytest.raises(ScriptFail):
        verify_tx(tx, prev)
    # and an amount lie changes the sighash
    tx2 = Transaction.parse(w.finalize(signed).raw)
    with pytest.raises(ScriptFail):
        verify_tx(tx2, [(prev[0][0], prev[0][1] + 1)])


def test_change_must_stay_in_the_same_account(signer):
    """Change sent to another of my accounts isn't hidden as change: the
    confirm screen shows it as a payment."""
    a, b = wallet_for(signer, "tr:0"), wallet_for(signer, "wpkh:0")
    fund(a, 100_000)
    _, other = b.receive_address()
    built = a.build(other, 10_000, fee_rate=2)
    s = signer.describe(built["psbt"])
    kinds = {o["address"]: o["kind"] for o in s["outputs"]}
    assert kinds[other] == "send" and s["spend"] == 10_000


def test_mixed_accounts_refused(signer):
    a, b = wallet_for(signer, "tr:0"), wallet_for(signer, "tr:1")
    fund(a, 50_000)
    fund(b, 50_000)
    pa = PSBT.from_string(a.build(DEST, 10_000, fee_rate=2)["psbt"])
    pb = PSBT.from_string(b.build(DEST, 10_000, fee_rate=2)["psbt"])
    pa.tx.vin.append(pb.tx.vin[0])
    pa.inputs.append(pb.inputs[0])
    with pytest.raises(SignerError, match="different wallets"):
        signer.describe(pa.to_string())


def test_taproot_script_paths_refused(signer):
    w = wallet_for(signer, "tr:0")
    fund(w, 100_000)
    p = PSBT.from_string(w.build(DEST, 10_000, fee_rate=2)["psbt"])
    p.inputs[0].taproot_merkle_root = b"\x01" * 32
    with pytest.raises(SignerError):
        signer.describe(p.to_string())


def test_address_and_message_for_accounts(signer):
    from core import message as M
    from embit.script import address_to_scriptpubkey
    w = wallet_for(signer, "tr:1")
    assert signer.address(wallet_id="tr:1", index=0) == w.receive_address()[1]
    r = signer.sign_message(index=0, message="hi", fmt="bip322", account="tr:1")
    assert r["address"].startswith("tb1p")
    assert M.verify(address_to_scriptpubkey(r["address"]), "hi", r["signature"])
    with pytest.raises(SignerError, match="BIP322"):
        signer.sign_message(index=0, message="hi", fmt="electrum", account="tr:1")


# ------------------------------------------------------------------ screens
from test_app import app, create_wallet, enter, press, type_text  # noqa: E402,F401


def goto(app, label):  # noqa: F811
    labels = [it[0] for it in app.visible.items]
    app.visible.sel = labels.index(label)
    enter(app)


def test_new_taproot_account_send_and_verify(app):  # noqa: F811
    from ui.flow import ConfirmSendScreen, HomeScreen, ReceiveScreen
    from ui.secure_flows import AddressScreen
    create_wallet(app)
    press(app, "KEY_W")
    goto(app, "NEW ACCOUNT")
    goto(app, "CUSTOM TAPROOT")
    assert app.visible.title == "NAME IT" and app.visible.buf.text == "Taproot"
    enter(app)
    assert isinstance(app.visible, HomeScreen) and app.wallet_id == "tr:0"
    assert app.wallet.taproot and app.settings.accounts == [{"id": "tr:0", "name": "Taproot"}]
    app.render(0)
    # receive: a bc1p (tb1p) address, verified on the signer
    press(app, "KEY_R")
    assert isinstance(app.visible, ReceiveScreen) and app.visible.addr.startswith("tb1p")
    shown = app.visible.addr
    press(app, "KEY_V")
    assert isinstance(app.visible, AddressScreen) and app.visible.addr == shown
    enter(app)
    press(app, "KEY_BACKSPACE")
    # send: the signer's confirm screen names the account
    fund(app.wallet, 80_000)
    app.refresh_balance()
    press(app, "KEY_S")
    type_text(app, DEST)
    enter(app)
    type_text(app, "25000")
    enter(app)
    enter(app)
    assert isinstance(app.visible, ConfirmSendScreen) and app.visible.title == "TAPROOT #0"
    assert app.visible.s["spend"] == 25_000


def test_second_segwit_account_and_hide(app):  # noqa: F811
    create_wallet(app)
    press(app, "KEY_W")
    goto(app, "NEW ACCOUNT")
    goto(app, "CUSTOM SEGWIT")
    for _ in range(10):
        press(app, "KEY_BACKSPACE")
    type_text(app, "Spending")
    enter(app)
    assert app.wallet_id == "wpkh:1" and app.wallet_name == "Spending"
    assert app.wallet.receive_address()[1].startswith("tb1q")
    press(app, "KEY_W")
    goto(app, "SPENDING")
    goto(app, "HIDE")
    press(app, "KEY_Z")
    enter(app)
    assert app.visible.title == "HIDDEN" and app.settings.accounts == []
    assert app.wallet_id is None


def test_taproot_message_goes_straight_to_bip322(app):  # noqa: F811
    from ui.qrscreens import AnimatedQRScreen
    from ui.secure_flows import SignMessageScreen
    from core import message as M
    from embit.script import address_to_scriptpubkey
    create_wallet(app)
    app.settings.accounts = [{"id": "tr:0", "name": "Taproot"}]
    app.switch_wallet({"account": "tr:0", "name": "Taproot"})
    press(app, "KEY_R")
    addr = app.visible.addr
    press(app, "KEY_M")
    type_text(app, "proof")
    enter(app)
    scr = app.visible
    assert isinstance(scr, SignMessageScreen) and scr.fmt == "bip322" and scr.addr == addr
    press(app, "KEY_Z")
    enter(app)
    out = app.visible
    assert isinstance(out, AnimatedQRScreen)
    assert M.verify(address_to_scriptpubkey(addr), "proof", out.frames[0])


def test_watch_only_export_for_taproot(app):  # noqa: F811
    from ui.flow import export_watch_only
    create_wallet(app)
    app.switch_wallet({"account": "tr:2", "name": "T2"})
    export_watch_only(app)
    desc = app.visible.frames[0]
    assert desc.startswith("tr([") and "/86'/1'/2']" in desc and desc.endswith("/<0;1>/*)")


# ------------------------------------------------------ savings / spending
def test_vault_only_rule_enforced_by_signer(signer, monkeypatch):
    w = wallet_for(signer, "tr:1")
    fund(w, 100_000)
    built = w.build(DEST, 10_000, fee_rate=2)
    with pytest.raises(Exception):
        signer.set_account_rule(pin="0000", account="tr:1", rule="vault")   # wrong PIN
    assert signer.set_account_rule(pin="2468", account="tr:1", rule="vault") == {"tr:1": "vault"}
    monkeypatch.setenv("CZ_FORCE_ONLINE", "1")
    s = signer.describe(built["psbt"])
    assert s["vault_only"] and s["blocked"] and not s["cap_ok"]
    with pytest.raises(SignerError, match="vault-only"):
        signer.sign(built["psbt"], s["digest"])
    monkeypatch.setenv("CZ_FORCE_ONLINE", "0")             # radios off: fine
    s = signer.describe(built["psbt"])
    assert s["cap_ok"] and not s["blocked"]
    spend(signer, w, 10_000, 2)
    # other accounts are unaffected, and the rule survives a lock
    monkeypatch.setenv("CZ_FORCE_ONLINE", "1")
    signer.set_cap(pin="2468", cap_sats=50_000)
    other = wallet_for(signer, "tr:0")
    fund(other, 100_000)
    assert signer.describe(other.build(DEST, 10_000, fee_rate=2)["psbt"])["cap_ok"]
    signer.lock()
    signer.unlock("2468")
    assert signer.account_rules() == {"tr:1": "vault"}
    assert signer.set_account_rule(pin="2468", account="tr:1", rule="normal") == {}


def test_rules_are_per_seed_and_encrypted(signer, tmp_path):
    import glob
    signer.set_account_rule(pin="2468", account="tr:3", rule="vault")
    files = glob.glob(str(tmp_path / "signer" / "rules-*.bin"))
    assert len(files) == 1 and b"tr:3" not in open(files[0], "rb").read()


def test_savings_preset(app, monkeypatch):  # noqa: F811
    from ui.flow import HomeScreen
    from ui.widgets import PinScreen
    create_wallet(app)
    press(app, "KEY_W")
    goto(app, "NEW ACCOUNT")
    goto(app, "SAVINGS")
    scr = app.visible
    assert isinstance(scr, PinScreen) and app.console.stack     # the signer asks
    assert scr.prompt == "TURN VAULT-ONLY ON" and "Taproot #0" in scr.note
    app.render(0)
    type_text(app, "1234")
    enter(app)
    assert app.visible.title == "SAVINGS READY"
    enter(app)
    assert isinstance(app.visible, HomeScreen) and app.wallet_id == "tr:0" and app.vault_only
    assert app.signer.account_rules() == {"tr:0": "vault"}
    app.render(0)
    # Spending: no PIN, everyday
    press(app, "KEY_W")
    goto(app, "NEW ACCOUNT")
    goto(app, "SPENDING")
    assert app.wallet_id == "tr:1" and not app.vault_only
    # the list shows which is which
    press(app, "KEY_W")
    rights = {it[0]: it[2] for it in app.visible.items if len(it) > 2}
    assert rights["SAVINGS"] == "VAULT" and rights["SPENDING"].endswith("TAPROOT")
    # turning the rule off needs the PIN on the signer, too
    goto(app, "SAVINGS")
    goto(app, "VAULT ONLY")
    assert isinstance(app.visible, PinScreen) and app.visible.prompt == "TURN VAULT-ONLY OFF"
    type_text(app, "1234")
    enter(app)
    assert app.signer.account_rules() == {}


def test_savings_cancelled_makes_nothing(app):  # noqa: F811
    create_wallet(app)
    press(app, "KEY_W")
    goto(app, "NEW ACCOUNT")
    goto(app, "SAVINGS")
    press(app, "KEY_ESC")
    assert app.settings.accounts == [] and app.signer.account_rules() == {}


def test_app_cannot_change_rules_directly(app):  # noqa: F811
    create_wallet(app)
    with pytest.raises(AttributeError):
        app.signer.set_account_rule
