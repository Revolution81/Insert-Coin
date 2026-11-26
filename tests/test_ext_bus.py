"""EXT header pins 1 and 3: NFC Cap, a secure chip on a bus of its own, or
the NFC Unit (tools/ext_bus.py, SETUP › DEVICE › HARDWARE › EXT HEADER)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import ext_bus  # noqa: E402

BASE = ("# M5Stack's config\ndtparam=audio=on\n[cm4]\notg_mode=1\n\n"
        "# Insert Coin: Unit NFC on its own I2C bus (EXT pin 3 SDA, pin 1 SCL)\n"
        "gpio=23,26=pu\n"
        "dtoverlay=i2c-gpio,bus=3,i2c_gpio_sda=23,i2c_gpio_scl=26,i2c_gpio_delay_us=10\n")


def test_chip_mode_adds_one_block_under_all():
    out = ext_bus.render(BASE, "chip")
    assert out.count("dtoverlay=i2c-gpio,bus=3") == 1        # the old --nfc-unit lines went
    block = out[out.index(ext_bus.BEGIN):]
    assert block.splitlines()[1] == "[all]"                    # not inside [cm4]
    assert "otg_mode=1" in out and "dtparam=audio=on" in out   # M5Stack's lines kept
    assert ext_bus.render(out, "chip") == out                  # twice changes nothing


def test_cap_mode_takes_only_our_lines_out():
    out = ext_bus.render(ext_bus.render(BASE, "nfc"), "cap")
    assert "i2c-gpio" not in out and ext_bus.BEGIN not in out
    assert out.startswith("# M5Stack's config\ndtparam=audio=on\n[cm4]\notg_mode=1\n")


def test_apply_backs_up_and_refuses_junk(tmp_path, monkeypatch):
    cfg = tmp_path / "config.txt"
    cfg.write_text(BASE)
    monkeypatch.setattr(ext_bus, "RULE", str(tmp_path / "63.rules"))
    monkeypatch.setattr(ext_bus, "DROPIN_DIR", str(tmp_path / "dropin"))
    monkeypatch.setattr(ext_bus.subprocess, "run", lambda *a, **k: None)
    assert ext_bus.apply("chip", str(cfg)) == "changed"
    assert (tmp_path / "config.txt.insertcoin-bak").read_text() == BASE
    assert 'GROUP="i2c"' in (tmp_path / "63.rules").read_text()     # the signer's, not the app's
    assert "DeviceAllow=/dev/i2c-3 rw" in (tmp_path / "dropin" / "ext-i2c.conf").read_text()
    assert ext_bus.current(cfg.read_text()) == "chip"
    ext_bus.apply("nfc", str(cfg))
    assert 'GROUP="czapp"' in (tmp_path / "63.rules").read_text()
    import pytest
    with pytest.raises(ValueError):
        ext_bus.apply("rm -rf", str(cfg))


def test_the_helper_only_takes_known_words():
    import copy_backup
    assert copy_backup.main(["cz-backup", "extbus", "../../etc"]) != 0


def test_the_chip_tries_its_own_bus_first(monkeypatch):
    from core.hw import atecc
    monkeypatch.delenv("CZ_ATECC_BUS", raising=False)
    monkeypatch.setattr(atecc.os.path, "exists", lambda p: p in ("/dev/i2c-3", "/dev/i2c-1"))
    assert atecc.chip_buses() == ["/dev/i2c-3", "/dev/i2c-1"]
    monkeypatch.setattr(atecc.os.path, "exists", lambda p: p == "/dev/i2c-1")
    assert atecc.chip_buses() == ["/dev/i2c-1"]


def test_hardware_menu_has_the_ext_row(tmp_path):
    from test_app import create_wallet  # noqa: F401
    from ui.hardware_flow import EXT_LABELS, ext_mode
    app = type("A", (), {})()
    assert ext_mode(app) == "cap" and EXT_LABELS["chip"][0] == "SECURE CHIP"
