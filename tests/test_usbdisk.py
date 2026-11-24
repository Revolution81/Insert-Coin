"""USB DISK: only public files are staged; the root helper takes only plain,
well-named files; the screen attaches and removes the stick."""
import os
import sys

from test_app import app, create_wallet, enter, settle  # noqa: F401

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import usb_disk  # noqa: E402


def test_prepare_stages_public_files_only(app):  # noqa: F811
    from core import usbdisk
    create_wallet(app)
    app.last_signed_psbt = "cHNidP8BAA=="
    os.makedirs(app.outbox_dir, exist_ok=True)
    with open(os.path.join(app.outbox_dir, "ab" * 32 + ".hex"), "w") as f:
        f.write("0200")
    files = dict(usbdisk.prepare(app))
    assert "README.txt" in files and "last-signed.psbt" in files
    assert any(n.startswith("signed-abab") for n in files)
    assert any(n.startswith("watch-only-") for n in files)
    words = " ".join(app.signer._local.signer.reveal_words(pin="1234").split()[:3]).encode()
    for n in files:
        data = open(os.path.join(usbdisk.stage_dir(), n), "rb").read()
        assert words not in data and b"prv" not in data, n
    usbdisk.stop()
    assert not os.path.exists(usbdisk.stage_dir())


def test_helper_takes_only_plain_files(tmp_path, monkeypatch):
    monkeypatch.setattr(usb_disk, "SRC", str(tmp_path))
    (tmp_path / "history-a.csv").write_text("date,status\n")
    (tmp_path / ".hidden").write_text("x")
    (tmp_path / "bad name.txt").write_text("x")
    os.symlink("/etc/passwd", tmp_path / "passwd.txt")
    (tmp_path / "sub").mkdir()
    assert [n for n, _ in usb_disk.staged()] == ["history-a.csv"]


def test_screen_on_and_off(app):  # noqa: F811
    from ui.usbdisk_flow import UsbDiskScreen, usb_disk as start
    create_wallet(app)
    start(app)
    from test_app import press
    press(app, "KEY_Z")                              # SHOW IT
    enter(app)
    settle(app)
    scr = app.visible
    assert isinstance(scr, UsbDiskScreen) and scr.files
    app.render(0)
    enter(app)
    assert app.visible.title == "USB DISK OFF"
