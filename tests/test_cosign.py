"""CO-SIGN (COLDCARD's CCC): key C signs a 2-of-3 only inside its rules, and
the rules need C's words (not the PIN) to see, change or remove."""
import pytest
from embit.psbt import PSBT
from embit.script import address_to_scriptpubkey

from core import seed as seedlib
from core.config import Settings
from core.multisig import build_descriptor, cosigner_key
from core.signer import Signer, SignerError
from core.wallet import WalletService
from test_app import app, create_wallet, enter, settle, type_text  # noqa: F401
from test_core import ABANDON, DEST, fake_tx
from test_multisig import new_root, verify_witness
from ui.widgets import ShowWordsScreen, WordEntryScreen

C = "legal winner thank year wave sausage worth useful legal winner thank yellow"


@pytest.fixture
def cc(tmp_path, monkeypatch):
    monkeypatch.setenv("CZ_FORCE_ONLINE", "0")
    s = Signer(str(tmp_path / "s"))
    info = s.setup("1111", ABANDON, "signet")
    st = s.cosign_setup(secret=C, rules={"per_tx": 100_000, "per_day": 150_000})
    b = new_root()
    keys = [info["multisig_key"], st["key"], cosigner_key(b, "signet")]
    entry = s.register_multisig(build_descriptor(2, keys), "Co-Sign")
    w = WalletService(Settings(network="signet"), entry["descriptor"], None, entry["id"],
                      in_memory=True)
    for _ in range(3):
        _, addr = w.receive_address()
        w.record_unconfirmed(fake_tx(address_to_scriptpubkey(addr), 300_000).serialize())
    return s, w, b


def _sign(s, w, sats):
    built = w.build(DEST, sats, 4)
    summary = s.describe(built["psbt"])
    return summary, s.sign(built["psbt"], summary["digest"])


def test_small_payment_signed_by_a_and_c(cc):
    s, w, _ = cc
    summary, out = _sign(s, w, 50_000)
    assert summary["cosign"]["will"] and summary["cosign"]["fingerprint"] == \
        seedlib.fingerprint(seedlib.root_key(C, "", "signet"))
    assert w.signatures(out) == (2, 2)
    tx = w.finalize(out)
    assert verify_witness(out, tx.raw) == 2


def test_big_payment_needs_key_b(cc):
    s, w, b = cc
    summary, out = _sign(s, w, 200_000)
    assert not summary["cosign"]["will"] and "PER-PAYMENT" in summary["cosign"]["why"]
    assert w.signatures(out) == (1, 2)
    p = PSBT.from_string(out)
    assert p.sign_with(b) >= 1                        # B finishes it
    assert verify_witness(p.to_string(), w.finalize(p.to_string()).raw) == 2


def test_per_day_counts_what_c_signed(cc):
    s, w, _ = cc
    first = _sign(s, w, 90_000)[1]
    assert w.signatures(first) == (2, 2)
    w.record_unconfirmed(w.finalize(first).raw)      # so the next one spends other coins
    summary, out = _sign(s, w, 90_000)               # 180k > 150k a day
    assert not summary["cosign"]["will"] and "TODAY" in summary["cosign"]["why"]
    assert w.signatures(out) == (1, 2)


def test_rules_need_key_c_words(cc):
    s, _, _ = cc
    assert s.cosign_rules(words=C)["per_tx"] == 100_000
    assert s.cosign_change(words=C, field="per_tx", value=0)["per_tx"] == 0
    for _ in range(2):
        with pytest.raises(SignerError, match="key C"):
            s.cosign_rules(words=ABANDON)
    with pytest.raises(SignerError, match="locked"):
        s.cosign_rules(words=ABANDON)
    with pytest.raises(SignerError):
        s.public_info()                                # third wrong try locked the signer
    s.unlock("1111")
    s.cosign_remove(words=C)
    assert s.cosign_status() == {"on": False}


def test_setup_checks(tmp_path):
    s = Signer(str(tmp_path))
    s.setup("1111", ABANDON, "signet")
    for bad in (dict(secret=ABANDON, rules={}), dict(secret="nope", rules={}),
                dict(secret=C, rules={"per_tx": -1}), dict(secret=C, rules={"bogus": 1})):
        with pytest.raises(SignerError):
            s.cosign_setup(**bad)
    s.cosign_setup(secret=C, rules={})
    with pytest.raises(SignerError, match="already"):
        s.cosign_setup(secret=C, rules={})
    assert all(b"legal" not in p.read_bytes() for p in tmp_path.iterdir() if p.is_file())


def test_the_app_only_sees_public_keys(app):  # noqa: F811
    from core import signer_service
    create_wallet(app)
    ts = app.signer._local
    for name in ("cosign_setup", "cosign_rules", "cosign_change", "cosign_remove", "seedvault_words"):
        r = signer_service._dispatch(ts, {"method": name, "params": {}})
        assert r["ok"] is False and r["type"] == "BadRequest", name
    r = signer_service._dispatch(ts, {"method": "cosign_status", "params": {}})
    assert r["ok"] and r["result"] == {"on": False}


def _pick(app, label):
    app.visible.sel = [i[0] for i in app.visible.items].index(label)
    enter(app)
    settle(app)


def _write_and_check(app):
    assert isinstance(app.visible, ShowWordsScreen)
    words = list(app.visible.words)
    for _ in range(app.visible.pages):
        enter(app)
    scr = app.visible
    assert isinstance(scr, WordEntryScreen)
    type_text(app, " ".join(words[p - 1] for p in scr.positions))
    enter(app)
    return words


def test_setup_on_the_screens(app):  # noqa: F811
    create_wallet(app)
    got = []
    app.prompt("cosign_prompt", lambda app, r: got.append(r))
    settle(app)
    _pick(app, "SET IT UP")
    _pick(app, "NEW 12 WORDS")
    enter(app)                                       # the note before the words
    c_words = _write_and_check(app)
    assert app.visible.title == "KEY C RULES"
    _pick(app, "PER PAYMENT")
    from test_app import press
    press(app, *["KEY_BACKSPACE"] * 12)
    type_text(app, "0.0005")
    enter(app)
    assert [i[2] for i in app.visible.items][0] == "0.0005 BTC" or "0.0005" in app.visible.items[0][2]
    _pick(app, "SAVE")
    assert app.visible.title == "KEY B"
    _pick(app, "MAKE NEW KEY B")
    enter(app)
    b_words = _write_and_check(app)
    settle(app)
    r = got[0]
    s = app.signer._local.signer
    assert r["c"] == cosigner_key(seedlib.root_key(" ".join(c_words), "", "signet"), "signet")
    assert r["b"] == cosigner_key(seedlib.root_key(" ".join(b_words), "", "signet"), "signet")
    assert r["a"] == s.public_info()["multisig_key"]
    assert s.cosign_rules(words=" ".join(c_words))["per_tx"] == 50_000
