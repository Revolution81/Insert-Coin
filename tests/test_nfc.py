"""
NFC tap cards: CBOR, ISO 14443 framing, the Coinkite card protocol against
simulated cards, and the SATSCARD sweep / TAPSIGNER cosigning screens.

The card simulators implement the protocol document's card side, so these
tests show the client follows the document; they can't show a real card
agrees. tools/hwtest.py nfc is for that.
"""
import glob
import os

import pytest
from embit import ec
from embit.networks import NETWORKS
from embit.script import address_to_scriptpubkey
from embit.transaction import Transaction

from core import cbor, cktap
from core.hw import nfc
from ui.widgets import MenuScreen

from test_app import app, create_wallet, enter, press, settle, type_text  # noqa: F401
from test_core import DEST, fake_tx
from script_vm import verify_tx

TEST = NETWORKS["test"]


# --------------------------------------------------------------- CBOR
def test_cbor_roundtrip_and_strictness():
    v = {"cmd": "sign", "digest": b"\x01" * 32, "subpath": [0, 5], "n": -3, "ok": True,
         "none": None, "path": [0x8000002C]}
    assert cbor.decode(cbor.encode(v)) == v
    for bad in (b"\x5f\x41a\xff",            # indefinite bytes
                cbor.encode(1) + b"\x00",     # trailing
                b"\x81" * 20 + b"\x00",       # too deep
                b"\x5a\xff\xff\xff\xff",      # truncated
                b"\x63\xff\xfe\xfd"):         # bad UTF-8
        with pytest.raises(cbor.CBORError):
            cbor.decode(bad)


# ------------------------------------------------------------ 14443
def test_crc_a_vectors():
    assert nfc.crc_a(b"\x00\x00") == bytes.fromhex("a01e")     # ISO 14443-3 annex B
    assert nfc.crc_a(b"\x12\x34") == bytes.fromhex("26cf")
    assert nfc.crc_a(b"\x50\x00") == bytes.fromhex("57cd")     # HLTA


class FakePicc:
    """An ISO 14443-4 card at the block level: small FSC (forces chaining
    both ways), asks for more time once (WTX)."""

    def __init__(self, apdu_handler, fsc_i=1, wtx_once=True):
        self.h, self.fsc_i, self.wtx_pending = apdu_handler, fsc_i, wtx_once
        self.rx, self.tx_chunks, self.bn = b"", [], 0
        self.log = []

    def transceive(self, frame, timeout_ms):
        self.log.append(frame)
        if frame[:1] == b"\xe0":
            return bytes([5, 0x70 | self.fsc_i, 0x80, 0x80, 0x00])   # TL T0 TA TB TC; FWI 8
        pcb = frame[0]
        if pcb & 0xF7 == 0xF2:                                       # WTX reply
            return self._next()
        if pcb & 0xE2 == 0x02:                                       # I-block
            self.rx += frame[1:]
            if pcb & 0x10:
                return bytes([0xA2 | (pcb & 1)])                     # R(ACK)
            resp = self.h(self.rx)
            self.rx = b""
            fsc = nfc.FSC_TABLE[self.fsc_i] - 3
            self.tx_chunks = [resp[i:i + fsc] for i in range(0, len(resp), fsc)]
            if self.wtx_pending:
                self.wtx_pending = False
                return b"\xf2\x01"
            return self._next()
        if pcb & 0xF6 == 0xA2:                                       # reader's R(ACK)
            return self._next()
        raise AssertionError(f"unexpected PCB {pcb:02x}")

    def _next(self):
        chunk = self.tx_chunks.pop(0)
        more = 0x10 if self.tx_chunks else 0
        pcb = 0x02 | self.bn | more
        self.bn ^= 1
        return bytes([pcb]) + chunk


