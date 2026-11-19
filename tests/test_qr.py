"""
QR formats, and the camera flows end to end: QR images are generated,
roughed up to look like camera frames, "seen" by a FakeCamera and decoded
by zbar exactly as on the device.
"""
import base64
import os
import random

import pytest
import qrcode
from PIL import Image, ImageFilter

from core.qr import Collector, QRFormatError, bbqr_encode, classify, parse_bip21
from ui.camera import FakeCamera, decode
from ui.flow import ConfirmSendScreen, HomeScreen, watch_only_texts
from ui.qrscreens import AnimatedQRScreen, ScanScreen
from ui.widgets import MessageScreen, TextInputScreen

from test_app import app, create_wallet, enter, press, type_text  # noqa: F401
from test_core import DEST, fund
from test_core import assert_valid_signatures


# ------------------------------------------------------------- formats
def test_bip21():
    r = parse_bip21("bitcoin:tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx?amount=1.5&label=Bob%27s")
    assert r.sats == 150_000_000 and r.label == "Bob's"
    assert parse_bip21("BITCOIN:abc?amount=0.00000001").sats == 1
    with pytest.raises(QRFormatError):
        parse_bip21("bitcoin:abc?amount=0.000000001")       # 9 decimals
    with pytest.raises(QRFormatError):
        parse_bip21("bitcoin:abc?amount=1e3")


def test_classify():
    assert classify("tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx").kind == "address"
    assert classify("cHNidP8BAA==").kind == "psbt"
    assert classify("70736274ff0100").data == bytes.fromhex("70736274ff0100")
    assert classify("hello there friend").kind == "text"
    with pytest.raises(QRFormatError, match="Lightning"):
        classify("lnbc1pvjluezpp5")


