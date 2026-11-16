"""Message signing: classic (Electrum/BIP137) and BIP322, on the signer's screen."""
import base64

import pytest
from embit import ec, script

from core import message as M
from ui.flow import ReceiveScreen
from ui.qrscreens import AnimatedQRScreen
from ui.secure_flows import SignMessageScreen

from test_app import app, create_wallet, enter, press, type_text  # noqa: F401

# BIP322's own test key and vectors
KEY = ec.PrivateKey.from_wif("L3VFeEujGtevx9w18HD1fhRbCH67Az2dpCymeRE1SoPK6XQtaN2k")
SPK = script.p2wpkh(KEY.get_public_key())


def test_bip322_vectors():
    assert SPK.address() == "bc1q9vza2e8x573nczrlzms0wvx3gsqjx7vavgkx0l"
    assert M.bip322_hash("").hex() == \
        "c90c269c4f8fcbe6880f72a721ddfbf1914268a794cbb21cfafee13770ae19f1"
    assert M.bip322_hash("Hello World").hex() == \
        "f0eb03b1a75ac6d9847f55c624a99169b5dccba2a31f5b23bea77ba270de0a7a"
    spend, sign = M.bip322_txs(SPK, "Hello World")
    assert spend.txid().hex() == "b79d196740ad5217771c1098fc4a4b51e0535c32236c71f1ea4d61a2d603352b"
    assert sign.txid().hex() == "88737ae86f2077145f93cc4b153ae9a1cb8d56afa511988c149c5c8c9d93bddf"
    want = ("AkcwRAIgZRfIY3p7/DoVTty6YZbWS71bc5Vct9p9Fia83eRmw2QCICK/ENGfwLtptFluMGs2KsqoNSk89pO7"
            "F29zJLUx9a/sASECx/EgAxlkQpQ9hYjgGu6EBCPMVPwVIVJqO4XCsMvViHI=")
    assert M.sign_bip322(KEY, "Hello World") == want
    assert M.verify_bip322(SPK, "Hello World", want)
    assert not M.verify_bip322(SPK, "Hello World!", want)


def test_classic_formats():
    e = M.sign(KEY, "Hello World", "electrum")
    b = M.sign(KEY, "Hello World", "bip137")
    he, hb = base64.b64decode(e)[0], base64.b64decode(b)[0]
    assert 31 <= he <= 34 and 39 <= hb <= 42 and base64.b64decode(e)[1:] == base64.b64decode(b)[1:]
    for sig in (e, b):
        assert M.verify(SPK, "Hello World", sig)
        assert not M.verify(SPK, "Hello world", sig)
    other = script.p2wpkh(ec.PrivateKey(b"\x01" * 32).get_public_key())
    assert not M.verify(other, "Hello World", e)


def test_text_rules():
    for bad in ("", "x" * (M.MAX_LEN + 1), "tab\there", "bell\x07"):
        with pytest.raises(M.MessageError):
            M.check_text(bad)
    assert M.check_text("two\nlines ok") == "two\nlines ok"
    with pytest.raises(M.MessageError):
        M.sign(KEY, "hi", "pgp")


def test_sign_message_on_the_signer(app):  # noqa: F811
    create_wallet(app)
    press(app, "KEY_R")
    addr = app.visible.addr
    press(app, "KEY_M")
    type_text(app, "I own this address 2026")
    enter(app)
    assert app.visible.title == "FORMAT"
    enter(app)                                        # STANDARD
    scr = app.visible
    assert isinstance(scr, SignMessageScreen) and app.console.stack
    assert scr.addr == addr and "I own this address 2026" in " ".join(scr.lines)
    app.render(0)
    enter(app)                                        # cursor starts on NO
    assert isinstance(app.visible, ReceiveScreen)
    # again, and say YES
    press(app, "KEY_M")
    type_text(app, "I own this address 2026")
    enter(app)
    press(app, "KEY_X")
    press(app, "KEY_X")                               # BIP322
    enter(app)
    press(app, "KEY_Z")
    enter(app)
    out = app.visible
    assert isinstance(out, AnimatedQRScreen) and out.title == "SIGNATURE"
    spk = script.address_to_scriptpubkey(addr)
    assert M.verify_bip322(spk, "I own this address 2026", out.frames[0])
    app.render(0)
    press(app, "KEY_TAB")
    assert "BEGIN BITCOIN SIGNED MESSAGE" in app.visible.frames[0] and addr in app.visible.frames[0]


def test_long_message_must_be_read_first(app):  # noqa: F811
    create_wallet(app)
    results = []
    text = "\n".join(f"line {i}" for i in range(9))
    app.prompt("message_prompt", lambda a, r: results.append(r), index=0, message=text)
    from test_app import settle
    settle(app)
    scr = app.visible
    assert isinstance(scr, SignMessageScreen) and not scr.seen_all
    press(app, "KEY_Z")                               # YES...
    enter(app)                                        # ...does nothing yet
    assert app.visible is scr and results == []
    for _ in range(6):
        press(app, "KEY_X")                           # scroll to the end
    assert scr.seen_all
    scr.sel = 0
    enter(app)
    assert results and M.verify(script.address_to_scriptpubkey(results[0]["address"]),
                                text, results[0]["signature"])


def test_app_cannot_sign_messages_directly(app):  # noqa: F811
    create_wallet(app)
    with pytest.raises(AttributeError):
        app.signer.sign_message
