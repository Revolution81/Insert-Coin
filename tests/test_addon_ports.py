"""HARDWARE finds add-ons by itself (0.8.42):

  side (Grove) port   fingerprint (UART), or M5's secure chip / the NFC Unit
                      (I2C1, the keyboard's bus: the signer relays to 0x50
                      only, and the port goes back to UART when nothing's there)
  top (GPIO) header   the NFC Cap, or a secure chip on pins 3/1, whose bus is
                      made while running (tools/ext_bus.py), never in config.txt
"""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import app as app_module  # noqa: E402
import copy_backup  # noqa: E402
import ext_bus  # noqa: E402
import migrate  # noqa: E402
from core.hw import grove, nfc, unit_relay  # noqa: E402
from core.hw.nfc import I2C_M_RD, REG_IC_ID  # noqa: E402
from test_app import app  # noqa: E402,F401


# ------------------------------------------------------------ side port switch
def test_grove_switch_writes_the_led(tmp_path, monkeypatch):
    led = tmp_path / "brightness"
    led.write_text("0\n")
    monkeypatch.setattr(grove, "LED", str(led))
    monkeypatch.delenv("CZ_GROVE", raising=False)
    monkeypatch.setattr(grove.time, "sleep", lambda s: None)
    assert grove.mode() == "uart"
    assert grove.set_mode("i2c") and led.read_text() == "1" and grove.mode() == "i2c"
    assert grove.set_mode("uart") and led.read_text() == "0"
    with pytest.raises(ValueError):
        grove.set_mode("spi")


# ------------------------------------------------------------ the signer's relay
class FakeBus:
    def __init__(self, ident=0x2A):          # 0b00101 010: ST25R3916 rev 2
        self.ident, self.sent, self.fail = ident, [], False

    def _rdwr(self, msgs):
        if self.fail:
            raise OSError(121, "Remote I/O error")
        self.sent.append(msgs)
        if msgs == [(0, bytes([0x40 | REG_IC_ID])), (I2C_M_RD, 1)]:
            return bytes([self.ident])
        return bytes(range(msgs[-1][1])) if msgs[-1][0] & I2C_M_RD else b""

    def close(self):
        pass


@pytest.fixture
def relay(monkeypatch):
    monkeypatch.setattr(grove, "mode", lambda: "i2c")
    r = unit_relay.UnitRelay(bus="/dev/null")
    bus = FakeBus()
    made = []

    def fake_i2c(path, address, shared_ok=False):
        made.append((path, address, shared_ok))
        return bus
    monkeypatch.setattr(unit_relay, "I2C", fake_i2c)
    return r, bus, made


def test_relay_reaches_only_0x50_and_checks_it_first(relay):
    r, bus, made = relay
    assert r.xfer([[0, "c1"]]) == ""
    assert made == [("/dev/null", 0x50, True)]
    assert bus.sent[0] == [(0, bytes([0x40 | REG_IC_ID])), (I2C_M_RD, 1)]   # identity first
    assert bus.sent[1] == [(0, b"\xc1")]
    assert r.xfer([[0, "5f"], [I2C_M_RD, 3]]) == "000102"


@pytest.mark.parametrize("msgs", [
    [], [[0, "c1"]] * 3, [[2, "c1"]], [[0, ""]], [[0, "zz"]], [[0, "00" * 301]],
    [[I2C_M_RD, 0]], [[I2C_M_RD, 301]], [[I2C_M_RD, 2], [0, "c1"]], [[0, 5]], "c1",
    [[I2C_M_RD, "3"]], [[0x10, "c1"]],
])
def test_relay_refuses_anything_else(relay, msgs):
    r, bus, _ = relay
    with pytest.raises(unit_relay.RelayError):
        r.xfer(msgs)
    assert bus.sent == []


def test_relay_refuses_while_the_port_is_uart(relay, monkeypatch):
    r, bus, _ = relay
    monkeypatch.setattr(grove, "mode", lambda: "uart")
    with pytest.raises(unit_relay.RelayError, match="I2C mode"):
        r.xfer([[0, "c1"]])
    assert bus.sent == []


