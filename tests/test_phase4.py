"""
UR codes, label export, duress PIN, SeedQR and the encrypted history file.
"""
import base64
import glob
import hashlib
import json
import os
import random

import pytest
from PIL import Image

from core import seed as seedlib
from core.qr import Collector
from core.ur import (URDecoder, URError, Xoshiro256, _xor, bytewords_decode, bytewords_encode,
                     cbor_encode, choose_fragments, crc32, nominal_fragment_len, ur_encode,
                     ur_payload)
from core.vault import Vault, WrongPin
from ui.camera import decode
from ui.flow import HomeScreen
from ui.qrscreens import AnimatedQRScreen, SeedQRScreen
from ui.widgets import MenuScreen, PinScreen

from test_app import app, create_wallet, enter, press, settle, type_text  # noqa: F401
from test_core import ABANDON, DEST, fund
from test_qr import camera_frame, pump_until, use_camera


# ------------------------------------------------------------ UR: spec vectors
def test_bytewords_vector():
    # BCR-2020-012 example: bytes 00 01 02 80 ff
    assert bytewords_encode(bytes([0, 1, 2, 128, 255])) == "aeadaolazmjendeoti"
    assert bytewords_decode("aeadaolazmjendeoti") == bytes([0, 1, 2, 128, 255])
    with pytest.raises(URError):
        bytewords_decode("aeadaolazmjendeotk")


def test_xoshiro_vector():
    rng = Xoshiro256(hashlib.sha256(b"Wolf").digest())
    assert [rng.next() % 100 for _ in range(20)] == \
        [42, 81, 85, 8, 82, 84, 76, 73, 70, 88, 2, 74, 40, 48, 77, 54, 88, 7, 5, 88]


def test_fountain_fragment_choice_vector():
    rng = Xoshiro256(hashlib.sha256(b"Wolf").digest())
    msg = bytes(rng.next_int(0, 255) for _ in range(1024))
    ck = crc32(msg)
    assert nominal_fragment_len(1024, 100) == 94
    assert [choose_fragments(n, 11, ck) for n in range(1, 20)] == \
        [[i] for i in range(11)] + [[9], [2, 5, 6, 8, 9, 10], [8], [1, 5], [1],
                                    [0, 2, 4, 5, 8, 10], [5], [2]]


# ---------------------------------------------------------- UR: round trips
def mixed_part(message: bytes, seq: int, n: int, flen: int) -> str:
    """A fountain part the way a full encoder (e.g. Sparrow's) makes them."""
    frags = [message[i:i + flen].ljust(flen, b"\x00") for i in range(0, len(message), flen)]
    ck = crc32(message)
    data = bytes(flen)
    for i in choose_fragments(seq, n, ck):
        data = _xor(data, frags[i])
    return f"ur:crypto-psbt/{seq}-{n}/" + bytewords_encode(cbor_encode([seq, n, len(message), ck, data]))


def test_ur_single_and_multi_roundtrip():
    small = os.urandom(20)
    d = URDecoder()
    d.add(ur_encode(small)[0])
    assert ur_payload(d) == ("crypto-psbt", small)
    big = os.urandom(900)
    frames = ur_encode(big)
    assert len(frames) > 5 and all(f.startswith("UR:CRYPTO-PSBT/") for f in frames)
    random.shuffle(frames)
    d = URDecoder()
    for f in frames:
        d.add(f)
    assert ur_payload(d)[1] == big


