"""LAN: a plugged-in cable is found from sysfs, shown as a plug icon, and
switched off with the radios in Vault mode (any wired name, not only eth0)."""
import os
import subprocess

from core import radios
from test_app import app  # noqa: F401


def _iface(root, name, carrier="1", oper="up", kind="1", wireless=False, device=True):
    d = root / name
    d.mkdir()
    (d / "type").write_text(kind)
    (d / "carrier").write_text(carrier)
    (d / "operstate").write_text(oper)
    if wireless:
        (d / "wireless").mkdir()
    if device:
        (d / "device").mkdir()


def test_wired_link_from_sysfs(tmp_path, monkeypatch):
    monkeypatch.delenv("CZ_SIM_LAN", raising=False)
    _iface(tmp_path, "lo", device=False)
    _iface(tmp_path, "wlan0", wireless=True)
    _iface(tmp_path, "docker0", device=False)
    _iface(tmp_path, "enx00e04c", carrier="0", oper="down")
    assert radios.wired_link(str(tmp_path)) is None
    (tmp_path / "enx00e04c" / "carrier").write_text("1")
    (tmp_path / "enx00e04c" / "operstate").write_text("up")
    assert radios.wired_link(str(tmp_path)) == "enx00e04c"


def test_icon_and_network_row(app, monkeypatch):  # noqa: F811
    from ui import theme as t
    from ui.flow import _lan_value
    drawn = []
    real = t.sprite
    monkeypatch.setattr(t, "sprite", lambda img, rows, *a, **k: (drawn.append(rows), real(img, rows, *a, **k))[1])
    monkeypatch.setenv("CZ_FORCE_ONLINE", "1")
    monkeypatch.setenv("CZ_SIM_LAN", "eth0")
    app._lan_at = -99
    assert _lan_value(app) == ("CONNECTED", "cyan")
    app.render(0)
    assert t.LAN in drawn and t.WIFI not in drawn
    drawn.clear()
    monkeypatch.setenv("CZ_SIM_LAN", "")
    app._lan_at = -99
    app.render(0)
    assert t.WIFI in drawn and t.LAN not in drawn
    monkeypatch.setenv("CZ_FORCE_ONLINE", "0")
    assert _lan_value(app) == ("OFF", "grey")


def test_radio_helper_takes_every_wired_interface_down():
    here = os.path.dirname(os.path.abspath(__file__))
    src = open(os.path.join(here, "..", "deploy", "cz-radio")).read()
    assert "eth0" not in src.split("case")[1]                # no fixed name
    assert 'for n in $(wired); do ip link set "$n" down' in src
    assert subprocess.run(["sh", "-n", os.path.join(here, "..", "deploy", "cz-radio")]).returncode == 0