def test_isodep_chaining_and_wtx():
    long_in = bytes(range(200))
    picc = FakePicc(lambda apdu: apdu[::-1] + b"\x90\x00")
    dep = nfc.IsoDep(picc.transceive)
    dep.rats()
    assert dep.fsc == 24 and dep.fwt_ms > 70
    data, sw = dep.apdu(long_in)
    assert sw == 0x9000 and data == long_in[::-1]
    assert any(f[0] & 0x10 for f in picc.log if f[0] & 0xE2 == 0x02)       # we chained
    assert any(f[0] & 0xF7 == 0xF2 for f in picc.log)                     # we answered WTX
    blocks = [f[0] & 1 for f in picc.log if f[0] & 0xE2 == 0x02]
    assert blocks[:2] == [0, 1]                                           # block numbers toggle


def test_tap_card_over_isodep():
    """The whole card protocol through the block layer."""
    card = cktap.SimSatscard()
    picc = FakePicc(lambda apdu: b"".join(
        x if isinstance(x, bytes) else x.to_bytes(2, "big") for x in card.transmit(apdu)),
        fsc_i=5, wtx_once=False)
    dep = nfc.IsoDep(picc.transceive)
    dep.rats()
    c = cktap.Card(dep.apdu)
    c.select()
    assert c.address(TEST).startswith("tb1q")


# -------------------------------------------------------------- cktap
def test_ecdh_agrees_with_libsecp():
    from embit.util import secp256k1
    a, b = os.urandom(32), os.urandom(32)
    A = ec.PrivateKey(a).get_public_key().sec()
    B = ec.PrivateKey(b).get_public_key().sec()
    assert cktap.ecdh(A, b) == cktap.ecdh(B, a)
    try:
        assert secp256k1.ecdh(secp256k1.ec_pubkey_parse(A), b) == cktap.ecdh(A, b)
    except (AttributeError, NotImplementedError):
        pass


def test_satscard_protocol():
    card = cktap.SimSatscard()
    c = cktap.Card(card.transmit)
    c.select()
    assert c.kind == "satscard" and c.slot() == (0, 10)
    addr = c.address(TEST)
    assert c.genuine(card.root_pub) and not c.genuine()       # sim root != Coinkite's
    with pytest.raises(cktap.CardError) as e:
        c.unseal("000000")
    assert e.value.code == 401
    key = c.unseal("123456")
    from embit.script import p2wpkh
    assert p2wpkh(key.get_public_key()).address(TEST) == addr
    assert c.slot_used() == "unsealed"
    assert c.new_slot("123456") == 1 and c.address(TEST) != addr


def test_cvc_never_sent_in_clear_and_rate_limit():
    card = cktap.SimSatscard(cvc="246810")
    sent = []

    def spy(apdu):
        sent.append(apdu)
        return card.transmit(apdu)
    c = cktap.Card(spy)
    c.select()
    for _ in range(3):
        with pytest.raises(cktap.CardError):
            c.unseal("111111")
    with pytest.raises(cktap.CardError) as e:
        c.unseal("246810")
    assert e.value.code == 429                                 # made to wait
    for _ in range(15):
        c.wait()
    c.unseal("246810")
    assert not any(b"246810" in a for a in sent)


def test_lying_card_is_caught():
    class Liar(cktap.SimSatscard):
        def c_read(self, req):                                 # signs with the wrong key
            r = super().c_read(req)
            r["pubkey"] = ec.PrivateKey(os.urandom(32)).get_public_key().sec()
            return r
    c = cktap.Card(Liar().transmit)
    c.select()
    with pytest.raises(cktap.CardError, match="signature"):
        c.address(TEST)

    class SwapKey(cktap.SimSatscard):
        def c_unseal(self, req):                               # hands over a different key
            r = super().c_unseal(req)
            r["pubkey"] = ec.PrivateKey(os.urandom(32)).get_public_key().sec()
            return r
    c = cktap.Card(SwapKey().transmit)
    c.select()
    c.address(TEST)
    with pytest.raises(cktap.CardError, match="match"):
        c.unseal("123456")