def test_ur_recovers_missed_parts_from_mixed_ones():
    payload = os.urandom(700)
    message = cbor_encode(payload)
    flen = nominal_fragment_len(len(message), 60)
    n = -(-len(message) // flen)
    d = URDecoder()
    for seq in range(1, n + 1):
        if seq not in (2, 5):                       # camera missed parts 2 and 5
            d.add(mixed_part(message, seq, n, flen))
    seq = n + 1
    while d.result is None and seq < n + 200:
        d.add(mixed_part(message, seq, n, flen))
        seq += 1
    assert d.result is not None and ur_payload(d)[1] == payload


def test_collector_reads_ur_psbt():
    psbt = b"psbt\xff" + os.urandom(400)
    c = Collector()
    for f in ur_encode(psbt):
        c.add(f)
    assert c.result.kind == "psbt" and c.result.data == psbt


def test_airgapped_with_ur_in_and_out(app):  # noqa: F811
    create_wallet(app)
    fund(app.wallet, 200_000)
    unsigned = base64.b64decode(app.wallet.build(DEST, 50_000, 3)["psbt"])
    use_camera(app, ur_encode(unsigned) * 2)
    press(app, "KEY_Q")
    pump_until(app, lambda: getattr(app.visible, "title", "") == "CONFIRM SEND")
    press(app, "KEY_Z")
    enter(app)
    scr = app.visible
    assert isinstance(scr, AnimatedQRScreen) and scr.frames[0].startswith("UR:CRYPTO-PSBT")
    c = Collector()
    for i in range(len(scr.frames)):
        for text in decode(scr._qr_img(i).convert("L")):
            c.add(text)
    assert c.result.kind == "psbt"
    tx = app.wallet.finalize(base64.b64encode(c.result.data).decode())
    assert tx.raw


# ------------------------------------------------------------- label export
def test_label_export_qr(app):  # noqa: F811
    create_wallet(app)
    app.wallet.set_label("addr", DEST, "Pizza")
    from ui.flow import export_labels
    export_labels(app)
    c = Collector()
    for i in range(len(app.visible.frames)):
        for text in decode(app.visible._qr_img(i).convert("L")):
            c.add(text)
    assert json.loads(c.result.text) == {"type": "addr", "ref": DEST, "label": "Pizza"}


# ------------------------------------------------------------------ duress
def test_vault_duress(tmp_path):
    v = Vault(str(tmp_path))
    v.create("1111", ABANDON, "signet")
    size = os.path.getsize(v.path)
    decoy = seedlib.new_mnemonic()
    v.set_duress("1111", "2222", decoy)
    assert abs(os.path.getsize(v.path) - size) < 40     # same shape either way
    u = v.unlock("2222")
    assert u.decoy and u.mnemonic == decoy and not u.duress_set
    u = v.unlock("1111")
    assert not u.decoy and u.mnemonic == ABANDON and u.duress_set
    with pytest.raises(Exception):
        v.change_pin("1111", "2222")                     # can't collide with duress
    v.set_duress("2222", "3333", seedlib.new_mnemonic())  # from inside the decoy: no-op
    assert v.unlock("1111").mnemonic == ABANDON
    assert v.unlock("2222").mnemonic == decoy
    with pytest.raises(WrongPin):
        v.unlock("3333")


def test_vault_duress_erase(tmp_path):
    v = Vault(str(tmp_path))
    v.create("1111", ABANDON, "signet")
    v.set_duress("1111", "2222", seedlib.new_mnemonic(), erase_real=True)
    assert v.unlock("2222").decoy
    with pytest.raises(WrongPin):
        v.unlock("1111")                                 # real seed is gone
    assert v.unlock("2222").decoy                        # decoy still works


def test_duress_through_the_app(app):  # noqa: F811
    create_wallet(app, pin="1234")
    real_fp = app.fingerprint
    press(app, "KEY_X", "KEY_C", "KEY_C")                # SETUP
    enter(app)
    app.visible.sel = [it[0] for it in app.visible.items].index("SECURITY")
    enter(app)                                           # SECURITY
    app.visible.sel = next(i for i, it in enumerate(app.visible.items) if it[0] == "DURESS PIN")
    enter(app)
    enter(app)                                           # SET ONE UP
    for pin in ("1234", "9876", "9876"):
        type_text(app, pin)
        enter(app)
    assert app.visible.title == "WHEN USED"
    enter(app)                                           # OPEN DECOY ONLY
    assert app.visible.title == "DURESS PIN ON"
    enter(app)
    assert app.visible.title == "SECURITY"
    duress_row = next(it for it in app.visible.items if it[0] == "DURESS PIN")
    assert duress_row[2] == "ON"

    app.lock()

    settle(app)
    type_text(app, "9876")
    enter(app)
    assert isinstance(app.visible, HomeScreen) and app.fingerprint != real_fp
    press(app, "KEY_X", "KEY_C", "KEY_C")
    enter(app)
    app.visible.sel = [it[0] for it in app.visible.items].index("SECURITY")
    enter(app)                                           # SECURITY
    duress_row = next(it for it in app.visible.items if it[0] == "DURESS PIN")
    assert duress_row[2] == "OFF"                        # the decoy gives nothing away
    app.lock()
    settle(app)
    type_text(app, "1234")
    enter(app)
    assert app.fingerprint == real_fp


# ----------------------------------------------------------------- SeedQR
def test_seedqr_codec():
    m = seedlib.new_mnemonic(24)
    assert seedlib.seedqr_decode(seedlib.seedqr_encode(m)) == m
    with pytest.raises(ValueError):
        seedlib.seedqr_decode("0" * 48)                  # bad checksum
    assert seedlib.seedqr_encode(ABANDON) == "0000" * 11 + "0003"


def matrix_image(matrix, mod=8):
    n = len(matrix)
    im = Image.new("L", ((n + 8) * mod,) * 2, 255)
    px = im.load()
    for y, row in enumerate(matrix):
        for x, on in enumerate(row):
            if on:
                for dy in range(mod):
                    for dx in range(mod):
                        px[(x + 4) * mod + dx, (y + 4) * mod + dy] = 0
    return im


def test_seedqr_backup_screen(app):  # noqa: F811
    words = create_wallet(app, pin="1234")
    press(app, "KEY_X", "KEY_C", "KEY_C")
    enter(app)
    app.visible.sel = [it[0] for it in app.visible.items].index("BACKUP")
    enter(app)                                           # BACKUP
    app.visible.sel = next(i for i, it in enumerate(app.visible.items) if it[0] == "SEEDQR BACKUP")
    enter(app)
    type_text(app, "1234")
    enter(app)
    scr = app.visible
    assert isinstance(scr, SeedQRScreen) and len(scr.matrix) == 25
    app.render(0)
    text = decode(matrix_image(scr.matrix))[0]
    assert seedlib.seedqr_decode(text) == " ".join(words)


def test_restore_by_scanning_seedqr(app):  # noqa: F811
    use_camera(app, [seedlib.seedqr_encode(ABANDON)])
    press(app, "KEY_X")                                  # RESTORE
    enter(app)
    app.visible.sel = 2                                      # SCAN SEEDQR
    enter(app)
    pump_until(app, lambda: getattr(app.visible, "title", "") == "SEEDQR READ")
    enter(app)                                               # (on the signer's screen)
    assert isinstance(app.visible, MenuScreen) and app.visible.title == "PASSPHRASE?"
    enter(app)
    type_text(app, "4321")
    enter(app)
    type_text(app, "4321")
    enter(app)
    enter(app)
    assert app.fingerprint == "73c5da0a"


# ------------------------------------------------------- history encryption
def test_history_file_is_opaque(app):  # noqa: F811
    create_wallet(app)
    fund(app.wallet, 12_345)
    app.wallet.set_label("addr", DEST, "secret-label")
    files = glob.glob(os.path.join(os.path.dirname(app.wallet._path), "w-*.db"))
    raw = open(app.wallet._path, "rb").read()
    assert app.wallet._path in files
    for needle in (b"tpub", b"tb1q", b"secret-label", b"12345", app.fingerprint.encode()):
        assert needle not in raw
