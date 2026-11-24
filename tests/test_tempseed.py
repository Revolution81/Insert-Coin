"""TEMPORARY SEED and the SEED VAULT: another seed until LOCK, extra seeds
kept encrypted with the main one, nothing secret ever sent to the app."""
import os

import pytest

from core import seed as seedlib
from core import seedxor
from core.signer import Signer, SignerError
from test_app import app, create_wallet, enter, press, settle, type_text  # noqa: F401
from ui.flow import HomeScreen
from ui.widgets import MenuScreen, WordEntryScreen

PIN = "1234"
MAIN = "abandon " * 11 + "about"
OTHER = "legal winner thank year wave sausage worth useful legal winner thank yellow"
THIRD = "letter advice cage absurd amount doctor acoustic avoid letter advice cage above"


def fp(words, pp="", net="signet"):
    return seedlib.fingerprint(seedlib.root_key(words, pp, net))


@pytest.fixture
def s(tmp_path):
    s = Signer(str(tmp_path))
    s.setup(PIN, MAIN, "signet")
    return s


def test_temporary_until_lock(s):
    main = s.public_info()
    info = s.use_temporary(secret=OTHER, label="old")
    assert info["fingerprint"] == fp(OTHER) != main["fingerprint"]
    assert info["temporary"] == {"label": "old"} and s.status()["temporary"]["label"] == "old"
    assert s.reveal_words(pin=PIN) == OTHER                # same PIN, the seed in use
    with pytest.raises(Exception):
        s.reveal_words(pin="9999")
    s.lock()
    assert s.unlock(PIN)["fingerprint"] == main["fingerprint"]
    assert "temporary" not in s.public_info() and "temporary" not in s.status()


def test_back_to_main_and_passphrase(s):
    main = s.public_info()["fingerprint"]
    assert s.use_temporary(secret=OTHER, passphrase="x")["fingerprint"] == fp(OTHER, "x")
    assert s.use_temporary(secret=THIRD)["fingerprint"] == fp(THIRD)     # switch between temps
    assert s.main_seed()["fingerprint"] == main and s.reveal_words(pin=PIN) == MAIN
    assert s.use_temporary(secret=MAIN)["fingerprint"] == main            # the main seed: no temp
    assert "temporary" not in s.status()
    for bad in ("not words", MAIN.replace("about", "abandon"), 5):
        with pytest.raises(SignerError):
            s.use_temporary(secret=bad)


def test_each_seed_keeps_its_own_notes(s):
    s.note_save(entry={"kind": "note", "title": "Main", "text": "m"})
    s.use_temporary(secret=OTHER)
    assert s.notes() == []
    s.note_save(entry={"kind": "note", "title": "Temp", "text": "t"})
    s.main_seed()
    assert [n["title"] for n in s.notes()] == ["Main"]


def test_xprv_as_temporary(s):
    xprv = seedlib.root_key(OTHER, "", "signet").to_string()
    assert s.use_temporary(secret=xprv)["fingerprint"] == fp(OTHER)
    assert s.reveal_words(pin=PIN) == xprv


def test_seed_vault(s, tmp_path):
    s.seedvault_add(secret=OTHER, passphrase="pp", label="Savings")
    s.seedvault_add(secret=THIRD)
    v = s.seedvault()
    assert [e["label"] for e in v] == ["Savings", fp(THIRD).upper()]
    assert v[0]["kind"] == "12W" and v[0]["passphrase"] and "secret" not in v[0]
    for name in os.listdir(tmp_path):
        if (tmp_path / name).is_file():
            assert b"legal" not in (tmp_path / name).read_bytes()
    for bad in (dict(secret=OTHER, passphrase="pp"), dict(secret=MAIN)):
        with pytest.raises(SignerError):
            s.seedvault_add(**bad)
    info = s.seedvault_use(index=0)
    assert info["fingerprint"] == fp(OTHER, "pp") and info["temporary"]["label"] == "Savings"
    assert s.seedvault()[0]["active"]                       # the vault still opens from a temp
    s.seedvault_rename(index=0, label="Cold")
    assert s.status()["temporary"]["label"] == "Cold"
    s.seedvault_delete(index=1)
    assert len(s.seedvault()) == 1
    s.lock()
    with pytest.raises(SignerError):
        s.seedvault()


def test_card_backup_carries_the_vault(tmp_path):
    a = Signer(str(tmp_path / "a"))
    a.setup(PIN, MAIN, "signet")
    a.seedvault_add(secret=OTHER, label="Old")
    blob = a.make_backup(app_data={})["blob"]
    a.use_temporary(secret=THIRD)
    assert "seedvault" not in a.open_backup(a.make_backup(app_data={})["blob"])["signer"]
    b = Signer(str(tmp_path / "b"))
    b.setup(PIN, MAIN, "signet")
    assert b.apply_backup(b.open_backup(blob_b64=blob))["seedvault"] == 1
    assert b.seedvault()[0]["label"] == "Old"
    assert b.apply_backup(b.open_backup(blob_b64=blob))["seedvault"] == 0