def test_tapsigner_signs_multisig_psbt(tmp_path):
    from core import multisig as ms
    from core.config import Settings
    from core.signer import Signer
    from core.wallet import WalletService
    from test_core import ABANDON
    s = Signer(str(tmp_path / "s"))
    s.setup("2468", ABANDON, "signet")
    card = cktap.SimTapsigner()
    c = cktap.Card(card.transmit)
    c.select()
    card_key = c.cosigner_key("123456", "signet")
    assert card_key.startswith("[") and "/48h/1h/0h/2h]tpub" in card_key
    desc = ms.build_descriptor(2, [s.public_info()["multisig_key"], card_key])
    entry = s.register_multisig(desc, "Card")
    w = WalletService(Settings(network="signet"), entry["descriptor"], None, entry["id"],
                      in_memory=True)
    _, addr = w.receive_address()
    w.record_unconfirmed(fake_tx(address_to_scriptpubkey(addr), 100_000).serialize())
    built = w.build(DEST, 20_000, fee_rate=2)
    summ = s.describe(built["psbt"])
    mine = s.sign(built["psbt"], summ["digest"])
    full, n = cktap.sign_psbt(c, "123456", mine, "signet")
    assert n == 1 and w.signatures(full) == (2, 2)
    tx = w.finalize(full)
    from embit.psbt import PSBT
    p = PSBT.from_string(full)
    verify_tx(Transaction.parse(tx.raw),
              [(p.utxo(i).script_pubkey.data, p.utxo(i).value) for i in range(len(p.inputs))])
    other = cktap.Card(cktap.SimTapsigner().transmit)
    other.select()
    with pytest.raises(cktap.CardError, match="isn't a signer"):
        cktap.sign_psbt(other, "123456", mine, "signet")


# ------------------------------------------------------------ screens
def with_card(app, card):
    reader = nfc.SimReader(card)
    app.nfc = reader
    app.hw["nfc"] = {"state": "ready", "kind": "simulated", "subghz": {"state": "asleep"}}
    return reader


def test_sweep_satscard_in_the_ui(app, monkeypatch):  # noqa: F811
    from ui.nfc_flow import TapScreen
    create_wallet(app)
    card = cktap.SimSatscard()
    reader = with_card(app, card)
    slot_addr = card._addr(card.slots[0]["pub"])
    coin = fake_tx(address_to_scriptpubkey(slot_addr), 50_000)
    utxos = [{"txid": coin.txid().hex(), "vout": 0, "value": 50_000, "height": 800}]
    monkeypatch.setattr(app.wallet, "foreign_utxos", lambda script: utxos)
    sent = []
    monkeypatch.setattr(app.wallet, "broadcast", lambda tx, replaces=None: (sent.append(tx),
                                                                           tx.txid)[1])
    monkeypatch.setenv("CZ_FORCE_ONLINE", "1")
    press(app, "KEY_T")
    assert isinstance(app.visible, TapScreen)
    app.render(0)
    reader.present = True
    settle(app)
    menu = app.visible
    assert isinstance(menu, MenuScreen) and menu.title == "SATSCARD"
    text = " ".join(menu.info)
    assert "SEALED" in text and "Genuine" in text and "50,000 sats" in text
    app.render(0)
    menu.sel = [it[0] for it in menu.items].index("SWEEP TO WALLET")
    enter(app)
    assert app.visible.title == "SWEEP?"
    press(app, "KEY_Z")
    enter(app)
    type_text(app, "123456")
    enter(app)
    assert app.visible.title == "SWEPT!"
    tx = Transaction.parse(sent[0].raw)
    verify_tx(tx, [(address_to_scriptpubkey(slot_addr).data, 50_000)])
    own = {app.wallet._script(0, i).data for i in range(5)}
    assert len(tx.vout) == 1 and tx.vout[0].script_pubkey.data in own   # all of it, to us
    assert tx.vout[0].value < 50_000 and card.slots[0]["state"] == "unsealed"
    assert not reader.field                            # field off once we're done


def test_wrong_code_is_explained(app, monkeypatch):  # noqa: F811
    create_wallet(app)
    card = cktap.SimSatscard()
    reader = with_card(app, card)
    monkeypatch.setattr(app.wallet, "foreign_utxos", lambda s: [
        {"txid": "11" * 32, "vout": 0, "value": 9_000, "height": 1}])
    monkeypatch.setenv("CZ_FORCE_ONLINE", "1")
    press(app, "KEY_T")
    reader.present = True
    settle(app)
    menu = app.visible
    menu.sel = [it[0] for it in menu.items].index("SWEEP TO WALLET")
    enter(app)
    press(app, "KEY_Z")
    enter(app)
    type_text(app, "999999")
    enter(app)
    assert app.visible.title == "CARD SAYS NO" and card.slots[0]["state"] == "sealed"