def test_relay_refuses_something_that_isnt_an_nfc_chip(relay):
    r, bus, _ = relay
    bus.ident = 0x00
    with pytest.raises(unit_relay.RelayError, match="no NFC Unit"):
        r.xfer([[0, "c1"]])
    assert len(bus.sent) == 1                      # only the identity read went out


def test_relay_checks_again_after_an_error(relay):
    r, bus, _ = relay
    r.xfer([[0, "c1"]])
    bus.fail = True
    with pytest.raises(unit_relay.RelayError):
        r.xfer([[0, "c1"]])
    bus.fail = False
    bus.sent.clear()
    r.xfer([[0, "c1"]])
    assert bus.sent[0][0] == (0, bytes([0x40 | REG_IC_ID]))


def test_app_reaches_it_only_through_the_signer(app):  # noqa: F811
    from core.signer_service import _dispatch
    from core.trusted import SAFE
    assert {"nfc_unit_xfer", "nfc_unit_close"} <= SAFE
    r = _dispatch(app.signer._local, {"method": "nfc_unit_xfer", "params": {"msgs": [[0, "c1"]]}})
    assert r["ok"] is False                        # no unit here, and the port's not I2C


def test_relay_bytes_round_trip():
    seen = []
    bus = nfc.RelayI2C(lambda wire: (seen.append(wire), "0a0b")[1])
    assert bus.xfer([0x40 | REG_IC_ID, 0, 0]) == [0, 0x0A, 0x0B]
    assert seen == [[[0, "7f"], [I2C_M_RD, 2]]]


# ------------------------------------------------------------ the scan
class Scan:
    """Fakes for App.refresh_hardware: what's plugged where."""

    def __init__(self, app, monkeypatch, tmp_path, *, finger=False, has_cap=False,
                 grove_chip=False, gpio_chip=False, unit=False, start_mode="uart"):
        self.calls, self.mode = [], start_mode
        bus = tmp_path / "i2c-3"
        self.bus = bus
        monkeypatch.setattr(app_module, "EXT_BUS", str(bus))
        monkeypatch.setattr(grove, "mode", lambda: self.mode)

        def set_mode(m):
            self.calls.append(f"grove {m}")
            self.mode = m
            return True
        monkeypatch.setattr(grove, "set_mode", set_mode)

        def root(cmd):
            self.calls.append(cmd)
            if cmd == "ext-on":
                bus.write_text("")
            elif cmd == "ext-off" and bus.exists():
                bus.unlink()
            return True, "ok"
        monkeypatch.setattr(app, "_root", root)

        def chips():
            found = []
            if grove_chip and self.mode == "i2c":
                found.append({"state": "ready", "where": "GROVE", "m5": True, "address": 0x35})
            if gpio_chip and bus.exists():
                found.append({"state": "blank", "where": "GPIO", "m5": False, "address": 0x60})
            return {**(found[0] if found else {"state": "absent"}), "found": found, "in_use": False}
        monkeypatch.setattr(app, "_chips", chips)

        import core.hw.finger as fmod

        def open_sensor():
            self.calls.append("finger?")
            if finger and self.mode == "uart":
                return types.SimpleNamespace(port=None), {"state": "ready", "saved": 0}
            return None, {"state": "absent", "errors": ["ttyS0: no answer"]}
        monkeypatch.setattr(fmod, "open_sensor", open_sensor)

        def open_reader(unit_relay=None, cap=True):
            if unit_relay is None:
                self.calls.append(f"cap? {cap}")
                if cap and has_cap:
                    return object(), {"state": "ready", "reader": "cap", "where": "GPIO"}
                return None, {"state": "absent", "errors": ["Cap: no"]}
            self.calls.append("unit?")
            if unit and self.mode == "i2c":
                return object(), {"state": "ready", "reader": "unit", "where": "GROVE"}
            return None, {"state": "absent", "errors": ["Unit: no"]}
        monkeypatch.setattr(nfc, "open_reader", open_reader)
        monkeypatch.setattr(nfc, "release_cap", lambda: self.calls.append("release cap"))
        monkeypatch.setattr(app, "addon_power", lambda: None)
        app.nfc = None


