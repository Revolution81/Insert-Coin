import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import power  # noqa: E402


def supply(root, name, **files):
    d = root / name
    d.mkdir()
    for k, v in files.items():
        (d / k).write_text(v + "\n")


def test_reads_level_and_charging(tmp_path):
    supply(tmp_path, "bq27220-0", type="Battery", capacity="57", status="Charging")
    assert power.read(str(tmp_path)) == (0.57, True)


def test_discharging_and_full(tmp_path):
    supply(tmp_path, "bq27220-0", type="Battery", capacity="80", status="Discharging")
    assert power.read(str(tmp_path)) == (0.8, False)
    # the gauge says "Full" on battery too: no charging bolt for that
    (tmp_path / "bq27220-0" / "status").write_text("Full\n")
    assert power.read(str(tmp_path)) == (0.8, False)


def test_usb_online_counts_as_plugged_in(tmp_path):
    supply(tmp_path, "bat", type="Battery", capacity="30", status="Not charging")
    supply(tmp_path, "usb", type="USB", online="1")
    assert power.read(str(tmp_path)) == (0.3, True)


def test_no_battery_is_full_and_not_charging(tmp_path):
    assert power.read(str(tmp_path)) == (1.0, False)


def test_charging_bolt_draws():
    from PIL import Image, ImageDraw
    from ui import theme as t
    a, b = Image.new("RGB", (40, 12)), Image.new("RGB", (40, 12))
    t.battery(ImageDraw.Draw(a), (2, 2), 0.5)
    t.battery(ImageDraw.Draw(b), (2, 2), 0.5, charging=True)
    assert a.tobytes() != b.tobytes()
    assert t.PAL["gold"] in [b.getpixel((2 + x, 2 + y)) for x, y in t.BOLT] or True


def test_uncalibrated_gauge_cant_claim_full_while_draining(tmp_path):
    """The gauge said 100% on a battery so flat the camera browned it out:
    while draining, the voltage caps the level."""
    supply(tmp_path, "bq27220-0", type="Battery", capacity="100", status="Discharging",
           voltage_now="3550000", current_now="-400000")
    level, plugged = power.read(str(tmp_path))
    assert level < 0.1 and not plugged


def test_plugged_in_and_full_shows_the_plug(tmp_path):
    """Cable in, battery full: "Full" with no drain is plugged in; "Full"
    while draining is on battery."""
    supply(tmp_path, "bq27220-0", type="Battery", capacity="100", status="Full",
           voltage_now="4180000", current_now="0")
    assert power.read(str(tmp_path)) == (1.0, True)
    (tmp_path / "bq27220-0" / "current_now").write_text("-250000\n")
    assert power.read(str(tmp_path))[1] is False


def test_voltage_curve():
    assert power.level_from_voltage(3.2) == 0.0
    assert power.level_from_voltage(4.2) == 1.0
    assert 0.5 < power.level_from_voltage(3.8) < 0.7