@pytest.mark.parametrize("size", [1, 50, 700, 5000])
def test_bbqr_roundtrip_any_order(size):
    data = os.urandom(size // 2) + b"x" * (size - size // 2)
    frames = bbqr_encode(data, "P")
    assert all(len(f) <= 148 for f in frames)
    shuffled = frames * 2
    random.shuffle(shuffled)
    c = Collector()
    for f in shuffled:
        c.add(f)
    assert c.result.kind == "psbt" and c.result.data == data


def test_bbqr_restarts_on_different_animation():
    a, b = bbqr_encode(os.urandom(600), "P"), bbqr_encode(os.urandom(900), "T")
    c = Collector()
    c.add(a[0])
    for f in b:
        c.add(f)
    assert c.result.kind == "tx"


# ------------------------------------------------------- camera frames
def camera_frame(text: str, seed: int = 0) -> Image.Image:
    """A QR as a camera might see it: small, tilted, soft, noisy."""
    rnd = random.Random(seed)
    q = qrcode.make(text, border=2).convert("L")
    modules = q.width // 10                         # qrcode.make uses 10 px boxes
    side = max(rnd.randint(260, 360), modules * 5)  # dense codes: hold the camera closer
    q = q.resize((side, side), Image.NEAREST).rotate(rnd.uniform(-8, 8), expand=True, fillcolor=255)
    frame = Image.new("L", (640, 480), 170)
    frame.paste(q, ((640 - q.width) // 2 + rnd.randint(-30, 30),
                    (480 - q.height) // 2 + rnd.randint(-20, 20)))
    frame = frame.filter(ImageFilter.GaussianBlur(0.8))
    px = frame.load()
    for _ in range(4000):
        x, y = rnd.randrange(640), rnd.randrange(480)
        px[x, y] = max(0, min(255, px[x, y] + rnd.randint(-60, 60)))
    return frame


def test_zbar_reads_rough_frames():
    for i in range(5):
        assert decode(camera_frame("B$ZP0100" + "A" * 140, seed=i)) == ["B$ZP0100" + "A" * 140]


def use_camera(app, texts, blank_every=0):
    imgs = []
    for i, t in enumerate(texts):
        imgs.append(camera_frame(t, seed=i))
        if blank_every and i % blank_every == 0:
            imgs.append(Image.new("L", (640, 480), 120))          # missed frames
    app.camera_factory = lambda: FakeCamera(imgs)


def pump_until(app, cond, n=60):
    from test_app import settle
    for _ in range(n):
        if cond():
            return
        app.pump()
        settle(app)
    assert cond(), f"stuck on {type(app.visible).__name__}"


# --------------------------------------------------------- the flows
def test_send_scan_bip21_prefills_amount(app):  # noqa: F811
    create_wallet(app)
    fund(app.wallet, 100_000)
    use_camera(app, [f"bitcoin:{DEST}?amount=0.0004&label=Coffee"])
    press(app, "KEY_S")
    press(app, "KEY_TAB")                       # SCAN (frames are read as the app pumps)
    pump_until(app, lambda: isinstance(app.visible, TextInputScreen) and app.visible.title == "AMOUNT")
    assert app.visible.buf.text == "40000"          # from amount=0.0004
    enter(app)
    enter(app)                                  # NORMAL fee
    assert isinstance(app.visible, ConfirmSendScreen)
    assert app.visible.s["spend"] == 40_000 and app.visible.s["outputs"][0]["address"] == DEST


def test_scan_rejects_wrong_kind(app):  # noqa: F811
    create_wallet(app)
    use_camera(app, ["cHNidP8BAA=="])           # a PSBT where an address is expected
    press(app, "KEY_S")
    press(app, "KEY_TAB")
    app.pump()
    assert isinstance(app.visible, ScanScreen) and "psbt" in app.visible.error
    app.render(0)
    press(app, "KEY_ESC")
    assert isinstance(app.visible, TextInputScreen) and app.visible.title == "SEND TO"


def test_airgapped_psbt_round_trip(app):  # noqa: F811
    """Sparrow's role is played by the watch-only wallet: it builds a PSBT,
    shows it as BBQr; we scan it, sign, show the result; it scans that back."""
    create_wallet(app)
    fund(app.wallet, 250_000)
    unsigned = app.wallet.build(DEST, 70_000, 4)["psbt"]
    frames = bbqr_encode(base64.b64decode(unsigned), "P", max_chars=100)
    assert len(frames) > 1
    use_camera(app, frames * 2, blank_every=2)

    press(app, "KEY_Q")
    assert isinstance(app.visible, ScanScreen)
    pump_until(app, lambda: isinstance(app.visible, ConfirmSendScreen))
    s = app.visible.s
    assert app.visible.title == "CONFIRM SEND" and s["spend"] == 70_000
    press(app, "KEY_Z")                         # YES
    enter(app)
    assert isinstance(app.visible, AnimatedQRScreen) and app.visible.title == "SIGNED PSBT"

    # "Sparrow" scans our animated QR off the screen
    back = Collector()
    for i in range(len(app.visible.frames)):
        for text in decode(app.visible._qr_img(i).convert("L")):
            back.add(text)
    assert back.result and back.result.kind == "psbt"
    signed_b64 = base64.b64encode(back.result.data).decode()
    tx = app.wallet.finalize(signed_b64)
    from embit.transaction import Transaction
    assert_valid_signatures(signed_b64, Transaction.parse(tx.raw))
    enter(app)
    assert isinstance(app.visible, HomeScreen)


def test_foreign_psbt_refused(app, tmp_path):  # noqa: F811
    create_wallet(app)
    from core.config import Settings
    from core.seed import new_mnemonic
    from core.signer import Signer
    from core.wallet import WalletService
    other = Signer(str(tmp_path / "other"))
    info = other.setup("1111", new_mnemonic(), "signet")
    w = WalletService(Settings(network="signet"), info["descriptor"],
                      info["change_descriptor"], info["fingerprint"], in_memory=True)
    fund(w, 50_000)
    use_camera(app, bbqr_encode(base64.b64decode(w.build(DEST, 10_000, 2)["psbt"]), "P"))
    press(app, "KEY_Q")
    pump_until(app, lambda: isinstance(app.visible, MessageScreen))
    assert "not from this wallet" in " ".join(app.visible.lines)


def test_vault_send_shows_tx_qr(app):  # noqa: F811
    create_wallet(app)
    fund(app.wallet, 100_000)
    press(app, "KEY_S")
    type_text(app, DEST)
    enter(app)
    type_text(app, "20000")
    enter(app)
    enter(app)
    press(app, "KEY_Z")
    enter(app)
    scr = app.visible
    assert isinstance(scr, AnimatedQRScreen) and scr.title == "SAVED TO OUTBOX"
    c = Collector()
    for i in range(len(scr.frames)):
        for t in decode(scr._qr_img(i).convert("L")):
            c.add(t)
    assert c.result.kind == "tx"
    from embit.transaction import Transaction
    assert Transaction.parse(c.result.data).txid().hex() == \
        os.path.basename(next(iter(os.listdir(app.outbox_dir))))[:-4]


def test_scanned_tx_in_vault_mode(app):  # noqa: F811
    create_wallet(app)
    from embit.transaction import Transaction, TransactionInput, TransactionOutput
    from embit.script import address_to_scriptpubkey
    raw = Transaction(vin=[TransactionInput(os.urandom(32), 0)],
                      vout=[TransactionOutput(5000, address_to_scriptpubkey(DEST))]).serialize()
    use_camera(app, bbqr_encode(raw, "T"))
    press(app, "KEY_Q")
    pump_until(app, lambda: isinstance(app.visible, MessageScreen))
    assert app.visible.title == "VAULT MODE"


def test_export_watch_only_qr(app):  # noqa: F811
    create_wallet(app)
    desc, key = watch_only_texts(app.public)
    assert desc.startswith("wpkh([") and desc.endswith("/<0;1>/*)")
    assert key.startswith("[") and "tpub" in key
    from ui.flow import export_watch_only
    export_watch_only(app)
    scr = app.visible
    assert decode(scr._qr_img(0).convert("L")) == [desc]
    press(app, "KEY_C")                        # switch to XPUB view
    assert decode(scr._qr_img(0).convert("L")) == [key]
    for f in (0, 1):
        scr.render(app, f)


def test_no_camera_shows_error(app):  # noqa: F811
    create_wallet(app)
    from ui.camera import CameraError

    def broken():
        raise CameraError("camera not available: test")
    app.camera_factory = broken
    press(app, "KEY_Q")
    assert isinstance(app.visible, ScanScreen) and "camera" in app.visible.error
    app.render(0)
    press(app, "KEY_ESC")
    assert isinstance(app.visible, HomeScreen)


def test_small_signed_tx_is_one_qr(app):  # noqa: F811
    """A one-in, one-out send fits a single, steady QR (no flipping)."""
    create_wallet(app)
    fund(app.wallet, 100_000)
    press(app, "KEY_S")
    type_text(app, DEST)
    enter(app)
    type_text(app, "20000")
    enter(app)
    enter(app)
    press(app, "KEY_Z")
    enter(app)
    scr = app.visible
    assert scr.title == "SAVED TO OUTBOX" and len(scr.frames) == 1
    c = Collector()
    for t in decode(scr._qr_img(0).convert("L")):
        c.add(t)
    assert c.result.kind == "tx"


def test_animated_qr_steps_by_hand():
    from ui.input import Event, Kind
    scr = AnimatedQRScreen("T", ["A", "B", "C"])
    assert scr.paused is None and scr.fps == 2.0
    class _App:  # noqa: N801
        def pop(self):
            pass
    start = scr.index
    scr.handle(Event(Kind.NAV, "RIGHT"), _App())
    assert scr.paused == (start + 1) % 3
    scr.handle(Event(Kind.NAV, "RIGHT"), _App())
    scr.handle(Event(Kind.NAV, "LEFT"), _App())
    assert scr.index == (start + 1) % 3          # stays put until the next arrow