def test_fingerprint_keeps_the_side_port_on_uart(app, monkeypatch, tmp_path):  # noqa: F811
    s = Scan(app, monkeypatch, tmp_path, finger=True, has_cap=True)
    hw = app.refresh_hardware()
    assert hw["grove"] == "finger" and hw["ext"] == "cap" and s.mode == "uart"
    assert "grove i2c" not in s.calls and "ext-on" not in s.calls


def test_m5_chip_on_the_side(app, monkeypatch, tmp_path):  # noqa: F811
    s = Scan(app, monkeypatch, tmp_path, grove_chip=True, has_cap=True)
    hw = app.refresh_hardware()                     # a plain scan never switches to I2C
    assert hw["grove"] is None and s.mode == "uart" and "grove i2c" not in s.calls
    hw = app.refresh_hardware(look_i2c=True)        # LOOK FOR CHIP / NFC
    assert hw["grove"] == "chip" and s.mode == "i2c" and app.settings.side_i2c
    assert s.calls.index("finger?") < s.calls.index("grove i2c")    # fingerprint asked first
    s.calls.clear()
    app.refresh_hardware()                          # RESCAN: still there, the port stays I2C
    assert "finger?" not in s.calls and "grove uart" not in s.calls


def test_nfc_unit_on_the_side(app, monkeypatch, tmp_path):  # noqa: F811
    s = Scan(app, monkeypatch, tmp_path, unit=True)
    hw = app.refresh_hardware(look_i2c=True)
    assert hw["grove"] == "nfc" and hw["nfc"]["where"] == "GROVE" and s.mode == "i2c"


def test_nothing_on_the_side_goes_back_to_uart(app, monkeypatch, tmp_path):  # noqa: F811
    s = Scan(app, monkeypatch, tmp_path, start_mode="i2c")
    hw = app.refresh_hardware()
    assert hw["grove"] is None and s.mode == "uart"


def test_blank_chip_on_top_gets_its_bus(app, monkeypatch, tmp_path):  # noqa: F811
    s = Scan(app, monkeypatch, tmp_path, gpio_chip=True)
    hw = app.refresh_hardware()
    assert hw["ext"] == "chip" and s.bus.exists()
    assert s.calls.index("release cap") < s.calls.index("ext-on")
    s.calls.clear()
    app.refresh_hardware()                          # RESCAN: bus kept, Cap not probed
    assert hw["ext"] == "chip" and "ext-on" not in s.calls and "ext-off" not in s.calls
    assert "cap? True" not in s.calls


def test_no_chip_on_top_removes_the_bus_again(app, monkeypatch, tmp_path):  # noqa: F811
    s = Scan(app, monkeypatch, tmp_path)
    hw = app.refresh_hardware()
    assert hw["ext"] is None and not s.bus.exists()
    assert s.calls.index("ext-on") < s.calls.index("ext-off")


def test_cap_swapped_in_for_the_chip(app, monkeypatch, tmp_path):  # noqa: F811
    s = Scan(app, monkeypatch, tmp_path, has_cap=True)
    s.bus.write_text("")                            # left from a chip, which is gone now
    hw = app.refresh_hardware()
    assert hw["ext"] == "cap" and not s.bus.exists()


# ------------------------------------------------------------ root helper
def test_helper_takes_one_word(monkeypatch):
    """deploy/cz-backup passes only $1 on: 0.8.41's `extbus chip` arrived as
    `extbus` and was refused, so EXT HEADER never changed anything."""
    got = []
    monkeypatch.setattr(copy_backup, "ext", lambda cmd: got.append(cmd) or 0)
    for cmd in ("ext-on", "ext-off", "ext-status"):
        assert copy_backup.main(["x", cmd]) == 0
    assert got == ["ext-on", "ext-off", "ext-status"]
    assert copy_backup.main(["x", "extbus"]) == 2
    helper = open(os.path.join(os.path.dirname(__file__), "..", "deploy", "cz-backup")).read()
    assert 'copy_backup.py "$1"' in helper


