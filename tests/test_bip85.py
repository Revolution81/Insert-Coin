"""BIP85 child seeds, shown only on the signer's screen."""
import pytest
from embit import bip32

from core import bip85
from core.signer import Signer
from ui.widgets import MenuScreen, PinScreen, ShowWordsScreen

from test_app import app, create_wallet, enter, press, type_text  # noqa: F401

ROOT = bip32.HDKey.from_string(
    "xprv9s21ZrQH143K2LBWUUQRFXhucrQqBpKdRRxNVq2zBqsx8HVqFk2uYo8kmbaLLHRdqtQpUm98uKfu3vca1LqdGhUtyoFnCNkfmXRyPXLjbKb")


def test_bip85_vectors():
    assert bip85.child_mnemonic(ROOT, 12, 0) == \
        "girl mad pet galaxy egg matter matrix prison refuse sense ordinary nose"
    assert bip85.child_mnemonic(ROOT, 24, 0) == (
        "puppy ocean match cereal symbol another shed magic wrap hammer bulb intact "
        "gadget divorce twin tonight reason outdoor destroy simple truth cigar social volcano")
    assert bip85.child_mnemonic(ROOT, 12, 1) != bip85.child_mnemonic(ROOT, 12, 0)
    for bad in ((18, 0), (12, -1), (12, 2 ** 31)):
        with pytest.raises(ValueError):
            bip85.child_mnemonic(ROOT, *bad)


def test_signer_needs_pin(tmp_path):
    from core.vault import WrongPin
    s = Signer(str(tmp_path))
    s.setup(pin="1234", mnemonic="abandon " * 11 + "about", network="signet")
    with pytest.raises(WrongPin):
        s.child_seed(pin="9999", words=12, index=0)
    m = s.child_seed(pin="1234", words=12, index=3)
    assert len(m.split()) == 12 and m == s.child_seed(pin="1234", words=12, index=3)


def test_child_seed_screens(app):  # noqa: F811
    create_wallet(app)
    from ui.flow import settings_screen
    settings_screen(app, at="CHILD SEEDS")                              # SETUP
    menu = app.visible
    menu.sel = [it[0] for it in menu.items].index("CHILD SEEDS")
    enter(app)
    assert app.visible.title == "CHILD SEEDS" and app.console.stack
    enter(app)                                        # 12 WORDS
    press(app, "KEY_BACKSPACE")
    type_text(app, "7")
    enter(app)
    assert isinstance(app.visible, PinScreen) and "#7" in app.visible.note
    type_text(app, "1234")
    enter(app)
    scr = app.visible
    assert isinstance(scr, ShowWordsScreen) and scr.title == "CHILD #7"
    want = app.signer.local.child_seed(pin="1234", words=12, index=7).split()
    assert scr.words == want
    for _ in range(3):
        enter(app)
    assert isinstance(app.visible, MenuScreen) and app.visible.title == "CHILD #7"
    app.render(0)
    enter(app)                                        # DONE
    assert not app.console.stack
    with pytest.raises(AttributeError):               # the app can't ask for it directly
        app.signer.child_seed
