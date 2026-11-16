"""
Split (SLIP39) backups: the official test vectors, round trips with every
K-subset of shares, and writing / restoring through the screens.
"""
import itertools
import json
import os

import pytest
import shamir_mnemonic as sm

from core import seed as seedlib
from core import split as splitlib
from ui.flow import HomeScreen
from ui.widgets import MessageScreen, ShowWordsScreen, WordEntryScreen

from test_app import app, create_wallet, enter, press, type_text  # noqa: F401
from test_core import ABANDON

VECTORS = json.load(open(os.path.join(os.path.dirname(__file__), "data", "slip39_vectors.json")))


@pytest.mark.parametrize("vector", VECTORS, ids=[v[0][:40] for v in VECTORS])
def test_official_slip39_vectors(vector):
    """The pinned library must pass every vector from the SLIP39 spec repo."""
    _, mnemonics, secret, _ = vector
    if secret:
        assert sm.combine_mnemonics(mnemonics, b"TREZOR").hex() == secret
    else:
        with pytest.raises(sm.MnemonicError):
            sm.combine_mnemonics(mnemonics, b"TREZOR")


@pytest.mark.parametrize("words,k,n", [(12, 2, 3), (12, 3, 5), (24, 2, 3), (24, 2, 2)])
def test_every_k_subset_rebuilds(words, k, n):
    m = seedlib.new_mnemonic(words)
    shares = splitlib.split(m, k, n)
    assert all(len(s) == splitlib.words_per_share(words) for s in shares)
    for subset in itertools.combinations(shares, k):
        assert splitlib.combine(list(subset)) == m
    if k > 1:
        with pytest.raises(splitlib.SplitError, match="Not enough"):
            splitlib.combine(shares[:k - 1])


def test_share_checks():
    a = splitlib.split(ABANDON, 2, 3)
    b = splitlib.split(ABANDON, 2, 3)            # same seed, new random split
    info = splitlib.check_share(a[1])
    assert (info.index, info.threshold, info.words) == (1, 2, 20)
    bad = list(a[0])
    bad[7] = "academic" if bad[7] != "academic" else "acid"
    with pytest.raises(splitlib.SplitError, match="Checksum"):
        splitlib.check_share(bad)
    with pytest.raises(splitlib.SplitError, match="different"):
        splitlib.combine([a[0], b[1]])
    assert splitlib.completions("acad") == ["academic"]


# ------------------------------------------------------------------- UI
def type_share(app, words):
    for w in words:
        type_text(app, w[:4] + " ")             # SLIP39 words are unique in 4 letters


def open_split_backup(app, pin="1234"):
    press(app, "KEY_X", "KEY_C", "KEY_C")       # SETUP
    enter(app)
    app.visible.sel = [it[0] for it in app.visible.items].index("BACKUP")
    enter(app)                                           # BACKUP
    app.visible.sel = next(i for i, it in enumerate(app.visible.items) if it[0] == "SPLIT BACKUP")
    enter(app)
    enter(app)                                  # 2 OF 3
    type_text(app, pin)
    enter(app)


def test_write_and_check_shares(app):  # noqa: F811
    words = create_wallet(app, pin="1234")
    open_split_backup(app)
    assert app.visible.title == "SPLIT BACKUP"
    enter(app)
    shares = []
    for i in range(3):
        scr = app.visible
        assert isinstance(scr, ShowWordsScreen) and scr.title == f"SHARE {i + 1} OF 3"
        shares.append(list(scr.words))
        app.render(0)
        for _ in range(scr.pages):
            enter(app)
    assert splitlib.combine([shares[0], shares[2]]) == " ".join(words)
    for i in range(3):
        scr = app.visible
        assert isinstance(scr, WordEntryScreen) and scr.title == f"CHECK SHARE {i + 1}"
        app.render(0)
        for p in scr.positions:
            type_text(app, shares[i][p - 1][:4] + " ")
    assert app.visible.title == "ALL CHECKED"


def test_wrong_check_word_shows_share_again(app):  # noqa: F811
    create_wallet(app, pin="1234")
    open_split_backup(app)
    enter(app)
    first = list(app.visible.words)
    for _ in range(3):
        for _ in range(app.visible.pages):
            enter(app)
    scr = app.visible
    wrong = next(w for w in ("acid", "acne") if w != first[scr.positions[0] - 1])
    type_text(app, wrong + " ")
    type_text(app, first[scr.positions[1] - 1][:4] + " ")
    assert app.visible.title == "NOT QUITE"
    enter(app)
    assert isinstance(app.visible, ShowWordsScreen) and app.visible.title == "SHARE 1 OF 3"


def restore_menu(app):
    press(app, "KEY_X")                         # RESTORE FROM WORDS
    enter(app)
    app.visible.sel = next(i for i, it in enumerate(app.visible.items) if it[0] == "SPLIT SHARES")
    enter(app)
    enter(app)                                  # 20-WORD SHARES


def test_restore_from_two_of_three(app):  # noqa: F811
    shares = splitlib.split(ABANDON, 2, 3)
    restore_menu(app)
    type_share(app, shares[2])
    assert app.visible.title == "SHARE 1 OF 2"
    app.render(0)
    enter(app)
    assert app.visible.title == "SHARE 2/2"
    type_share(app, shares[0])
    assert app.visible.title == "PASSPHRASE?"
    enter(app)                                  # NO PASSPHRASE
    type_text(app, "4321")
    enter(app)
    type_text(app, "4321")
    enter(app)
    enter(app)
    assert isinstance(app.visible, HomeScreen) and app.fingerprint == "73c5da0a"


def test_restore_rejects_mixed_and_repeated_shares(app):  # noqa: F811
    a, b = splitlib.split(ABANDON, 2, 3), splitlib.split(ABANDON, 2, 3)
    restore_menu(app)
    type_share(app, a[0])
    assert app.visible.title == "SHARE 1 OF 2"
    enter(app)
    type_share(app, a[0])                       # the same paper again
    assert isinstance(app.visible, WordEntryScreen) and "already" in app.visible.error
    press(app, "KEY_ESC")                       # back to the share-length menu
    assert app.visible.title == "SPLIT SHARES"
    enter(app)
    assert app.visible.title == "SHARE 2/2"         # share 1 is still remembered
    type_share(app, b[1])                       # a paper from a different split
    assert "different" in app.visible.error
    app.render(0)


def test_word_entry_rules(app):  # noqa: F811
    """Regression: 4 letters used to auto-accept and then swallow the next
    word's first letter when it matched the rest of the accepted word."""
    got = []
    scr = WordEntryScreen("T", [1, 2, 3, 4], lambda app, w: got.append(w))
    app.push(scr)
    type_text(app, "abandon")                   # typed in full: taken at once
    assert scr.words == ["abandon"]
    type_text(app, "dove ")                     # starts with the 'd' 'abandon' ends in
    assert scr.words == ["abandon", "dove"]
    type_text(app, "act ")                      # 'act' is also the start of 'action'
    assert scr.words[-1] == "act"
    type_text(app, "acti")
    assert scr.words[-1] == "act" and scr.buf.text == "acti"   # last word waits for OK
    enter(app)
    assert got == [["abandon", "dove", "act", "action"]]
