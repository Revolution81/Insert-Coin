"""
Where a new seed's randomness comes from: the Linux kernel always, plus
camera sensor noise and the secure chip's RNG when they're there, plus dice
if you roll them. All hashed together, each under its own label.
"""
import random
import socket
import threading

import pytest
from PIL import Image

from core import seed as seedlib
from core.hw import atecc
from core.scand import Session
from ui.camera import FakeCamera, RemoteScanner, decode, frame_noise

from test_app import app, enter, press  # noqa: F401


@pytest.fixture(autouse=True)
def fresh_chip():
    atecc._SIMS.clear()
    atecc._OPEN.clear()
    yield
    atecc._SIMS.clear()
    atecc._OPEN.clear()


def noisy_frames(n=4, seed=1):
    rnd = random.Random(seed)
    return [Image.frombytes("L", (64, 48), bytes(rnd.randrange(256) for _ in range(64 * 48)))
            for _ in range(n)]


# ------------------------------------------------------------------ mixing
def test_every_source_changes_the_seed(monkeypatch):
    monkeypatch.setattr(seedlib.os, "urandom", lambda n: b"\x11" * n)   # kernel fixed
    base = seedlib.new_mnemonic(12)
    assert seedlib.new_mnemonic(12) == base                             # deterministic given inputs
    variants = {
        seedlib.new_mnemonic(12, b"123456"),
        seedlib.new_mnemonic(12, camera=b"\x01" * 32),
        seedlib.new_mnemonic(12, chip=b"\x02" * 32),
        # same bytes under a different label is a different input
        seedlib.new_mnemonic(12, camera=b"\x02" * 32),
    }
    assert base not in variants and len(variants) == 4
    assert all(seedlib.is_valid(m) for m in variants)


def test_kernel_randomness_always_used():
    """Even with every extra source fixed, two seeds differ: os.urandom."""
    a = seedlib.new_mnemonic(24, b"1", camera=b"c" * 32, chip=b"k" * 32)
    b = seedlib.new_mnemonic(24, b"1", camera=b"c" * 32, chip=b"k" * 32)
    assert a != b and len(a.split()) == 24


# ------------------------------------------------------------------ camera
def test_frame_noise():
    n1 = frame_noise(FakeCamera(noisy_frames(seed=1)))
    n2 = frame_noise(FakeCamera(noisy_frames(seed=2)))
    assert len(n1) == 32 and n1 != n2
    still = Image.new("L", (64, 48), 90)
    assert frame_noise(FakeCamera([still])) == b""        # a frozen camera adds nothing


def test_noise_through_czscan():
    closed = []

    class Cam(FakeCamera):
        def close(self):
            closed.append(1)
    a, b = socket.socketpair()
    threading.Thread(target=Session(a, lambda: Cam(noisy_frames()), decode).serve,
                     daemon=True).start()
    r = RemoteScanner(lambda t: None, sock=b)
    n = r.noise()
    assert len(n) == 32
    assert closed == [1]                                   # camera on only for that
    r.stop()


# -------------------------------------------------------------------- chip
def test_chip_entropy(monkeypatch):
    monkeypatch.setenv("CZ_SE", "sim")
    r1, r2 = atecc.chip_entropy(), atecc.chip_entropy()
    assert len(r1) == 32 and r1 != r2
    monkeypatch.setenv("CZ_SE", "sim-blank")               # unlocked chip: fixed pattern
    atecc._OPEN.clear()
    assert atecc.chip_entropy() == b""
    monkeypatch.setenv("CZ_SE", "none")
    assert atecc.chip_entropy() == b""


# ------------------------------------------------------- your own sources
def test_dice_only_is_checkable():
    import hashlib
    from embit import bip39
    rolls = "3615244251" * 5                                # 50 rolls
    m = seedlib.dice_only_mnemonic(rolls, 12)
    assert m == bip39.mnemonic_from_bytes(hashlib.sha256(rolls.encode()).digest()[:16])
    m24 = seedlib.dice_only_mnemonic(rolls * 2, 24)
    assert len(m24.split()) == 24
    with pytest.raises(ValueError):
        seedlib.dice_only_mnemonic(rolls[:49], 12)          # too few for 128 bits
    with pytest.raises(ValueError):
        seedlib.dice_only_mnemonic(rolls + "7", 12)


def test_cards_and_coins():
    assert seedlib.parse_cards("as 7h 10d KC") == ["AS", "7H", "TD", "KC"]
    assert seedlib.parse_cards("AS7HTD") == ["AS", "7H", "TD"]
    with pytest.raises(ValueError):
        seedlib.parse_cards("AS AS")
    with pytest.raises(ValueError):
        seedlib.parse_cards("ZZ")
    assert round(seedlib.card_bits(52)) == 226 and round(seedlib.card_bits(1), 2) == 5.7
    assert seedlib.coin_entropy("hTth") == b"HTTH"
    with pytest.raises(ValueError):
        seedlib.coin_entropy("HX")


