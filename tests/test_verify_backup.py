"""VERIFY BACKUP: every copy of this seed's card backups is opened by the
signer and reported, nothing restored."""
import os

from test_app import app, create_wallet, enter, settle  # noqa: F401

from ui.widgets import MenuScreen, MessageScreen


def _card_backup_menu(app):
    from ui.flow import card_backup_menu
    card_backup_menu(app)
    assert isinstance(app.visible, MenuScreen)
    return app.visible


def test_verify_all_copies(app, tmp_path, monkeypatch):  # noqa: F811
    card = tmp_path / "card"
    card.mkdir()
    monkeypatch.setenv("CZ_BACKUP_DIRS", str(card))
    create_wallet(app)
    m = _card_backup_menu(app)
    assert [i[0] for i in m.items] == ["SAVE BACKUP", "VERIFY BACKUP", "RESTORE BACKUP"]
    enter(app)                                       # SAVE BACKUP
    settle(app)
    enter(app)                                       # close BACKUP SAVED
    from core import backup
    tag = app.signer.backup_tag()
    local = backup.copies(tag)
    assert local and local[0][0] == "DEVICE"
    # the card copy (what cz-backup makes on the device)
    p = local[0][1]
    (card / os.path.basename(p)).write_bytes(open(p, "rb").read())
    accounts_before = list(app.settings.accounts)
    m.sel = 1
    enter(app)                                       # VERIFY BACKUP
    settle(app)
    v = app.visible
    assert isinstance(v, MessageScreen) and v.title == "BACKUP CHECK" and v.border == "green"
    assert sum(1 for line in v.lines if line.startswith("OK")) == 2
    enter(app)
    settle(app)
    assert app.settings.accounts == accounts_before   # nothing restored

    # a damaged card copy is named, the device copy still OK
    data = bytearray((card / os.path.basename(p)).read_bytes())
    data[-5] ^= 0x01
    (card / os.path.basename(p)).write_bytes(bytes(data))
    m.sel = 1
    enter(app)
    settle(app)
    v = app.visible
    assert v.border == "red"
    assert any(line.startswith("BAD") and "CARD" in line for line in v.lines)
    assert any(line.startswith("OK") and "DEVICE" in line for line in v.lines)


def test_verify_with_no_backup(app, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("CZ_BACKUP_DIRS", str(tmp_path / "none"))
    create_wallet(app)
    _card_backup_menu(app)
    app.visible.sel = 1
    enter(app)
    assert app.visible.title == "NO BACKUP"
