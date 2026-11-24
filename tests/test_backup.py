"""Card backup: accounts, multisig, rules, labels and settings; never the seed."""
import os

import pytest

from core import backup
from core.signer import Signer, SignerError
from ui.widgets import MenuScreen

from test_app import app, create_wallet, enter, press, settle, type_text  # noqa: F401
from test_core import ABANDON, NET


@pytest.fixture
def signer(tmp_path):
    s = Signer(str(tmp_path / "signer"))
    s.setup("2468", ABANDON, NET)
    return s


def test_seal_roundtrip_and_tamper(signer):
    r = signer.make_backup(app_data={"settings": {"accounts": []}, "labels": []})
    blob = backup.unb64(r["blob"])
    assert blob.startswith(backup.MAGIC) and len(r["tag"]) == 12
    p = signer.open_backup(blob_b64=r["blob"])
    assert p["network"] == NET and p["app"]["labels"] == []
    bad = bytearray(blob)
    bad[-3] ^= 1
    with pytest.raises(SignerError, match="different seed"):
        signer.open_backup(blob_b64=backup.b64(bytes(bad)))
    with pytest.raises(SignerError):
        signer.open_backup(blob_b64=backup.b64(b"hello"))


def test_other_seed_or_passphrase_cannot_open(signer, tmp_path):
    r = signer.make_backup(app_data={})
    other = Signer(str(tmp_path / "other"))
    other.setup("2468", "zoo " * 11 + "wrong", NET)
    with pytest.raises(SignerError, match="different seed"):
        other.open_backup(blob_b64=r["blob"])
    pp = Signer(str(tmp_path / "pp"))
    pp.setup("2468", ABANDON, NET, passphrase="extra", store_passphrase=True)
    with pytest.raises(SignerError):
        pp.open_backup(blob_b64=r["blob"])
    assert pp.backup_tag() != signer.backup_tag()


def test_no_secrets_inside(signer):
    from core.backup import keys, open_sealed
    r = signer.make_backup(app_data={"settings": {}, "labels": []})
    p = open_sealed(keys(signer._root.secret)[0], backup.unb64(r["blob"]))
    flat = str(p)
    assert "abandon" not in flat and "2468" not in flat and "prv" not in flat


def test_restore_only_adds(signer, tmp_path):
    signer.set_account_rule(pin="2468", account="tr:0", rule="vault")
    r = signer.make_backup(app_data={})
    signer.set_account_rule(pin="2468", account="tr:0", rule="normal")
    p = signer.open_backup(blob_b64=r["blob"])
    p["signer"]["rules"]["tr:1"] = "normal"            # a restore can't loosen
    p["signer"]["rules"]["bogus"] = "vault"
    out = signer.apply_backup(p)
    assert out["rules"] == {"tr:0": "vault"}


def test_files_named_per_seed_and_pruned(tmp_path, monkeypatch):
    import core.config
    monkeypatch.setattr(core.config, "APP_DIR", str(tmp_path / "app"))
    import time as _t
    names = []
    for i in range(7):
        monkeypatch.setattr(backup.time, "strftime", lambda f, i=i: f"20261113-12000{i}")
        names.append(backup.write(backup.MAGIC + b"x" * 40, "abcdef012345"))
    backup.write(backup.MAGIC + b"y" * 40, "999999999999")
    mine = backup.find("abcdef012345")
    assert len(mine) == backup.KEEP and os.path.basename(mine[0]) == names[-1]
    assert len(backup.find()) == backup.KEEP + 1
    del _t