def test_key_mashing_credits_rhythm_not_autorepeat(monkeypatch):
    from ui.input import Event, Kind
    from ui.secure_flows import KeyMashScreen
    times = []
    monkeypatch.setattr("ui.secure_flows.time.perf_counter_ns", lambda: times.pop(0))
    out = []
    scr = KeyMashScreen(lambda c, data, bits: out.append((data, bits)))
    # varied typing: 4 different keys at irregular gaps
    times += [0, 130_000_000, 190_000_000, 410_000_000]
    for ch in "qzpm":
        scr.handle(Event(Kind.CHAR, ch), None)
    assert scr.bits == 8
    # a held key auto-repeating every 33 ms: no credit after the first
    times += [450_000_000 + k * 33_000_000 for k in range(6)]
    for _ in range(6):
        scr.handle(Event(Kind.CHAR, "x"), None)
    assert scr.bits <= 12 and scr.count == 10
    scr.handle(Event(Kind.SUBMIT), None)
    assert out and out[0][1] == scr.bits and len(out[0][0]) > 50


# -------------------------------------------------------------------- flow
def spy_seed(monkeypatch):
    got = {}
    real = seedlib.new_mnemonic

    def spy(words, extra=b"", camera=b"", chip=b"", user=None):
        got.update(words=words, dice=extra, camera=camera, chip=chip, user=user or {})
        return real(words, extra, camera=camera, chip=chip, user=user)
    monkeypatch.setattr(seedlib, "new_mnemonic", spy)
    return got


def test_new_wallet_uses_camera_and_chip(app, monkeypatch):  # noqa: F811
    monkeypatch.setenv("CZ_SE", "sim")
    app.camera_factory = lambda: FakeCamera(noisy_frames())
    got = spy_seed(monkeypatch)
    enter(app)                                             # CREATE NEW WALLET
    assert app.visible.title == "NEW WALLET" and app.console.stack
    assert app.visible.info[0] == "Mixed in: LINUX RNG, CAMERA, CHIP"
    app.render(0)
    enter(app)                                             # 12 WORDS
    assert len(got["camera"]) == 32 and len(got["chip"]) == 32


def test_new_wallet_without_extras(app):  # noqa: F811
    from ui.camera import CameraError

    def no_camera():
        raise CameraError("none")
    app.camera_factory = no_camera
    enter(app)
    assert app.visible.title == "NEW WALLET"
    assert app.visible.info[0] == "Mixed in: LINUX RNG"
    press(app, "KEY_ESC")


def goto(app, label):  # noqa: F811
    app.visible.sel = [it[0] for it in app.visible.items].index(label)
    enter(app)


def test_stack_your_own_sources(app, monkeypatch):  # noqa: F811
    from test_app import type_text
    got = spy_seed(monkeypatch)
    enter(app)
    goto(app, "ADD COIN FLIPS")
    type_text(app, "HTTHHTHT")
    enter(app)
    goto(app, "ADD CARD SHUFFLE")
    type_text(app, "AS 7H TD")
    enter(app)
    goto(app, "ADD DICE ROLLS")
    type_text(app, "123456")
    enter(app)
    goto(app, "ADD KEY MASHING")
    assert app.visible.title == "KEY MASHING"
    type_text(app, "asdkfjqwe")
    app.render(0)
    enter(app)
    menu = app.visible
    assert menu.title == "NEW WALLET" and menu.info[1].startswith("You added 4 sources:")
    rights = {it[0]: it[2] for it in menu.items if len(it) > 2}
    assert rights["ADD COIN FLIPS"] == "8 BITS" and rights["ADD CARD SHUFFLE"] == "17 BITS"
    app.render(0)
    goto(app, "24 WORDS")
    assert got["words"] == 24 and got["dice"] == b"123456"
    assert set(got["user"]) == {"coin flips", "card shuffle", "key mashing"}
    assert got["user"]["card shuffle"] == b"AS 7H TD"


def test_dice_only_through_the_screens(app):  # noqa: F811
    import hashlib
    from embit import bip39
    from test_app import type_text
    enter(app)
    goto(app, "DICE ONLY")
    enter(app)                                             # read the warning
    enter(app)                                             # 12 WORDS
    type_text(app, "12345" * 9)                            # 45: too few
    enter(app)
    assert "at least 50" in app.visible.error
    type_text(app, "66666")
    enter(app)
    rolls = "12345" * 9 + "66666"
    want = bip39.mnemonic_from_bytes(hashlib.sha256(rolls.encode()).digest()[:16]).split()
    assert app.visible.words == want
