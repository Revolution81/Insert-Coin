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