def test_ext_bus_on_and_off(monkeypatch, tmp_path):
    bus = tmp_path / "i2c-3"
    monkeypatch.setattr(ext_bus, "BUS", str(bus))
    monkeypatch.setattr(ext_bus.time, "sleep", lambda s: None)
    monkeypatch.setattr(ext_bus.os, "chown", lambda *a: None)
    ran = []

    def run(cmd, timeout=20):
        ran.append(cmd)
        out = ""
        if cmd[0] == "dtoverlay" and cmd[1] == "i2c-gpio":
            bus.write_text("")
        if cmd == ["dtoverlay", "-l"]:
            out = "Overlays (in load order):\n0:  i2c-gpio  bus=3 i2c_gpio_sda=23 i2c_gpio_scl=26\n"
        if cmd[:2] == ["dtoverlay", "-r"]:
            bus.unlink()
        return types.SimpleNamespace(returncode=0, stdout=out, stderr="")
    monkeypatch.setattr(ext_bus, "_run", run)
    assert ext_bus.status() == "off"
    assert ext_bus.on() == "on" and ext_bus.status() == "on"
    assert ["dtoverlay", *ext_bus.OVERLAY] in ran
    assert ["systemctl", "set-property", "--runtime", "czsigner.service",
            f"DeviceAllow={bus} rw"] in ran
    assert not any("config.txt" in " ".join(c) for c in ran)    # nothing for the next start
    assert ext_bus.off() == "off" and not bus.exists()
    assert ["dtoverlay", "-r", "0"] in ran


def test_ext_bus_reports_a_failed_overlay(monkeypatch, tmp_path):
    monkeypatch.setattr(ext_bus, "BUS", str(tmp_path / "i2c-3"))
    monkeypatch.setattr(ext_bus, "_run", lambda cmd, timeout=20: types.SimpleNamespace(
        returncode=1 if cmd[0] == "dtoverlay" else 0, stdout="", stderr="Failed to apply overlay"))
    with pytest.raises(OSError, match="Failed to apply"):
        ext_bus.on()


def test_old_udev_rule_handed_to_the_signer(monkeypatch, tmp_path):
    rule = tmp_path / "63.rules"
    rule.write_text('SUBSYSTEM=="i2c-dev", KERNEL=="i2c-3", GROUP="czapp", MODE="0660"\n')
    monkeypatch.setattr(migrate, "EXT_RULE", str(rule))
    monkeypatch.setattr(migrate.subprocess, "run", lambda *a, **k: None)
    assert migrate.step_ext_bus_rule() is False     # no restart needed
    assert 'GROUP="i2c"' in rule.read_text()
    assert migrate.step_ext_bus_rule() is False


# ------------------------------------------------------------ side port at power-on
def test_side_port_starts_on_uart(tmp_path, monkeypatch):
    """M5's Unit ID on the side port stopped the start: GPIO4 floats high
    (I2C1) until Linux sets it low. The boot firmware now drives it low."""
    cfg = tmp_path / "config.txt"
    original = "dtparam=i2c_arm=on\n[cm4]\notg_mode=1\n"
    cfg.write_text(original)
    assert migrate.step_grove_uart_at_boot(str(cfg)) is False     # no app restart
    text = cfg.read_text()
    assert text.startswith(original)                              # nothing else touched
    block = text[len(original):]
    assert "[all]\ngpio=4=op,dl\n" in block and block.count("gpio=") == 1
    assert (tmp_path / "config.txt.insertcoin-bak").read_text() == original
    migrate.step_grove_uart_at_boot(str(cfg))                     # twice: still one block
    assert cfg.read_text() == text
    assert migrate.step_grove_uart_at_boot(str(tmp_path / "missing.txt")) is False


