"""
Check the add-ons on the device. Run it once the parts are connected:

    sudo python3 tools/hwtest.py

and paste the output to whoever is helping you. It only reads: it never
writes to or locks the secure chip, and it doesn't store fingerprints.
"""
import hashlib
import os
import sys

sys.path.insert(0, __file__.rsplit("/tools/", 1)[0])

from core.hw.atecc import ADDRESSES, ATECC, ChipError, I2CTransport, describe, pin_point  # noqa: E402
from core.hw.finger import PORTS, Fingerprint, FingerError, SerialPort  # noqa: E402


def chip():
    print("== Secure chip (I2C /dev/i2c-1)")
    if not os.path.exists("/dev/i2c-1"):
        print("  /dev/i2c-1 missing: enable I2C (raspi-config) and reboot")
        return
    for addr in ADDRESSES:
        try:
            c = ATECC(I2CTransport(addr))
            print(f"  0x{addr:02x}: info {c.info().hex()}")
        except (OSError, ChipError) as e:
            print(f"  0x{addr:02x}: nothing ({e})")
            continue
        st = describe(c, addr)
        print(f"  status: {st}")
        cfg = c.config()
        print(f"  config locked={cfg[87] != 0x55} data locked={cfg[86] != 0x55}")
        for slot in range(16):
            print(f"    slot {slot:2}: SlotConfig {c.slot_config(slot):04x}  KeyConfig {c.key_config(slot):04x}")
        if st.get("state") == "ready":
            a = c.ecdh(st["slot"], pin_point(b"test-1"))
            b = c.ecdh(st["slot"], pin_point(b"test-1"))
            d = c.ecdh(st["slot"], pin_point(b"test-2"))
            print(f"  ECDH repeatable={a == b} differs per PIN={a != d} "
                  f"(fingerprint {hashlib.sha256(a).hexdigest()[:8]})")


def finger():
    print("== Fingerprint (UART)")
    for path in PORTS:
        if not os.path.exists(path):
            print(f"  {path}: not present")
            continue
        try:
            f = Fingerprint(SerialPort(path))
            print(f"  {path}: wake={f.wake()} connected={f.connected()} saved={f.count()}")
            f.led(1)
        except (OSError, FingerError) as e:
            print(f"  {path}: {e}")
    print("  (If nothing answers: the Grove port may be in I2C mode; it needs")
    print("   GPIO4 low for UART. `pinctrl set 4 op dl` sets it.)")


def _card_check(chip):
    """Field on, wait for a card, select it and read its status (read only)."""
    import time
    from core.hw.nfc import IsoDep
    chip.start()
    print("  started; field on, hold a card to the back...")
    chip.field_on()
    for _ in range(20):
        atqa = chip.reqa()
        if atqa:
            break
        time.sleep(0.25)
    else:
        print("  no card answered REQA")
        chip.field_off()
        return
    uid, sak = chip.select()
    print(f"  ATQA {atqa.hex()} UID {uid.hex()} SAK {sak:02x}")
    dep = IsoDep(chip.transceive)
    print("  ATS", dep.rats().hex(), f"FSC {dep.fsc} FWT {dep.fwt_ms:.0f} ms")
    from core.cktap import Card
    c = Card(dep.apdu)
    st = c.select()
    print("  card:", c.kind, {k: v for k, v in st.items() if k != "pubkey"})
    chip.field_off()


def nfc_unit():
    """The Unit NFC (U216) on its own I2C bus (install.sh --nfc-unit, then a
    reboot): bus, chip ID, start-up and a card, all read only."""
    import glob
    from core.hw.nfc import NFCError, ST25R3916, _i2c_from_env
    print("== Unit NFC (I2C)")
    print("  i2c buses:", " ".join(sorted(glob.glob("/dev/i2c-*"))) or "none")
    try:
        bus = _i2c_from_env()
        chip = ST25R3916(bus, i2c=True)
        kind, rev = chip.identity()
        print(f"  {bus.path} 0x{bus.address:02x}: ST25R3916 type {kind} rev {rev} (want type 5)")
        _card_check(chip)
    except (OSError, NFCError, ImportError) as e:
        print("  Unit NFC:", e)
        print("  (No /dev/i2c-3? sudo sh deploy/install.sh --nfc-unit, then reboot.")
        print("   Wiring: Unit SDA (yellow) to EXT pin 3, SCL (white) to pin 1,")
        print("   red to pin 6 (5V), black to pin 4 (GND).)")


def nfc_cap():
    """The Cap CC1101 & NFC, step by step: SPI devices, the CC1101's ID and
    sleep, the ST25R3916's ID, start-up, then (if a tap card is held to the
    back) REQA, select, RATS and the card's status. Reads only; unseals,
    signs and changes nothing on a card."""
    import glob
    from core.hw.nfc import CC1101, NFCError, ST25R3916, _spi_from_env
    print("== Cap CC1101 & NFC (SPI)")
    print("  spidev:", " ".join(sorted(glob.glob("/dev/spidev*"))) or "none (enable SPI?)")
    try:
        print("  CC1101:", CC1101(_spi_from_env("CZ_CC1101_SPI", "0.1", 0)).sleep())
    except Exception as e:  # noqa: BLE001
        print("  CC1101:", e)
    try:
        chip = ST25R3916(_spi_from_env("CZ_NFC_SPI", "0.0", 1))
        kind, rev = chip.identity()
        print(f"  ST25R3916 identity: type {kind} rev {rev} (want type 5)")
        _card_check(chip)
    except (OSError, NFCError, ImportError) as e:
        print("  NFC:", e)
    print("  (Wrong bus? Try CZ_NFC_SPI=0.1 CZ_CC1101_SPI=0.0, or another bus.)")


if __name__ == "__main__":
    what = sys.argv[1:] or ["chip", "finger", "nfc"]
    if "chip" in what:
        chip()
    if "finger" in what:
        finger()
    if "nfc" in what:
        nfc_cap()
    if "nfc" in what or "unit" in what:
        nfc_unit()
