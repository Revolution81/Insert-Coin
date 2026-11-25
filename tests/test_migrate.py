"""System steps an update brings along (tools/migrate.py), and the one-time
trigger for updates installed by an older helper."""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import migrate  # noqa: E402


def _db(monkeypatch, members, has_group=True):
    calls = []
    monkeypatch.setattr(migrate.pwd, "getpwnam", lambda u: types.SimpleNamespace(pw_gid=999))

    def getgrnam(g):
        if not has_group:
            raise KeyError(g)
        return types.SimpleNamespace(gr_gid=997, gr_mem=list(members))
    monkeypatch.setattr(migrate.grp, "getgrnam", getgrnam)
    monkeypatch.setattr(migrate.subprocess, "run", lambda cmd, check: calls.append(cmd))
    return calls


def test_adds_the_app_to_gpio_once(monkeypatch):
    calls = _db(monkeypatch, [])
    assert migrate.needed() and migrate.run() is True
    assert calls == [["usermod", "-aG", "gpio", "czapp"]]
    calls = _db(monkeypatch, ["czapp"])
    assert not migrate.needed() and migrate.run() is False and calls == []


def test_makes_the_group_if_missing(monkeypatch):
    calls = _db(monkeypatch, [], has_group=False)
    migrate.run()
    assert calls[0] == ["groupadd", "--system", "gpio"]


def test_not_a_device(monkeypatch):
    def nope(u):
        raise KeyError(u)
    monkeypatch.setattr(migrate.pwd, "getpwnam", nope)
    assert not migrate.needed() and migrate.run() is False


def test_app_only_asks_as_czapp(monkeypatch):
    import subprocess

    import app as appmod
    started = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: started.append(a))
    appmod.App.system_steps()                          # the test user isn't czapp
    assert started == []


def test_addon_power_through_sysfs(tmp_path):
    import addon_power
    for led in ("grove_5v_out", "ext_5v_out"):
        (tmp_path / led).mkdir()
        (tmp_path / led / "brightness").write_text("0")
    assert addon_power.switch_on(str(tmp_path)) == ["Grove 5V (grove_5v_out): on",
                                                    "EXT 5V (ext_5v_out): on"]
    assert (tmp_path / "grove_5v_out" / "brightness").read_text() == "1"
    assert (tmp_path / "ext_5v_out" / "brightness").read_text() == "1"
    assert "grove_fun" not in addon_power.LEDS      # the UART/I2C switch is never touched


def test_finger_wakes_a_sleeping_sensor():
    from core.hw.finger import Fingerprint, SimulatedSensor
    sim = SimulatedSensor()
    sim.stored = {0: "a", 1: "b"}
    f = Fingerprint(sim)
    sim.asleep = True
    assert f.delete_all() and sim.stored == {}        # woken, then erased
    sim.asleep = True
    assert f.count() == 0


def test_finger_fe_means_asleep():
    """Seen on the device: asleep, the sensor answers every command with
    code 0xFE (a valid frame), not silence."""
    from core.hw.finger import Fingerprint, SimulatedSensor
    sim = SimulatedSensor()
    sim.stored = {0: "a"}
    sim.asleep = True
    assert Fingerprint(sim).count() == 1
