"""18-word seeds: made, typed, split, derived; SeedQR says it can't."""
import pytest

from core import seed as seedlib
from core import split as splitlib
from test_app import app, enter, press, type_text  # noqa: F401
from ui.flow import HomeScreen
from ui.widgets import MenuScreen, MessageScreen, ShowWordsScreen, WordEntryScreen


def test_new_and_dice_only_18():
    m = seedlib.new_mnemonic(18)
    assert len(m.split()) == 18 and seedlib.is_valid(m)
    with pytest.raises(ValueError):
        seedlib.dice_only_mnemonic("1" * 74, 18)
    rolls = "123456" * 13                                 # 78 rolls
    d = seedlib.dice_only_mnemonic(rolls, 18)
    import hashlib
    from embit import bip39
    assert d == bip39.mnemonic_from_bytes(hashlib.sha256(rolls.encode()).digest()[:24])


def test_create_18_words_on_the_signer(app):  # noqa: F811
    enter(app)                                       # CREATE NEW WALLET
    menu = app.visible
    assert isinstance(menu, MenuScreen) and [i[0] for i in menu.items][:3] == \
        ["12 WORDS", "18 WORDS", "24 WORDS"]
    menu.sel = 1
    enter(app)
    assert isinstance(app.visible, ShowWordsScreen) and len(app.visible.words) == 18
    words = list(app.visible.words)
    for _ in range(app.visible.pages):
        enter(app)
    assert isinstance(app.visible, WordEntryScreen)
    for p in app.visible.positions:
        type_text(app, words[p - 1] + " ")
    enter(app)                                       # no passphrase
    type_text(app, "1234")
    enter(app)
    type_text(app, "1234")
    enter(app)
    enter(app)                                       # READY!
    assert isinstance(app.visible, HomeScreen)
    # SeedQR is only defined for 12 and 24 words: said plainly
    from test_app import settle
    app.prompt("seedqr_prompt", lambda app, r: None)
    settle(app)
    type_text(app, "1234")
    enter(app)
    settle(app)
    assert isinstance(app.visible, MessageScreen) and app.visible.title == "NO SEEDQR"


def test_restore_18_words(app):  # noqa: F811
    m = seedlib.new_mnemonic(18)
    from ui.flow import restore_flow
    restore_flow(app)
    app.visible.sel = 1                              # 18 WORDS
    enter(app)
    assert isinstance(app.visible, WordEntryScreen) and len(app.visible.positions) == 18
    for w in m.split():
        type_text(app, w + " ")
    enter(app)
    enter(app)                                       # no passphrase
    type_text(app, "1234")
    enter(app)
    type_text(app, "1234")
    enter(app)
    enter(app)
    assert isinstance(app.visible, HomeScreen)
    assert app.signer._local.signer.reveal_words(pin="1234") == m


def test_split_18_words_rebuilds():
    m = seedlib.new_mnemonic(18)
    shares = splitlib.split(m, 2, 3)
    assert all(len(s) == 27 for s in shares)
    assert splitlib.combine(shares[1:]) == m