def test_tapsigner_as_cosigner_end_to_end(app):  # noqa: F811
    """NEW MULTISIG 2-of-2 with this device + a TAPSIGNER, then spend:
    this device signs, the card signs, done."""
    from test_qr import pump_until
    from ui.flow import ConfirmSendScreen
    create_wallet(app)
    card = cktap.SimTapsigner()
    reader = with_card(app, card)
    reader.present = True
    press(app, "KEY_W")
    app.visible.sel = [it[0] for it in app.visible.items].index("NEW MULTISIG")
    enter(app)
    enter(app)                                         # SEGWIT
    app.visible.sel = [it[0] for it in app.visible.items].index("2 OF 2")
    enter(app)
    press(app, "KEY_TAB")                              # TAP CARD instead of scanning
    settle(app)
    assert app.visible.title == "TAPSIGNER"            # code entry
    type_text(app, "123456")
    enter(app)
    pump_until(app, lambda: app.visible.title == "COSIGNER 2/2")
    enter(app)
    assert app.visible.title == "NAME IT"
    enter(app)
    assert app.visible.title == "CHECK SIGNERS"
    press(app, "KEY_Z")
    enter(app)                                         # SAVE
    enter(app)                                         # close descriptor
    _, addr = app.wallet.receive_address()
    app.wallet.record_unconfirmed(fake_tx(address_to_scriptpubkey(addr), 90_000).serialize())
    app.refresh_balance()
    press(app, "KEY_S")
    type_text(app, DEST)
    enter(app)
    type_text(app, "30000")
    enter(app)
    enter(app)
    assert isinstance(app.visible, ConfirmSendScreen)
    press(app, "KEY_Z")
    enter(app)
    assert app.visible.title == "NEEDS SIGNATURES"
    enter(app)
    press(app, "KEY_T")
    settle(app)
    menu = app.visible
    assert menu.title == "TAPSIGNER" and menu.items[0][0] == "SIGN PENDING TX"
    enter(app)
    type_text(app, "123456")
    enter(app)
    assert app.visible.title == "FULLY SIGNED" and card.signed == 1
    outbox = glob.glob(os.path.join(app.outbox_dir, "*.hex"))
    raw = bytes.fromhex(open(outbox[0]).read())
    tx = Transaction.parse(raw)
    verify_tx(tx, [(address_to_scriptpubkey(addr).data, 90_000)])


def test_hardware_screen_and_subghz(app):  # noqa: F811
    from ui.hardware_flow import hardware_menu, status_icons
    from ui import theme as t
    create_wallet(app)
    with_card(app, cktap.SimSatscard())
    assert any(icon is t.NFC for icon, _ in status_icons(app))
    hardware_menu(app)
    m = app.visible
    row = [it for it in m.items if it[0] == "NFC"][0]
    assert row[2] == "CAP"
    m.sel = m.items.index(row)
    enter(app)
    assert app.visible.title == "NFC CAP"
    assert any("asleep" in line for line in app.visible.lines)


def test_hardware_screen_tells_the_unit_apart(app):  # noqa: F811
    from ui.hardware_flow import hardware_menu
    create_wallet(app)
    with_card(app, cktap.SimSatscard())
    app.hw["nfc"] = {"state": "ready", "reader": "unit", "kind": "ST25R3916 rev 2",
                     "bus": "I2C /dev/i2c-3 0x50", "subghz": {"state": "absent"}}
    hardware_menu(app)
    m = app.visible
    row = [it for it in m.items if it[0] == "NFC"][0]
    assert row[2] == "UNIT NFC"
    m.sel = m.items.index(row)
    enter(app)
    assert app.visible.title == "UNIT NFC"
    text = " ".join(app.visible.lines)
    assert "I2C" in text and "sub-GHz" in text and "CC1101" not in text
    enter(app)
    app.hw["nfc"] = {"state": "absent", "errors": ["Cap: no", "Unit: no"]}
    hardware_menu(app)
    assert [it for it in app.visible.items if it[0] == "NFC"][0][2] == "NONE"


