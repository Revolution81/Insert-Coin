"""KEY TELEPORT (COLDCARD's protocol): the crypto, the signer's keys, and two
devices passing a message, a seed and a multisig PSBT back and forth."""
import base64
import json

import pytest
from embit import bip32
from embit.networks import NETWORKS as N

from core import seed as seedlib
from core import teleport as kt
from core.multisig import build_descriptor, cosigner_key
from core.qr import Collector
from core.signer import Signer, SignerError
from test_app import app, create_wallet, enter, settle, type_text  # noqa: F401
from test_core import ABANDON
from test_multisig import new_root
from ui.qrscreens import AnimatedQRScreen
from ui.widgets import MessageScreen

OTHER = "legal winner thank year wave sausage worth useful legal winner thank yellow"
WORDS24 = ("letter advice cage absurd amount doctor acoustic avoid letter advice cage absurd "
           "amount doctor acoustic avoid letter advice cage absurd amount doctor acoustic bless")


# ------------------------------------------------------------------ crypto
def test_rx_code_round_trip():
    for _ in range(20):
        k = kt.new_secret()
        code, enc = kt.rx_code(k)
        assert len(code) == 8 and code.isdigit() and len(enc) == 33
        assert kt.rx_code(k) == (code, enc)                      # from the key alone
        assert kt.decrypt_rx(code, enc) == kt.pubkey(k)
    wrong = [kt.decrypt_rx("%08d" % i, enc) for i in range(40)]
    assert kt.pubkey(k) not in wrong or code in ["%08d" % i for i in range(40)]
    assert kt.decrypt_rx("1234", enc) is None and kt.decrypt_rx("abcdefgh", enc) is None


def test_package_layers():
    rx, tx = kt.new_secret(), kt.new_secret()
    noid, pw = kt.new_password()
    assert len(pw) == 8 and kt.password_bytes(pw.lower()) == noid
    pkg = kt.seal(tx, kt.pubkey(rx), noid, b"nhello")
    sk, inner = kt.open_outer(rx, pkg[:33], pkg[33:])
    assert kt.open_inner(sk, noid, inner) == ("n", b"hello")
    assert kt.open_inner(sk, b"\0" * 5, inner) is None              # wrong teleport password
    assert kt.open_outer(kt.new_secret(), pkg[:33], pkg[33:]) is None   # someone else's
    bad = bytearray(pkg)
    bad[40] ^= 1
    assert kt.open_outer(rx, bytes(bad[:33]), bytes(bad[33:])) is None
    assert kt.ecdh(rx, kt.pubkey(tx)) == kt.ecdh(tx, kt.pubkey(rx)) == sk


def test_forgiving_password_and_ecdh_fallback():
    assert kt.password_bytes("0O1L 8B22") == kt.password_bytes("OOLLBB22")
    with pytest.raises(kt.TeleportError):
        kt.password_bytes("ABC")
    a, b = kt.new_secret(), kt.new_secret()
    x, y = kt._mul_py(kt.pubkey(b), int.from_bytes(a, "big"))
    import hashlib
    assert hashlib.sha256(x.to_bytes(32, "big") + y.to_bytes(32, "big")).digest() == kt.ecdh(a, kt.pubkey(b))


