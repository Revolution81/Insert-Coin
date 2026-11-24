"""NOTES & PASSWORDS: kept by the signer, encrypted per seed, never sent to
the app; BIP85 passwords match the spec; card backups carry them."""
import os

import pytest
from embit import bip32

from core import notes as nl
from core.signer import Signer, SignerError
from test_app import app, create_wallet, enter, press, settle, type_text  # noqa: F401
from ui.notes_flow import NoteScreen, PasswordScreen
from ui.widgets import MenuScreen

ROOT = bip32.HDKey.from_string(
    "xprv9s21ZrQH143K2LBWUUQRFXhucrQqBpKdRRxNVq2zBqsx8HVqFk2uYo8kmbaLLHRdqtQpUm98uKfu3vca1LqdGhUtyoFnCNkfmXRyPXLjbKb")
WORDS = "abandon " * 11 + "about"


def test_bip85_password_vectors():
    assert nl.bip85_password(ROOT, 21, 0) == "dKLoepugzdVJvdL56ogNV"
    assert nl.bip85_password(ROOT, 12, 0, "base85") == "_s`{TW89)i4`"
    for bad in ((19, 0), (87, 0), (21, -1)):
        with pytest.raises(nl.NoteError):
            nl.bip85_password(ROOT, *bad)


def test_clean_and_merge():
    e = nl.clean({"kind": "password", "title": "Mail", "password": "hunter2", "evil": "x"})
    assert "evil" not in e and e["made"] > 0
    for bad in ({"kind": "x", "title": "a"}, {"kind": "note", "title": ""},
                {"kind": "password", "title": "a"}, {"kind": "note", "title": "a" * 40}):
        with pytest.raises(nl.NoteError):
            nl.clean(bad)
    mine = [e]
    merged, added = nl.merge(mine, [dict(e, made=1), {"kind": "note", "title": "New", "text": "hi"},
                                    {"kind": "bad"}])
    assert added == 1 and len(merged) == 2
    pw = nl.random_password(20)
    assert len(pw) == 20 and any(c.isdigit() for c in pw)


def test_signer_keeps_them_encrypted_per_seed(tmp_path):
    s = Signer(str(tmp_path))
    s.setup("1234", WORDS, "signet")
    s.note_save(entry={"kind": "note", "title": "Safe", "text": "combination 12-34-56"})
    assert s.notes()[0]["text"] == "combination 12-34-56"
    for name in os.listdir(tmp_path):
        p = tmp_path / name
        if p.is_file():
            assert b"combination" not in p.read_bytes()
    s.note_save(entry={"kind": "note", "title": "Safe", "text": "changed"}, index=0)
    assert s.notes()[0]["text"] == "changed" and len(s.notes()) == 1
    s.note_delete(0)
    assert s.notes() == []
    with pytest.raises(SignerError):
        s.note_delete(0)
    s.lock()
    with pytest.raises(SignerError):
        s.notes()


def test_card_backup_carries_notes(tmp_path):
    a = Signer(str(tmp_path / "a"))
    a.setup("1234", WORDS, "signet")
    a.note_save(entry={"kind": "password", "title": "Bank", "password": "s3cret!"})
    blob = a.make_backup(app_data={})["blob"]
    b = Signer(str(tmp_path / "b"))
    b.setup("1234", WORDS, "signet")
    r = b.apply_backup(b.open_backup(blob_b64=blob))
    assert r["notes"] == 1 and b.notes()[0]["password"] == "s3cret!"
    assert b.apply_backup(b.open_backup(blob_b64=blob))["notes"] == 0       # no duplicates


def test_the_app_cannot_read_them(app):  # noqa: F811
    from core import signer_service
    create_wallet(app)
    ts = app.signer._local
    for name in ("notes", "note_save", "note_delete", "bip85_password"):
        r = signer_service._dispatch(ts, {"method": name, "params": {}})
        assert r["ok"] is False and r["type"] == "BadRequest", name


def test_screens_add_view_edit_delete(app):  # noqa: F811
    create_wallet(app)
    got = []
    app.prompt("notes_prompt", lambda app, r: got.append(r))
    settle(app)
    m = app.visible
    assert isinstance(m, MenuScreen) and m.items[0][0] == "+ NEW NOTE"
    enter(app)                                       # + NEW NOTE
    type_text(app, "Locker")
    enter(app)
    type_text(app, "Code 4821 row B")
    enter(app)
    assert app.visible.title == "SAVED"
    enter(app)
    m = app.visible
    assert m.items[0][0] == "Locker" and m.items[0][2] == "NOTE"
    m.sel = 0
    enter(app)                                       # opens straight into the note
    assert isinstance(app.visible, NoteScreen) and "Code 4821 row B" in " ".join(app.visible.lines)
    enter(app)
    app.visible.sel = 2                              # DELETE
    enter(app)
    press(app, "KEY_Z")                              # YES
    enter(app)
    assert app.visible.items[0][0] == "+ NEW NOTE"
    # a generated password
    app.visible.sel = 1
    enter(app)                                       # + NEW PASSWORD
    type_text(app, "Email")
    enter(app)
    type_text(app, "me@example.com")
    enter(app)
    enter(app)                                       # no site
    enter(app)                                       # GENERATE ONE
    enter(app)                                       # SAVED
    m = app.visible
    assert m.items[0][0] == "Email" and m.items[0][2] == "PW"
    m.sel = 0
    enter(app)
    assert isinstance(app.visible, PasswordScreen) and len(app.visible.e["password"]) == 20
    enter(app)
    press(app, "KEY_BACKSPACE")
    # BIP85
    app.visible.sel = [i[0] for i in app.visible.items].index("BIP85 PASSWORD")
    enter(app)
    enter(app)                                       # number 0
    assert isinstance(app.visible, PasswordScreen)
    assert app.visible.e["password"] == app.signer._local.signer.bip85_password(21, 0)
    enter(app)
    press(app, "KEY_BACKSPACE")
    app.visible.sel = [i[0] for i in app.visible.items].index("DONE")
    enter(app)
    settle(app)
    assert got == [None]                             # the app learns nothing


def test_edit_goes_back_to_the_list_with_the_change(app):  # noqa: F811
    create_wallet(app)
    s = app.signer._local.signer
    s.note_save(entry={"kind": "note", "title": "Old", "text": "one"})
    app.prompt("notes_prompt", lambda app, r: None)
    settle(app)
    enter(app)                                       # open "Old" (shows the note)
    enter(app)                                       # close the note
    app.visible.sel = 1                              # EDIT
    enter(app)
    press(app, *["KEY_BACKSPACE"] * 3)
    type_text(app, "New")
    enter(app)
    enter(app)                                       # keep the text
    assert app.visible.title == "SAVED"
    enter(app)
    assert app.visible.title == "NOTES & PASSWORDS" and app.visible.items[0][0] == "New"
    assert s.notes()[0]["text"] == "one" and len(s.notes()) == 1
