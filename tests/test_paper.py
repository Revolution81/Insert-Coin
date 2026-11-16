"""Paper wallets: new keys unrelated to the seed, saved as text (+ PDF)."""
import hashlib
import os
import stat

import pytest
from embit import ec, script
from embit.networks import NETWORKS

from core import paper

from test_app import app, create_wallet, enter, press, settle, type_text  # noqa: F401


def test_wallet_contents_match_the_key():
    secret = hashlib.sha256(b"test").digest()
    w = paper.build(secret, "bitcoin", "segwit")
    pk = ec.PrivateKey(secret)
    assert w["wif"] == pk.wif(NETWORKS["main"]) and w["wif"][0] in "KL"
    assert w["address"] == script.p2wpkh(pk.get_public_key()).address(NETWORKS["main"])
    assert w["address"].startswith("bc1q") and w["hex"] == secret.hex()
    assert w["descriptor"].startswith(f"wpkh({w['wif']})#")
    c = paper.build(secret, "bitcoin", "classic")
    assert c["address"].startswith("1") and c["descriptor"].startswith("pkh(")
    t = paper.build(secret, "signet")
    assert t["address"].startswith("tb1q") and t["wif"][0] == "c"


def test_text_file_layout():
    w = paper.build(paper.random_key(), "signet")
    text = paper.text_file(w)
    for part in ("Insert Coin Generated Paper Wallet", "SIGNET: TEST COINS ONLY",
                 "Deposit address", "Private key (WIF=Wallet Import Format):",
                 "Private key (Hex, 32 bytes):", "importdescriptors", w["descriptor"],
                 "QR Codes (requires UTF-8, Unicode and a white background):",
                 "SMALL AMOUNTS"):
        assert part in text
    assert text.count(w["address"]) == 2 and text.count(w["wif"]) == 3   # + the descriptor
    assert "█" in text and "▀" in text


def test_dice_keys_are_checkable():
    rolls = "6153246" * 15
    assert paper.dice_key(rolls) == hashlib.sha256(rolls.encode()).digest()
    with pytest.raises(paper.PaperError):
        paper.dice_key("123456" * 10)                # too few
    with pytest.raises(paper.PaperError):
        paper.dice_key("7" * 120)                    # not dice


def test_random_keys_differ_and_mix_the_chip():
    a, b = paper.random_key(), paper.random_key(b"\x11" * 32)
    assert a != b and len(a) == 32


def test_pdf_is_a_pdf():
    w = paper.build(paper.random_key(), "bitcoin", "classic")
    assert paper.pdf(w).startswith(b"%PDF")


def test_paper_wallets_in_the_ui(app):  # noqa: F811
    from ui.flow import settings_screen
    from ui.widgets import MessageScreen
    create_wallet(app)
    settings_screen(app, at="PAPER WALLETS")
    settle(app)
    m = app.visible
    m.sel = [i[0] for i in m.items].index("PAPER WALLETS")
    enter(app)
    assert [i[0] for i in app.visible.items] == ["MAKE NEW", "PRINT"]
    enter(app)                                           # MAKE NEW
    settle(app)
    c = app.console
    assert c.stack and c.top.title == "PAPER WALLETS"     # the signer's own screen
    enter(app)                                           # past the warning
    menu = c.top
    names = [i[0] for i in menu.items]
    menu.sel = names.index("GENERATE WALLET")
    enter(app)
    settle(app)
    assert c.top.title == "PAPER WALLET"
    enter(app)                                           # MAKE ANOTHER
    menu = c.top
    menu.sel = 0
    press(app, "KEY_RIGHT")                              # CLASSIC
    menu.sel = 1
    press(app, "KEY_RIGHT")                              # no PDF
    menu.sel = [i[0] for i in menu.items].index("USE DICE")
    enter(app)
    type_text(app, "3" * 50)
    enter(app)                                           # too few: stays
    assert c.top.title == "USE DICE"
    type_text(app, "4" * 49)
    enter(app)
    settle(app)
    assert c.top.title == "PAPER WALLET"
    c.top.sel = 1
    enter(app)                                           # DONE
    settle(app)
    assert not c.stack
    msg = app.visible
    assert isinstance(msg, MessageScreen) and msg.title == "PAPER WALLETS"
    assert msg.lines[0].startswith("2 paper wallets")

    import core.config
    d = os.path.join(os.path.dirname(core.config.APP_DIR), "signer", "paper")
    files = sorted(os.listdir(d))
    assert len([f for f in files if f.endswith(".txt")]) == 2
    assert len([f for f in files if f.endswith(".pdf")]) == 1
    dice_addr = [f for f in files if f.startswith(("m", "n")) and f.endswith(".txt")]
    assert len(dice_addr) == 1                           # the classic (dice) one
    expected = paper.build(hashlib.sha256(("3" * 50 + "4" * 49).encode()).digest(),
                           "signet", "classic")["address"]
    assert dice_addr[0] == expected + ".txt"
    for f in files:
        assert stat.S_IMODE(os.stat(os.path.join(d, f)).st_mode) == 0o600


def test_card_helper_moves_paper_wallets(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "copy_backup", os.path.join(os.path.dirname(__file__), "..", "tools", "copy_backup.py"))
    cb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cb)
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    monkeypatch.setattr(cb, "PAPER_SRC", str(src))
    monkeypatch.setattr(cb, "PAPER_DST", str(dst))
    monkeypatch.setattr(cb, "PAPER_OLD", str(tmp_path / "no-card"))
    w = paper.build(paper.random_key(), "bitcoin")
    names = paper.save(str(src), w)
    (src / "notes.sh").write_text("echo hi")
    assert cb.main(["x", "paper"]) == 0
    assert sorted(os.listdir(dst)) == names
    assert os.listdir(src) == ["notes.sh"]               # only paper files moved
