"""Seed XOR (COLDCARD's format): split and rebuild, on the signer."""
import itertools

import pytest

from core import seed as seedlib
from core import seedxor
from test_app import app, create_wallet, enter, settle, type_text  # noqa: F401
from ui.flow import HomeScreen, restore_flow
from ui.widgets import MessageScreen, ShowWordsScreen, WordEntryScreen

# COLDCARD's published example (docs: Seed XOR)
A = ("romance wink lottery autumn shop bring dawn tongue range crater truth ability miss "
     "spice fitness easy legal release recall obey exchange recycle dragon room")
B = ("lion misery divide hurry latin fluid camp advance illegal lab pyramid unaware eager "
     "fringe sick camera series noodle toy crowd jeans select depth lounge")
C = ("vault nominee cradle silk own frown throw leg cactus recall talent worry gadget "
     "surface shy planet purpose coffee drip few seven term squeeze educate")
SEED = ("silent toe meat possible chair blossom wait occur this worth option bag nurse find "
        "fish scene bench asthma bike wage world quit primary indoor")


def test_coldcard_vector_any_order():
    for order in itertools.permutations([A, B, C]):
        assert seedxor.combine(list(order)) == SEED


@pytest.mark.parametrize("words,parts", [(12, 2), (18, 3), (24, 4)])
def test_split_and_combine(words, parts):
    m = seedlib.new_mnemonic(words)
    ps = seedxor.split(m, parts)
    assert len(ps) == parts and all(len(p.split()) == words and seedlib.is_valid(p) for p in ps)
    assert seedxor.combine(ps) == m
    assert seedxor.combine(ps[:-1]) != m if parts > 2 else True


def test_bad_inputs():
    with pytest.raises(seedxor.SeedXorError, match="twice"):
        seedxor.combine([A, A, C])
    with pytest.raises(seedxor.SeedXorError, match="lengths"):
        seedxor.combine([A, "abandon " * 11 + "about"])
    with pytest.raises(seedxor.SeedXorError):
        seedxor.split(SEED, 5)


def test_restore_from_parts_on_the_signer(app):  # noqa: F811
    restore_flow(app)
    m = app.visible
    m.sel = [i[0] for i in m.items].index("SEED XOR")
    enter(app)
    settle(app)
    app.visible.sel = 1                              # 3 PARTS
    enter(app)
    app.visible.sel = 2                              # 24 WORDS
    enter(app)
    for part in (C, A, B):
        assert isinstance(app.visible, WordEntryScreen)
        type_text(app, " ".join(w[:4] for w in part.split()))
        enter(app)
    assert isinstance(app.visible, MessageScreen) and app.visible.title == "SEED XOR"
    enter(app)
    enter(app)                                       # no passphrase
    for _ in range(2):
        type_text(app, "1234")
        enter(app)
    settle(app)
    enter(app)
    assert isinstance(app.visible, HomeScreen)
    assert app.signer._local.signer.reveal_words(pin="1234") == SEED


def test_write_parts_from_split_backup(app):  # noqa: F811
    words = create_wallet(app)
    app.prompt("split_prompt", lambda app, r: None)
    settle(app)
    app.visible.sel = [i[0] for i in app.visible.items].index("SEED XOR 2 PARTS")
    enter(app)
    settle(app)
    type_text(app, "1234")
    enter(app)
    settle(app)
    assert app.visible.title == "SEED XOR"
    enter(app)
    parts = []
    for _ in range(2):
        assert isinstance(app.visible, ShowWordsScreen)
        parts.append(list(app.visible.words))
        for _ in range(app.visible.pages):
            enter(app)
    assert seedxor.combine([" ".join(p) for p in parts]) == " ".join(words)
    for p in parts:                                  # the check: 2 words from each part
        scr = app.visible
        assert isinstance(scr, WordEntryScreen)
        type_text(app, " ".join(p[pos - 1] for pos in scr.positions))
        enter(app)
    assert app.visible.title == "ALL CHECKED"
