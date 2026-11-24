"""SAVE TO USB STICK: only public files are staged, the root side takes only
plain well-named files, and the screens say what to do when there's no
stick or the wrong format."""
import os
import sys

from test_app import app, create_wallet, enter, press, settle  # noqa: F401

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import copy_backup  # noqa: E402


def test_prepare_stages_public_files_only(app):  # noqa: F811
    from core import usbstick
    create_wallet(app)
    app.last_signed_psbt = "cHNidP8BAA=="
    os.makedirs(app.outbox_dir, exist_ok=True)
    with open(os.path.join(app.outbox_dir, "ab" * 32 + ".hex"), "w") as f:
        f.write("0200")
    files = dict(usbstick.prepare(app))
    assert "README.txt" in files and "last-signed.psbt" in files
    assert any(n.startswith("signed-abab") for n in files)
    words = " ".join(app.signer._local.signer.reveal_words(pin="1234").split()[:3]).encode()
    for n in files:
        data = open(os.path.join(usbstick.stage_dir(), n), "rb").read()
        assert words not in data and b"prv" not in data, n


def test_root_side_copies_plain_files_only(tmp_path, monkeypatch):
    src, stick = tmp_path / "src", tmp_path / "stick"
    src.mkdir()
    stick.mkdir()
    (src / "history-a.csv").write_text("date,status\n")
    (src / ".hidden").write_text("x")
    (src / "bad name.txt").write_text("x")
    os.symlink("/etc/passwd", src / "passwd.txt")
    monkeypatch.setattr(copy_backup, "STICK_SRC", str(src))
    monkeypatch.setattr(copy_backup, "_usb_partitions", lambda: ["/dev/sda1"])
    monkeypatch.setattr(copy_backup, "_mounted_at", lambda dev: str(stick))

    class R:
        def __init__(self, out):
            self.stdout, self.returncode, self.stderr = out, 0, ""
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: R("vfat\n" if "TYPE" in cmd else
                                                               "STICK\n" if "LABEL" in cmd else ""))
    assert copy_backup.usbstick() == 0
    assert sorted(os.listdir(stick / "InsertCoin")) == ["history-a.csv"]


def test_screens(app, monkeypatch):  # noqa: F811
    from core import usbstick
    from ui.usbstick_flow import save_to_usb_stick
    create_wallet(app)
    save_to_usb_stick(app)
    press(app, "KEY_Z")
    enter(app)
    settle(app)
    assert app.visible.title == "SAVED" and "InsertCoin" in " ".join(app.visible.lines)
    enter(app)

    def none():
        raise usbstick.USBStickError("no-stick")
    monkeypatch.setattr(usbstick, "save", none)
    save_to_usb_stick(app)
    press(app, "KEY_Z")
    enter(app)
    settle(app)
    assert app.visible.title == "USB STICK" and "No USB stick" in app.visible.lines[0]
