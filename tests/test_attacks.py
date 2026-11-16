"""
Attack tests (0.8.20 review): what a compromised app, a thief, or someone
with the microSD card could try, and that each attempt fails safely.
"""
import json
import os
import random
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("CZ_ARGON_T", "1")
os.environ.setdefault("CZ_ARGON_M_KIB", "1024")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from core import signer_service, trusted  # noqa: E402
from core import vault as vaultmod  # noqa: E402
from core.signer import Signer, SignerError  # noqa: E402
from core.vault import Vault, Wiped, WrongPin  # noqa: E402
from test_core import ABANDON, DEST, NET, rig  # noqa: E402,F401
from test_app import app, create_wallet  # noqa: E402,F401

JUNK = [None, True, 0, -1, 2 ** 63, 3.5, "", "x" * 100_000, "../../etc/passwd", "\x00",
        [], [1, [2, [3]]], {}, {"a": {"b": {"c": None}}}, "1234", "2468", b"x".hex()]


def junk_params(rnd):
    keys = ["pin", "psbt", "digest", "seconds", "field", "value", "action", "account",
            "index", "message", "wallet_id", "descriptor", "rule", "hours", "network",
            "mnemonic", "secret", "code", "bypass_pin", "evil", "__class__"]
    return {rnd.choice(keys): rnd.choice(JUNK) for _ in range(rnd.randrange(0, 4))}


# ----------------------------------------------------- a compromised app
def test_app_cannot_reach_anything_outside_the_two_lists(app):  # noqa: F811
    create_wallet(app)
    ts = app.signer._local
    raw = [n for n in dir(Signer) if not n.startswith("__")]
    for name in raw + ["__init__", "__class__", "__dict__", "_root", "vault", "_dispatch",
                       "finish_login", "reveal_words", "set_policy", "set_bypass",
                       "set_totp", "check_pin", "wipe_all", "sign"]:
        if name in signer_service.METHODS:
            continue
        r = signer_service._dispatch(ts, {"method": name, "params": {}})
        assert r == {"ok": False, "error": "unknown method", "type": "BadRequest"}, name
    for bad in (None, [], "x", {"method": "status", "params": []},
                {"method": "status", "params": "pin=1"}):
        assert signer_service._dispatch(ts, bad)["ok"] is False


def test_junk_to_every_no_pin_request_is_a_clean_error(app):  # noqa: F811
    create_wallet(app)
    ts = app.signer._local
    rnd = random.Random(7)
    for name in sorted(trusted.SAFE):
        for _ in range(60):
            r = signer_service._dispatch(ts, {"method": name, "params": junk_params(rnd)})
            assert isinstance(r, dict) and "ok" in r
            json.dumps(r)                                     # always serialisable
    st = ts.signer.status()                                    # still answering, unharmed
    assert st["has_seed"] and st["tries_left"] == st["max_tries"]
    assert not ts.signer.status().get("policy", {}).get("locked")


def test_junk_to_every_prompt_never_hangs_or_leaks(app):  # noqa: F811
    create_wallet(app)
    ts = app.signer._local
    console = app.signer.console
    rnd = random.Random(11)
    for name in sorted(trusted.PROMPTS):
        for _ in range(8):
            out = {}
            t = threading.Thread(target=lambda: out.setdefault(
                "r", signer_service._dispatch(ts, {"method": name,
                                                   "params": junk_params(rnd)})), daemon=True)
            t.start()
            end = time.monotonic() + 5
            while t.is_alive() and time.monotonic() < end:
                time.sleep(0.02)
                console.cancel()                              # nobody's at the keys
            t.join(1)
            assert not t.is_alive(), name
            r = out["r"]
            assert r["ok"] is False or name in ("lock",), (name, r)
            assert "2468" not in json.dumps(r) and "abandon" not in json.dumps(r)


def test_prompt_flood_gets_busy_not_stacked(app):  # noqa: F811
    create_wallet(app)
    ts = app.signer._local
    results = []
    threads = [threading.Thread(target=lambda: results.append(signer_service._dispatch(
        ts, {"method": "words_prompt", "params": {}})), daemon=True) for _ in range(8)]
    for t in threads:
        t.start()
    time.sleep(0.3)
    busy = [r for r in results if r.get("type") == "Busy"]
    assert len(busy) >= 6                                     # one prompt at a time
    for _ in range(50):
        app.signer.console.cancel()
        time.sleep(0.01)
    for t in threads:
        t.join(2)


# ----------------------------------------------------- a thief with the device
def test_counter_survives_a_power_cut_mid_guess(tmp_path, monkeypatch):
    s = Signer(str(tmp_path / "s"))
    s.setup("2468", ABANDON, NET)
    s.lock()
    before = s.vault.info()["tries_left"]

    def cut(*a, **k):
        raise SystemExit("power cut")
    monkeypatch.setattr(vaultmod.Vault, "_try_key", cut)
    with pytest.raises(SystemExit):
        s.unlock("0000")
    monkeypatch.undo()
    fresh = Signer(str(tmp_path / "s"))                       # after the reboot
    assert fresh.vault.info()["tries_left"] == before - 1