# ------------------------------------------------------------ hot swaps
def _watch(app, monkeypatch, mode, present=True):
    st = {"mode": mode, "present": present, "switched": []}
    monkeypatch.setattr(grove, "mode", lambda: st["mode"])

    def set_mode(m):
        st["switched"].append(m)
        st["mode"] = m
        return True
    monkeypatch.setattr(grove, "set_mode", set_mode)
    monkeypatch.setattr(app, "_side_present", lambda what: st["present"])
    monkeypatch.setattr(app, "_chips", lambda: {"state": "absent", "found": []})
    return st


def test_unit_pulled_out_puts_the_port_back_on_uart(app, monkeypatch):  # noqa: F811
    """Seen on the device: chip out, fingerprint in, keys dead until it was
    unplugged (its UART line was on the keyboard's bus)."""
    import core.hw.finger as fmod
    st = _watch(app, monkeypatch, "i2c")
    app.hw["grove"] = "chip"
    app.settings.side_i2c = True
    app._side_check()
    assert st["switched"] == []                      # still there
    st["present"] = False
    app._side_check()                                # gone (checked twice, 20 ms apart)
    assert st["switched"] == ["uart"]
    assert app.SIDE_WATCH_S <= 0.5                   # within half a second of pulling it
    app.pump()
    assert app.hw["grove"] is None
    assert app.settings.side_i2c is False            # not put back on I2C by itself
    sensor = types.SimpleNamespace(port=None)
    monkeypatch.setattr(fmod, "open_sensor", lambda: (sensor, {"state": "ready", "saved": 1}))
    app.finger = None
    app._side_probed = 0.0
    app._side_check()                                # the sensor that went in
    app.pump()
    assert app.hw["grove"] == "finger" and app.finger is sensor


def test_watch_waits_for_rescan(app, monkeypatch):  # noqa: F811
    st = _watch(app, monkeypatch, "i2c", present=False)
    app.hw["grove"] = "chip"
    lock = app._hwlock()
    import threading
    held = threading.Event()
    done = threading.Event()

    def hold():
        with lock:
            held.set()
            done.wait(2)
    t = threading.Thread(target=hold)
    t.start()
    held.wait(2)
    for _ in range(3):
        app._side_check()
    done.set()
    t.join()
    assert st["switched"] == []


def test_row_colour_is_the_state(app):  # noqa: F811
    from ui.hardware_flow import hardware_menu
    app.hw["chip"] = {"state": "ready", "serial": "aa", "in_use": True,
                      "found": [{"state": "ready", "serial": "aa", "m5": True, "where": "GROVE"},
                                {"state": "blank", "serial": "bb", "m5": False, "where": "GPIO"}]}
    hardware_menu(app)
    rows = {it[0]: it[2:4] for it in app.visible.items if len(it) > 2}
    assert rows["M5 SECURE CHIP"] == ("GROVE", "gold") and rows["SECURE CHIP"] == ("GPIO", "cyan")


# ------------------------------------------------------------ fingerprint power
def test_fingerprint_gets_fresh_power_unless_just_powered(app, monkeypatch):  # noqa: F811
    """Fingerprint2 answered within a second of power-on and then not at all
    (0.8.45's boot log): unless the 5 V just came on, cycle it first."""
    import core.hw.finger as fmod
    calls = []
    monkeypatch.setattr(grove, "power_cycle", lambda: calls.append("cycle") or True)
    monkeypatch.setattr(app_module.time, "sleep", lambda s: calls.append(f"wait {s}"))
    monkeypatch.setattr(fmod, "open_sensor", lambda: calls.append("ask") or (None, {"state": "absent"}))
    app._probe_finger(True)
    assert calls == ["ask"]
    calls.clear()
    app._probe_finger(False)
    assert calls == ["cycle", "wait 0.8", "ask"]