def test_coldcard_secret_encoding():
    xv = N["test"]["xprv"]
    for m in (ABANDON, OTHER, WORDS24, " ".join(OTHER.split() + OTHER.split()[:6])):
        if not seedlib.is_valid(m):
            continue
        enc = kt.encode_secret(m)
        assert enc[0] == 0x80 | (len(m.split()) * 4 // 3 // 8 - 2)
        assert kt.decode_secret(enc, xv) == m
    x = seedlib.root_key(OTHER, "", "signet").to_string()
    enc = kt.encode_secret(x)
    assert enc[0] == 1 and kt.decode_secret(enc, xv) == x
    raw = bytes([32]) + bytes(range(32))                            # "master seed bytes"
    assert kt.decode_secret(raw, xv) == bip32.HDKey.from_seed(bytes(range(32)), version=xv).to_string()
    with pytest.raises(kt.TeleportError):
        kt.decode_secret(b"\x05", xv)


def test_notes_and_vault_formats():
    ours = [{"kind": "note", "title": "Safe", "text": "12-34"},
            {"kind": "password", "title": "Mail", "user": "", "password": "pw", "site": "x.com"}]
    cc = json.loads(kt.notes_out(ours))
    assert cc[0] == {"title": "Safe", "misc": "12-34"} and cc[1]["user"] == ""
    back = kt.notes_in(kt.notes_out(ours))
    assert back[0]["kind"] == "note" and back[1]["kind"] == "password" and back[1]["site"] == "x.com"
    enc = kt.encode_secret(OTHER)
    raw, label = kt.vault_in(kt.vault_out("abcd1234", enc, "Old"))
    assert raw == enc and label == "Old"
    bk = f'# Coldcard backup\nraw_secret = "{enc.hex()}"\nsetting.notes = [{{"title": "t", "misc": "m"}}]\n'
    assert kt.backup_in(bk.encode()) == (enc, [{"title": "t", "misc": "m"}])


# ------------------------------------------------------------------ signer
def test_receive_key_kept_encrypted(tmp_path):
    s = Signer(str(tmp_path))
    s.setup("1234", ABANDON, "signet")
    assert s.kt_rx_key(make=False) is None
    k = s.kt_rx_key()
    assert s.kt_rx_key() == k and s.kt_rx_key(new=True) != k
    k = s.kt_rx_key()
    assert all(k not in p.read_bytes() for p in tmp_path.iterdir() if p.is_file())
    s.lock()
    s.unlock("1234")
    assert s.kt_rx_key(make=False) == k
    s.kt_rx_done()
    assert s.kt_rx_key(make=False) is None


def test_duress_pin_never_teleports_the_real_key(tmp_path):
    """Found in review: the duress PIN used to send the open wallet's master
    key. Now it gets the decoy's words, like SHOW SEED WORDS."""
    s = Signer(str(tmp_path))
    s.setup("1234", ABANDON, "signet")
    decoy = OTHER
    s.vault.set_duress("1234", "9999", decoy)
    enc, fp = s.teleport_secret(pin="9999")
    assert kt.decode_secret(enc, N["test"]["xprv"]) == decoy
    assert fp != s.public_info()["fingerprint"]
    with pytest.raises(SignerError, match="wrong PIN"):
        s.seedvault_secret(index=0, pin="9999")
    with pytest.raises(SignerError, match="wrong PIN"):
        s.set_cap("9999", 10 ** 9)


def test_teleport_secret_needs_the_pin(tmp_path):
    s = Signer(str(tmp_path))
    s.setup("1234", ABANDON, "signet")
    with pytest.raises(Exception):
        s.teleport_secret(pin="9999")
    enc, fp = s.teleport_secret(pin="1234")
    assert kt.decode_secret(enc, N["test"]["xprv"]) == ABANDON
    s.use_temporary(secret=OTHER, passphrase="pp")
    enc, fp = s.teleport_secret(pin="1234")                    # passphrase: sent as the xprv
    assert enc[0] == 1 and fp == seedlib.fingerprint(seedlib.root_key(OTHER, "pp", "signet"))


def test_multisig_psbt_keys_match_on_both_sides(tmp_path):
    a, b = Signer(str(tmp_path / "a")), Signer(str(tmp_path / "b"))
    ia = a.setup("1111", ABANDON, "signet")
    ib = b.setup("1111", OTHER, "signet")
    keys = [ia["multisig_key"], ib["multisig_key"], cosigner_key(new_root(), "signet")]
    ea = a.register_multisig(build_descriptor(2, keys), "Fam")
    b.register_multisig(build_descriptor(2, keys), "Fam")
    nonce = kt.new_nonce()
    mine, his = a.kt_ms_target(wallet_id=ea["id"], fingerprint=ib["fingerprint"], nonce=nonce)
    noid, _ = kt.new_password()
    body = kt.seal(mine, his, noid, b"pPSBT", with_pubkey=False)
    opened = [kt.open_outer(m, h, body) for _, m, h in b.kt_ms_candidates(nonce)]
    found = [o for o in opened if o]
    assert len(found) == 1 and kt.open_inner(found[0][0], noid, found[0][1]) == ("p", b"PSBT")
    assert sorted(b.ms_cosigners(wallet_id=ea["id"])) == sorted(k[1:9] for k in keys)
    with pytest.raises(SignerError):
        a.kt_ms_target(wallet_id=ea["id"], fingerprint=ia["fingerprint"], nonce=nonce)


def test_locked_policy(tmp_path):
    s = Signer(str(tmp_path))
    s.setup("1234", ABANDON, "signet")
    s.set_bypass("1234", "7777")
    with pytest.raises(SignerError, match="policy"):
        s.kt_rx_key()


def test_the_app_cannot_reach_it(app):  # noqa: F811
    from core import signer_service
    create_wallet(app)
    ts = app.signer._local
    for name in ("kt_rx_key", "kt_rx_done", "teleport_secret", "seedvault_secret",
                 "kt_ms_candidates", "kt_ms_target"):
        r = signer_service._dispatch(ts, {"method": name, "params": {}})
        assert r["ok"] is False and r["type"] == "BadRequest", name


# -------------------------------------------------------------- two devices
def _frames(app):
    scr = app.visible
    assert isinstance(scr, AnimatedQRScreen)
    return list(scr.frames)


def _scan(frames):
    col = Collector()
    for f in frames:
        col.add(f)
    assert col.result and col.result.kind == "teleport"
    return col.result


def _pick(app, label):
    app.visible.sel = [i[0] for i in app.visible.items].index(label)
    enter(app)
    settle(app)


@pytest.fixture
def two(tmp_path, monkeypatch):
    """Two devices (two app fixtures can't share a test: build the second)."""
    import core.config
    from app import App
    from core.signer_service import SignerClient

    def make_app(d):
        monkeypatch.setattr(core.config, "APP_DIR", str(d / "app"))
        x = App(signer=SignerClient(inproc=True, data_dir=str(d / "signer")), sync_jobs=True)
        x.settings.network = "signet"
        x.outbox_dir = str(d / "outbox")
        x.start()
        return x
    a = make_app(tmp_path / "a")
    b = make_app(tmp_path / "b")
    create_wallet(a)
    create_wallet(b)
    return a, b


def _start_receive(a):
    a.prompt("kt_receive_prompt", lambda app, r: None)
    settle(a)
    rq = _frames(a)
    code = "".join(a.visible.lines[1].split())
    return rq, code


def _send_to(b, rq, code, *picks):
    from ui.flow import teleport_scanned
    teleport_scanned(b, _scan(rq))
    settle(b)
    type_text(b, code)
    enter(b)
    for p in picks:
        _pick(b, p)


def _receive(a, sq, pw):
    from ui.flow import teleport_scanned
    got = []
    res = _scan(sq)
    a.prompt("kt_incoming_prompt", lambda app, r: got.append(r), kind=res.text,
             payload=base64.b64encode(res.data).decode())
    settle(a)
    type_text(a, pw)
    enter(a)
    settle(a)
    return got


def test_quick_message_between_two_devices(two):
    a, b = two
    rq, code = _start_receive(a)
    assert len(rq) == 1 and rq[0].startswith("B$2R0100")
    enter(a)                                         # leave the QR; the key waits
    _send_to(b, rq, code, "QUICK MESSAGE")
    type_text(b, "meet at noon")
    enter(b)
    assert b.visible.title == "TELEPORT PASSWORD"
    pw = "".join(b.visible.lines[0].split())
    enter(b)
    sq = _frames(b)
    assert sq[0].startswith("B$2S")
    _receive(a, sq, pw)
    assert "meet at noon" in " ".join(a.visible.lines)
    assert a.signer._local.signer.kt_rx_key(make=False) is None          # used up


def test_seed_from_the_vault_between_two_devices(two):
    a, b = two
    sb = b.signer._local.signer
    sb.seedvault_add(secret=OTHER, label="Old")
    rq, code = _start_receive(a)
    enter(a)
    _send_to(b, rq, code, "FROM SEED VAULT", "Old")
    from test_app import press
    assert b.visible.title == "SEND A SEED?"
    press(b, "KEY_Z")                                # SEND IT
    enter(b)
    type_text(b, "1234")                             # a vault seed needs the PIN too
    enter(b)
    settle(b)
    pw = "".join(b.visible.lines[0].split())
    enter(b)
    got = _receive(a, _frames(b), pw)
    assert a.visible.title == "SEED RECEIVED"
    assert seedlib.fingerprint(seedlib.root_key(OTHER, "", "signet")).upper() in " ".join(a.visible.info)
    _pick(a, "USE AS TEMPORARY")
    assert got[0]["temporary"]["fingerprint"] == seedlib.fingerprint(seedlib.root_key(OTHER, "", "signet"))


def test_wrong_passwords(two):
    a, b = two
    rq, code = _start_receive(a)
    enter(a)
    from ui.flow import teleport_scanned
    teleport_scanned(b, _scan(rq))
    settle(b)
    bad = "%08d" % ((int(code) + 1) % 100_000_000)
    type_text(b, bad)
    enter(b)
    if b.visible.title == "KEY TELEPORT":            # caught right away (about half the time)
        assert b.visible.error
        return
    _pick(b, "QUICK MESSAGE")                        # else: the package won't open
    type_text(b, "hi")
    enter(b)
    pw = "".join(b.visible.lines[0].split())
    enter(b)
    res = _scan(_frames(b))
    a.prompt("kt_incoming_prompt", lambda app, r: None, kind="S",
             payload=base64.b64encode(res.data).decode())
    settle(a)
    assert isinstance(a.visible, MessageScreen) and a.visible.title == "CAN'T OPEN IT"
    assert pw


def test_multisig_psbt_to_a_cosigner(two, monkeypatch):
    from core.config import Settings
    from core.wallet import WalletService
    from embit.script import address_to_scriptpubkey
    from test_core import DEST, fake_tx
    monkeypatch.setenv("CZ_FORCE_ONLINE", "0")
    a, b = two
    sa, sb = a.signer._local.signer, b.signer._local.signer
    keys = [sa.public_info()["multisig_key"], sb.public_info()["multisig_key"],
            cosigner_key(new_root(), "signet")]
    entry = sa.register_multisig(build_descriptor(2, keys), "Fam")
    sb.register_multisig(build_descriptor(2, keys), "Fam")
    w = WalletService(Settings(network="signet"), entry["descriptor"], None, entry["id"],
                      in_memory=True)
    _, addr = w.receive_address()
    w.record_unconfirmed(fake_tx(address_to_scriptpubkey(addr), 300_000).serialize())
    psbt = w.build(DEST, 80_000, 4)["psbt"]
    a.prompt("kt_psbt_prompt", lambda app, r: None, psbt=psbt)
    settle(a)
    fp_b = sb.public_info()["fingerprint"].upper()
    assert len(a.visible.items) == 2 and fp_b in [i[0] for i in a.visible.items]
    _pick(a, fp_b)
    pw = "".join(a.visible.lines[0].split())
    enter(a)
    frames = _frames(a)
    assert frames[0].startswith("B$2E")
    got = _receive(b, frames, pw)
    assert b.visible.title == "PSBT RECEIVED"
    enter(b)
    assert got[0]["psbt"] == psbt