def test_i2c_reads_and_writes_like_the_library(monkeypatch, tmp_path):
    """Writes are one message; reads (registers, FIFO, space B) are the
    header, a repeated start, then the data."""
    sent = []
    fake = tmp_path / "i2c-3"
    fake.write_bytes(b"")
    bus = nfc.I2C(str(fake))
    monkeypatch.setattr(bus, "_rdwr", lambda msgs: (sent.append(msgs),
                                                    bytes(range(1, msgs[-1][1] + 1))
                                                    if msgs[-1][0] & nfc.I2C_M_RD else b"")[1])
    chip = nfc.ST25R3916(bus, i2c=True)
    assert chip.read(nfc.REG_IC_ID, 1) == b"\x01"
    assert sent[-1] == [(0, bytes([0x40 | nfc.REG_IC_ID])), (nfc.I2C_M_RD, 1)]
    chip.write(nfc.REG_IO2, 0x84)
    assert sent[-1] == [(0, bytes([nfc.REG_IO2, 0x84]))]
    chip.write_b(nfc.B_EMD, 0x40)
    assert sent[-1] == [(0, bytes([nfc.SPACE_B, nfc.B_EMD, 0x40]))]
    assert chip.fifo_read(3) == b"\x01\x02\x03"
    assert sent[-1] == [(0, bytes([nfc.OP_READ_FIFO])), (nfc.I2C_M_RD, 3)]
    chip.fifo_load(b"\x50\x00")
    assert sent[-1] == [(0, bytes([nfc.OP_LOAD_FIFO, 0x50, 0x00]))]
    chip.cmd(nfc.CMD_REQA)
    assert sent[-1] == [(0, bytes([nfc.CMD_REQA]))]
    assert nfc.I2C.header_len([nfc.SPACE_B, 0x40 | 0x05]) == 2


def test_unit_refused_on_the_keyboards_bus(monkeypatch):
    monkeypatch.delenv("CZ_NFC_I2C_SHARED", raising=False)
    with pytest.raises(nfc.NFCError, match="keyboard"):
        nfc.I2C("/dev/i2c-1")


def test_reader_found_says_which(monkeypatch):
    """No Cap on SPI, a Unit on its own bus: HARDWARE is told it's the Unit."""
    class FakeChip:
        def __init__(self, bus, vdd_5v=False, i2c=False):
            self.i2c = i2c

        def start(self):
            if not self.i2c:
                raise nfc.NFCError("no ST25R3916 answering on SPI")

        def identity(self):
            return 5, 2

    class FakeBus:
        path, address = "/dev/i2c-3", 0x50

        def close(self):
            pass
    monkeypatch.setenv("CZ_NFC", "auto")
    monkeypatch.setattr(nfc, "ST25R3916", FakeChip)
    monkeypatch.setattr(nfc, "_spi_from_env", lambda *a, **k: object())
    monkeypatch.setattr(nfc, "CC1101", lambda spi: type("C", (), {"sleep": lambda self: {"state": "absent"}})())
    monkeypatch.setattr(nfc, "_i2c_from_env", lambda: FakeBus())
    reader, st = nfc.open_reader()
    assert reader is not None and st["reader"] == "unit" and "i2c-3" in st["bus"]


def test_cc1101_put_to_sleep():
    class FakeSPI:
        def __init__(self):
            self.sent = []

        def xfer(self, data):
            self.sent.append(list(data))
            if data[0] == 0x30 | 0xC0:
                return [0, 0x00]                        # PARTNUM
            if data[0] == 0x31 | 0xC0:
                return [0, 0x14]                        # VERSION
            return [0] * len(data)
    spi = FakeSPI()
    st = nfc.CC1101(spi).sleep()
    assert st["state"] == "asleep"
    assert spi.sent[0] == [0x30] and spi.sent[-1] == [0x39]   # SRES first, SPWD last
    assert not any(cmd[0] in (0x35, 0x3F) for cmd in spi.sent)   # never STX, never FIFO