def test_power_cycle_only_on_uart(tmp_path, monkeypatch):
    p = tmp_path / "power"
    p.write_text("1")
    monkeypatch.setattr(grove, "POWER", str(p))
    monkeypatch.setattr(grove.time, "sleep", lambda s: None)
    monkeypatch.setattr(grove, "mode", lambda: "i2c")
    assert grove.power_cycle() is False and p.read_text() == "1"
    monkeypatch.setattr(grove, "mode", lambda: "uart")
    assert grove.power_cycle() is True and p.read_text() == "1" and grove.power() is True


def test_title_scan_not_repeated(app, monkeypatch):  # noqa: F811
    n = []
    monkeypatch.setattr(app, "refresh_hardware", lambda: n.append(1))
    app._hw_at = app_module.time.monotonic()
    app.start()
    assert n == []


# ------------------------------------------------------------ never I2C on a guess
def test_plain_scan_never_switches_to_i2c(app, monkeypatch, tmp_path):  # noqa: F811
    """A silent serial unit (or nothing) on the side port: RESCAN and start
    leave it on UART unless an I2C unit was asked for or found before."""
    s = Scan(app, monkeypatch, tmp_path)
    for _ in range(3):
        app.refresh_hardware()
    assert "grove i2c" not in s.calls and s.mode == "uart"


def test_unit_found_before_is_looked_for_again(app, monkeypatch, tmp_path):  # noqa: F811
    s = Scan(app, monkeypatch, tmp_path, grove_chip=True)
    app.settings.side_i2c = True                    # found there last time
    assert app.refresh_hardware()["grove"] == "chip" and s.mode == "i2c"


def test_a_working_fingerprint_wins_over_the_memory(app, monkeypatch, tmp_path):  # noqa: F811
    """Swapped while off: the sensor answers on UART, so I2C isn't tried."""
    s = Scan(app, monkeypatch, tmp_path, finger=True)
    app.settings.side_i2c = True
    assert app.refresh_hardware()["grove"] == "finger" and "grove i2c" not in s.calls


def test_look_finds_nothing_forgets(app, monkeypatch, tmp_path):  # noqa: F811
    s = Scan(app, monkeypatch, tmp_path)
    app.settings.side_i2c = True
    app.refresh_hardware(look_i2c=True)
    assert s.mode == "uart" and app.settings.side_i2c is False


def test_look_menu_warns_and_refuses_with_a_fingerprint(app, monkeypatch, tmp_path):  # noqa: F811
    from ui.hardware_flow import hardware_menu
    from ui.widgets import ConfirmScreen
    from test_app import enter
    app.hw["grove"] = "finger"
    hardware_menu(app)
    m = app.visible
    m.sel = [i[0] for i in m.items].index("LOOK FOR CHIP / NFC")
    enter(app)
    assert app.visible.title == "FINGERPRINT IN"
    app.pop()
    app.hw["grove"] = None
    hardware_menu(app)
    m = app.visible
    m.sel = [i[0] for i in m.items].index("LOOK FOR CHIP / NFC")
    enter(app)
    assert isinstance(app.visible, ConfirmScreen) and "damage" in " ".join(app.visible.lines)


# ------------------------------------------------------------ fingerprint is ADVANCED
def test_fingerprint_tools_behind_advanced(app):  # noqa: F811
    from core.hw.finger import Fingerprint, SimulatedSensor
    from ui.hardware_flow import finger_menu
    app.finger = Fingerprint(SimulatedSensor())
    app.hw["finger"] = {"state": "ready", "saved": 0}
    app.settings.advanced = False
    finger_menu(app)
    assert app.visible.title == "FINGERPRINT" and "ADVANCED" in " ".join(app.visible.lines)
    app.pop()
    app.hw["finger"]["saved"] = 1                  # already in use: still reachable
    finger_menu(app)
    assert [i[0] for i in app.visible.items][0] == "ADD A FINGER"
    app.pop()
    app.hw["finger"]["saved"] = 0
    app.settings.advanced = True
    finger_menu(app)
    assert [i[0] for i in app.visible.items][0] == "ADD A FINGER"
