"""The Cap CC1101 & NFC on the Zero: power on GPIO26, NFC chip select on
GPIO22 framing every transfer, CC1101 GDO0 tri-stated, and hands off the
pins when the Unit NFC's I2C bus owns them."""
import sys
import types

import pytest

from core.hw import nfc


class FakePin:
    log = []

    def __init__(self, pin, value, label=""):
        self.pin, self.value = pin, value
        FakePin.log.append((pin, value))

    def set(self, v):
        self.value = v
        FakePin.log.append((self.pin, v))

    def close(self):
        pass


class FakeSpiDev:
    def __init__(self):
        self.sent = []
        self.no_cs = False

    def open(self, bus, dev):
        self.where = (bus, dev)

    def xfer2(self, data):
        self.sent.append((list(data), [p for p in FakePin.log]))
        return [0] * len(data)

    def close(self):
        pass


@pytest.fixture
def fakes(monkeypatch):
    FakePin.log = []
    monkeypatch.setitem(sys.modules, "spidev", types.SimpleNamespace(SpiDev=FakeSpiDev))
    from core.hw import gpio
    monkeypatch.setattr(gpio, "OutputPin", FakePin)
    monkeypatch.setattr(nfc, "_CAP", {})
    monkeypatch.setenv("CZ_NFC_I2C", "/nonexistent/i2c-3:0x50")
    return FakePin


def test_cs_frames_each_transfer(fakes):
    spi = nfc._cap_nfc_spi()
    assert spi.s.where == (0, 1) and spi.s.no_cs
    fakes.log.clear()
    spi.xfer([0x40, 0])
    assert fakes.log == [(22, 0), (22, 1)]
    assert spi.s.sent[0][1] == [(22, 0)]              # low during the transfer


def test_power_once(fakes):
    nfc._cap_power()
    nfc._cap_power()
    assert fakes.log == [(26, 1)]


def test_unit_bus_keeps_its_pins(fakes, tmp_path, monkeypatch):
    bus = tmp_path / "i2c-3"
    bus.write_text("")
    monkeypatch.setenv("CZ_NFC_I2C", f"{bus}:0x50")
    with pytest.raises(OSError, match="Unit NFC"):
        nfc._cap_power()
    assert fakes.log == []


def test_cc1101_gdo0_tristated():
    sent = []
    spi = types.SimpleNamespace(xfer=lambda d: sent.append(list(d)) or [0x0F, 0x14])
    nfc.CC1101(spi).sleep()
    assert [0x02, 0x2E] in sent and sent.index([0x02, 0x2E]) < sent.index([0x39])