def test_locked_policy_blocks_it(s):
    s.set_bypass(PIN, "7777")
    for call in (lambda: s.use_temporary(secret=OTHER), lambda: s.seedvault(),
                 lambda: s.seedvault_add(secret=OTHER)):
        with pytest.raises(SignerError, match="policy"):
            call()


def test_the_app_cannot_reach_secrets(app):  # noqa: F811
    from core import signer_service
    create_wallet(app)
    ts = app.signer._local
    for name in ("use_temporary", "seedvault", "seedvault_add", "seedvault_use",
                 "seedvault_delete", "temporary_secret"):
        r = signer_service._dispatch(ts, {"method": name, "params": {}})
        assert r["ok"] is False and r["type"] == "BadRequest", name
    assert signer_service._dispatch(ts, {"method": "main_seed", "params": {}})["ok"]


def _open_prompt(app):
    got = []
    app.prompt("temp_seed_prompt", lambda app, r: got.append(r))
    settle(app)
    assert isinstance(app.visible, MenuScreen) and app.visible.title == "TEMPORARY SEED"
    return got


def _pick(app, label):
    app.visible.sel = [i[0] for i in app.visible.items].index(label)
    enter(app)
    settle(app)


def test_screens_type_words_save_and_go_back(app):  # noqa: F811
    from ui.flow import use_temporary
    create_wallet(app)
    main = app.fingerprint
    got = _open_prompt(app)
    _pick(app, "TYPE SEED WORDS")
    _pick(app, "12 WORDS")
    assert isinstance(app.visible, WordEntryScreen)
    type_text(app, " ".join(w[:4] for w in OTHER.split()))
    enter(app)
    _pick(app, "NO PASSPHRASE")
    assert app.visible.title == "USE THIS SEED?" and fp(OTHER).upper() in " ".join(app.visible.info)
    _pick(app, "USE + SAVE IN VAULT")
    type_text(app, "Old")
    enter(app)
    settle(app)
    info = got[0]
    assert info["fingerprint"] == fp(OTHER) and info["temporary"]["label"] == "Old"
    use_temporary(app, info)
    assert app.visible.title == "TEMPORARY SEED"
    enter(app)
    assert isinstance(app.visible, HomeScreen) and app.fingerprint == fp(OTHER)
    assert app.wallet_name == "Temp Old"
    app.render(0)
    # back again, from the vault's IN USE row and BACK TO MAIN SEED
    got = _open_prompt(app)
    assert app.visible.items[0][0] == "BACK TO MAIN SEED"
    assert "SAVE THIS ONE" not in [i[0] for i in app.visible.items]
    _pick(app, "SEED VAULT")
    assert app.visible.items[0][2] == "IN USE"
    press(app, "KEY_BACKSPACE")
    _pick(app, "BACK TO MAIN SEED")
    use_temporary(app, got[0])
    enter(app)
    assert app.fingerprint == main and app.wallet_name == "Main"


def test_screens_seed_xor_parts_and_vault_use(app):  # noqa: F811
    from ui.flow import use_temporary
    create_wallet(app)
    parts = seedxor.split(OTHER, 2)
    got = _open_prompt(app)
    _pick(app, "SEED XOR PARTS")
    _pick(app, "2 PARTS")
    _pick(app, "12 WORDS")
    for p in parts:
        type_text(app, " ".join(w[:4] for w in p.split()))
        enter(app)
    _pick(app, "ADD PASSPHRASE")
    type_text(app, "x")
    enter(app)
    assert "passphrase" in " ".join(app.visible.info)
    _pick(app, "USE IT")
    assert got[0]["fingerprint"] == fp(OTHER, "x")
    use_temporary(app, got[0])
    enter(app)
    # save it from the menu, then open it from the vault after a lock
    got = _open_prompt(app)
    _pick(app, "SAVE THIS ONE")
    press(app, *["KEY_BACKSPACE"] * 8)
    type_text(app, "Xor")
    enter(app)
    assert app.visible.title == "SAVED"
    enter(app)
    s = app.signer._local.signer
    s.main_seed()
    assert s.seedvault()[0]["label"] == "Xor"


def test_child_seed_as_temporary(app):  # noqa: F811
    create_wallet(app)
    s = app.signer._local.signer
    child = s.child_seed(pin=PIN, words=12, index=3)
    got = []
    app.prompt("child_seed_prompt", lambda app, r: got.append(r))
    settle(app)
    enter(app)                                       # 12 WORDS
    press(app, "KEY_BACKSPACE")
    type_text(app, "3")
    enter(app)
    type_text(app, PIN)
    enter(app)
    settle(app)
    for _ in range(app.visible.pages):
        enter(app)
    _pick(app, "USE AS TEMPORARY")
    assert got[0]["temporary"]["temporary"]["label"] == "CHILD #3"
    assert got[0]["temporary"]["fingerprint"] == fp(child)