def test_every_pin_costs_the_same_work(tmp_path, monkeypatch):
    """Wrong, duress, wipe and right PINs all do exactly one Argon2 run, so
    timing doesn't tell them apart."""
    v = Vault(str(tmp_path / "v"))
    v.create("1111", ABANDON, NET)
    v.set_duress("1111", "2222", ABANDON)
    v.set_wipe_pin("1111", "3333")
    calls = []
    real = vaultmod.hash_secret_raw
    monkeypatch.setattr(vaultmod, "hash_secret_raw", lambda *a, **k: calls.append(1) or real(*a, **k))
    for pin in ("1111", "2222", "9999"):
        calls.clear()
        try:
            v.unlock(pin)
        except WrongPin:
            pass
        assert len(calls) == 1, pin
    calls.clear()
    with pytest.raises(Wiped):
        v.unlock("3333")
    assert len(calls) == 1


def test_ten_wrong_pins_take_everything(app):  # noqa: F811
    from test_app import enter, settle, type_text
    from ui.flow import HomeScreen
    create_wallet(app)
    s = app.signer.local
    s.set_policy("1234", "per_tx", 1000)
    app.lock(full=True)
    settle(app)
    for _ in range(10):
        type_text(app, "0000")
        enter(app)
        settle(app)
    assert app.visible.title == "WIPED"
    assert not s.vault.exists() and os.listdir(s.dir) == []
    assert not isinstance(app.visible, HomeScreen)


# ----------------------------------------------------- someone with the card
def test_tampering_with_the_vault_header_never_helps(tmp_path):
    v = Vault(str(tmp_path / "v"))
    v.create("1111", ABANDON, NET, max_tries=3)
    body = json.load(open(v.path))
    for field, value in (("max_tries", 1_000_000), ("network", "bitcoin"), ("pp_stored", True)):
        bad = dict(body, **{field: value})
        json.dump(bad, open(v.path, "w"))
        with pytest.raises((WrongPin, Wiped)):
            v.unlock("1111")                     # even the right PIN fails: header is sealed
        v._set_tries(0)
    json.dump(body, open(v.path, "w"))
    v.unlock("1111")
    body["slots"].reverse()                       # swapped slots
    json.dump(body, open(v.path, "w"))
    with pytest.raises(WrongPin):
        v.unlock("1111")


def test_file_sizes_dont_reveal_duress_or_wipe_pins(tmp_path):
    sizes = []
    for setup in (lambda v: None, lambda v: v.set_duress("1111", "2222", ABANDON),
                  lambda v: v.set_wipe_pin("1111", "3333")):
        v = Vault(str(tmp_path / str(len(sizes))))
        v.create("1111", ABANDON, NET)
        setup(v)
        body = json.load(open(v.path))
        sizes.append((sorted(body), [len(s["ct"]) for s in body["slots"]], len(body["w"])))
    assert sizes[0] == sizes[1] == sizes[2]


# ----------------------------------------------------- hostile PSBTs
def test_a_giant_psbt_is_refused_quickly(rig):  # noqa: F811
    from embit.psbt import PSBT
    from embit.transaction import TransactionOutput
    from embit.script import address_to_scriptpubkey
    signer, wallet = rig
    b = wallet.build(DEST, 5_000, fee_rate=5)
    p = PSBT.from_string(b["psbt"])
    spk = address_to_scriptpubkey(DEST)
    from embit.psbt import OutputScope
    for _ in range(3000):
        o = OutputScope()
        o.script_pubkey, o.value = spk, 1
        p.outputs.append(o)
    t0 = time.monotonic()
    with pytest.raises(SignerError, match="too big"):
        signer.describe(p.to_string())
    assert time.monotonic() - t0 < 10


def test_signing_twice_needs_confirming_twice(rig):  # noqa: F811
    signer, wallet = rig
    b = wallet.build(DEST, 5_000, fee_rate=5)
    s = signer.describe(b["psbt"])
    signer.sign(b["psbt"], s["digest"])
    with pytest.raises(SignerError, match="confirm"):
        signer.sign(b["psbt"], s["digest"])


def test_a_prompt_cancelled_before_it_starts_opens_nothing(app):  # noqa: F811
    create_wallet(app)
    c = app.signer.console
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", signer_service._dispatch(
        app.signer._local, {"method": "words_prompt", "params": {}})), daemon=True)
    t.start()
    for _ in range(100):
        if c._done is not None:
            break
        time.sleep(0.005)
    c.cancel()                       # before the console ran the prompt's first step
    t.join(2)
    c.pump()                         # now the stale first step comes up: it must do nothing
    assert c.stack == [] and out["r"]["ok"] is False