def test_save_and_restore_through_the_screens(app, tmp_path):  # noqa: F811
    from test_core import fund
    from ui.flow import card_backup_menu, settings_screen
    create_wallet(app)
    # some setup worth keeping
    app.settings.accounts = [{"id": "tr:0", "name": "Savings"}]
    app.settings.electrum_url = "ssl://my.node:50002"
    app.settings.save()
    app.signer.local.set_account_rule(pin="1234", account="tr:0", rule="vault")
    fund(app.wallet, 10_000)
    _, addr = app.wallet.receive_address()
    app.wallet.set_label("addr", addr, "from mom")
    card_backup_menu(app)
    enter(app)                                         # SAVE BACKUP
    assert app.visible.title == "BACKUP SAVED"
    files = backup.find(app.signer.backup_tag())
    assert len(files) == 1
    # lose the app side, the rule; then restore
    app.settings.accounts = []
    app.settings.electrum_url = ""
    app.settings.save()
    app.wallet.state["labels"] = {}
    app.signer.local.set_account_rule(pin="1234", account="tr:0", rule="normal")
    app.home()
    settings_screen(app, at="CARD BACKUP")
    menu = app.visible
    menu.sel = [it[0] for it in menu.items].index("CARD BACKUP")
    enter(app)
    press(app, "KEY_X", "KEY_X")                       # RESTORE BACKUP (after VERIFY)
    enter(app)
    assert isinstance(app.visible, MenuScreen) and app.visible.title == "RESTORE FROM"
    enter(app)
    scr = app.visible
    assert scr.title == "RESTORE BACKUP?" and app.console.stack     # on the signer
    assert any("Savings" in line for line in scr.lines)
    app.render(0)
    press(app, "KEY_Z")
    enter(app)
    assert app.visible.title == "RESTORED"
    assert app.settings.accounts == [{"id": "tr:0", "name": "Savings"}]
    assert app.settings.electrum_url == "ssl://my.node:50002"
    assert app.signer.account_rules() == {"tr:0": "vault"}
    assert app.wallet.label("addr", addr) == "from mom"


def test_multisig_comes_back(signer):
    from embit import bip32
    from core import multisig as ms
    mine = signer.public_info()["multisig_key"]
    others = [ms.cosigner_key(bip32.HDKey.from_seed(bytes([i]) * 32), NET) for i in (1, 2)]
    desc = ms.build_descriptor(2, [mine] + others)
    signer.register_multisig(desc, "Family")
    r = signer.make_backup(app_data={})
    signer.remove_multisig(wallet_id=signer.list_multisig()[0]["id"])
    p = signer.open_backup(blob_b64=r["blob"])
    assert "0 multisig" not in " ".join(backup.summary(p))
    out = signer.apply_backup(p)
    assert out["multisig"] == 1 and signer.list_multisig()[0]["name"] == "Family"


def test_offered_after_restoring_from_words(app):  # noqa: F811
    from ui.flow import card_backup_menu
    from ui.widgets import ConfirmScreen
    words = create_wallet(app)
    app.settings.accounts = [{"id": "tr:0", "name": "Savings"}]
    app.settings.save()
    card_backup_menu(app)
    enter(app)                                         # SAVE BACKUP
    app.signer.local.wipe(pin="1234")
    app.settings.accounts = []
    app.reset()
    press(app, "KEY_X")                                # RESTORE...
    enter(app)
    enter(app)                                         # 12 WORDS
    for w in words:
        type_text(app, w[:4] + " ")
    enter(app)
    enter(app)                                         # NO PASSPHRASE
    type_text(app, "1234")
    enter(app)
    type_text(app, "1234")
    enter(app)
    assert app.visible.title == "READY!"
    enter(app)
    assert isinstance(app.visible, ConfirmScreen) and app.visible.title == "BACKUP FOUND"
    press(app, "KEY_Z")
    enter(app)                                         # RESTORE (app side)
    press(app, "KEY_Z")
    enter(app)                                         # RESTORE (signer)
    assert app.visible.title == "RESTORED"
    assert app.settings.accounts == [{"id": "tr:0", "name": "Savings"}]


def test_root_copy_helper(tmp_path, monkeypatch):
    import importlib.util
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location("cb", os.path.join(root, "tools", "copy_backup.py"))
    cb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cb)
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    monkeypatch.setattr(cb, "SRC", str(src))
    monkeypatch.setattr(cb, "DST", str(dst))
    good = "czw-abcdef012345-20261113-120000.bak"
    (src / good).write_bytes(backup.MAGIC + b"x" * 50)
    assert cb.main(["x", good]) == 0 and (dst / good).exists()
    assert cb.main(["x", "../../etc/shadow"]) == 2
    assert cb.main(["x", "czw-abcdef012345-20261113-120000.bak; rm -rf /"]) == 2
    # a symlink planted by the app is never followed
    secret = tmp_path / "vault.json"
    secret.write_bytes(backup.MAGIC + b"SECRET")
    link = "czw-abcdef012345-20261113-120001.bak"
    os.symlink(secret, src / link)
    with pytest.raises(OSError):
        cb.main(["x", link])
    assert not (dst / link).exists()
    # not a sealed backup: refused
    plain = "czw-abcdef012345-20261113-120002.bak"
    (src / plain).write_bytes(b"just text")
    assert cb.main(["x", plain]) == 2
