"""
Regression tests for the November 2026 security review (docs/AUDIT.md).
Each test names the finding it pins down.
"""
import datetime as dt
import hashlib
import os
import sys
from pathlib import Path

os.environ.setdefault("CZ_ARGON_T", "1")
os.environ.setdefault("CZ_ARGON_M_KIB", "1024")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from embit.psbt import PSBT  # noqa: E402
from embit.transaction import TransactionOutput  # noqa: E402

from core import signer as signer_mod  # noqa: E402
from core.signer import SignerError  # noqa: E402
from test_core import DEST, rig  # noqa: E402,F401
from test_app import app  # noqa: E402,F401


def _edit(psbt_b64, fn):
    p = PSBT.from_string(psbt_b64)
    fn(p)
    return p.to_string()


# ----------------------------------------------------------- F1 fee attack
def test_f1_v0_input_needs_its_previous_transaction(rig):
    signer, wallet = rig
    built = wallet.build(DEST, 30_000, fee_rate=5)

    def drop(p):
        p.inputs[0].non_witness_utxo = None
    with pytest.raises(SignerError, match="previous transaction"):
        signer.describe(_edit(built["psbt"], drop))


def test_f1_lying_amount_is_refused(rig):
    signer, wallet = rig
    built = wallet.build(DEST, 30_000, fee_rate=5)

    def lie(p):
        w = p.inputs[0].witness_utxo
        p.inputs[0].witness_utxo = TransactionOutput(w.value - 50_000, w.script_pubkey)
    with pytest.raises(SignerError, match="amount doesn't match"):
        signer.describe(_edit(built["psbt"], lie))


def test_f1_previous_transaction_must_hash_to_the_txid(rig):
    signer, wallet = rig
    built = wallet.build(DEST, 30_000, fee_rate=5)

    def swap(p):
        prev = p.inputs[0].non_witness_utxo
        prev.locktime = prev.locktime + 1            # different txid, same outputs
    with pytest.raises(SignerError, match="doesn't match"):
        signer.describe(_edit(built["psbt"], swap))


# ------------------------------------------------ F2 confirm the whole PSBT
def test_f2_sign_refuses_a_psbt_changed_after_confirming(rig):
    signer, wallet = rig
    built = wallet.build(DEST, 30_000, fee_rate=5)
    s = signer.describe(built["psbt"])

    def relabel(p):                                  # same transaction, other metadata
        p.inputs[0].witness_utxo = None
    with pytest.raises(SignerError, match="changed after confirmation"):
        signer.sign(_edit(built["psbt"], relabel), s["digest"])
    assert signer.sign(built["psbt"], s["digest"])   # the confirmed one still signs


# ------------------------------------------------------ F3 cap and the clock
def test_f3_moving_the_clock_doesnt_refill_the_cap(rig, monkeypatch):
    signer, wallet = rig
    monkeypatch.setenv("CZ_FORCE_ONLINE", "1")
    signer.set_cap("2468", 40_000)
    built = wallet.build(DEST, 30_000, fee_rate=5)
    s = signer.describe(built["psbt"])
    signer.sign(built["psbt"], s["digest"])
    assert signer._cap_state()["spent"] > 30_000

    class Tomorrow(dt.date):
        @classmethod
        def today(cls):
            return dt.date(2099, 1, 1)
    monkeypatch.setattr(signer_mod.dt, "date", Tomorrow)
    assert signer._cap_state()["spent"] > 30_000     # same boot, minutes later: no reset
    up = signer_mod._boot_clock()
    monkeypatch.setattr(signer_mod, "_boot_clock", lambda: up + signer_mod.CAP_DAY_S + 1)
    assert signer._cap_state()["spent"] == 0         # a real day later: reset
    monkeypatch.setattr(signer_mod, "_boot_clock", lambda: up)
    monkeypatch.setattr(signer_mod, "_boot_id", lambda: "another-boot")
    assert signer._cap_state()["spent"] == 0         # after a reboot the date decides


# ------------------------------------------- F6 change far beyond the gap
def test_f6_far_away_change_is_flagged(rig):
    signer, wallet = rig
    built = wallet.build(DEST, 30_000, fee_rate=5)
    s = signer.describe(built["psbt"])
    assert not any("may not show" in w for w in s["warnings"])
    p = PSBT.from_string(built["psbt"])
    ci = next(i for i, o in enumerate(s["outputs"]) if o["kind"] == "change")
    root = signer._root
    der = next(iter(p.outputs[ci].bip32_derivations.values()))
    path = list(der.derivation[:-1]) + [50_000]
    pub = root.derive(path).get_public_key()
    from embit.script import p2wpkh
    from embit.psbt import DerivationPath
    p.outputs[ci].bip32_derivations = {pub: DerivationPath(der.fingerprint, path)}
    p.outputs[ci].script_pubkey = p2wpkh(pub)
    s2 = signer.describe(p.to_string())
    assert any("#50000" in w for w in s2["warnings"])


# ------------------------------------------------- F8 odd signature types
def test_f8_unusual_sighash_refused_up_front(rig):
    signer, wallet = rig
    built = wallet.build(DEST, 30_000, fee_rate=5)

    def none(p):
        p.inputs[0].sighash_type = 2                 # SIGHASH_NONE: outputs could change
    with pytest.raises(SignerError, match="unusual signature type"):
        signer.describe(_edit(built["psbt"], none))


# ----------------------------------- F5 paper wallets need your own password
def test_f5_paper_wallets_never_shared_behind_the_default_password(app, monkeypatch):  # noqa: F811
    from core import share
    from ui.flow import settings_screen
    from ui.share_flow import SharingScreen
    from test_app import create_wallet, enter, settle
    create_wallet(app)
    monkeypatch.setattr(share, "controller",
                        lambda d: share.InProcess(d, port=0, host="127.0.0.1"))
    monkeypatch.setenv("CZ_SIM_SHARE_ANY", "1")
    settings_screen(app, at="SHARE FILES")
    settle(app)
    m = app.visible
    m.sel = [i[0] for i in m.items].index("SHARE FILES")
    enter(app)
    menu = app.visible
    menu.sel = [i[0] for i in menu.items].index("PAPER WALLETS")
    enter(app)                                                # INCLUDE
    menu.sel = 0
    enter(app)
    assert app.visible.title == "SET A PASSWORD"
    app.settings.share_pass_hash = share.hash_password("my-own-pass")
    app.pop()
    enter(app)
    assert isinstance(app.visible, SharingScreen)
    app.visible.on_hide(app) if hasattr(app.visible, "on_hide") else None


# --------------------------------- F9 a hostile Electrum server can't exhaust memory
def test_f9_endless_electrum_line_is_cut_off(monkeypatch):
    import socket
    import threading
    from core import electrum
    monkeypatch.setattr(electrum, "MAX_REPLY", 200_000)
    a, b = socket.socketpair()
    c = electrum.ElectrumClient.__new__(electrum.ElectrumClient)
    c._sock, c._buf, c._id = a, b"", 0

    def flood():
        try:
            while True:
                b.sendall(b"x" * 65536)
        except OSError:
            pass
    threading.Thread(target=flood, daemon=True).start()
    with pytest.raises(electrum.ElectrumError, match="oversized"):
        c.call("server.version", "x", "1.4")
    b.close()


def test_f9_non_object_json_is_an_error_not_a_crash():
    import socket
    from core import electrum
    a, b = socket.socketpair()
    c = electrum.ElectrumClient.__new__(electrum.ElectrumClient)
    c._sock, c._buf, c._id = a, b"", 0
    b.sendall(b"not json\n")
    with pytest.raises(electrum.ElectrumError, match="isn't JSON"):
        c.call("server.version", "x", "1.4")
    a2, b2 = socket.socketpair()
    c._sock, c._buf = a2, b""
    b2.sendall(b'[1,2]\n42\n{"id": 2, "result": "ok"}\n')
    assert c.call("server.version", "x", "1.4") == "ok"


# ----------------------------------- F4 paper wallet keys stay off the card
def _helper():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "copy_backup", os.path.join(os.path.dirname(__file__), "..", "tools", "copy_backup.py"))
    cb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cb)
    return cb


def test_f4_paper_wallets_go_to_the_private_folder_not_the_card(tmp_path, monkeypatch):
    import stat
    from core import paper
    cb = _helper()
    src, dst, card = tmp_path / "signer", tmp_path / "paper", tmp_path / "card-paper"
    src.mkdir()
    card.mkdir()
    for k, v in (("PAPER_SRC", src), ("PAPER_DST", dst), ("PAPER_OLD", card)):
        monkeypatch.setattr(cb, k, str(v))
    new = paper.save(str(src), paper.build(paper.random_key(), "bitcoin"))
    old = paper.save(str(card), paper.build(paper.random_key(), "bitcoin"))   # from 0.8.14
    assert cb.main(["x", "paper"]) == 0
    assert sorted(os.listdir(dst)) == sorted(new + old)
    assert not card.exists()                             # nothing left on the card
    for n in os.listdir(dst):
        assert stat.S_IMODE(os.stat(dst / n).st_mode) == 0o640   # no one else can read
    assert not cb.PAPER_DST.startswith("/boot")


def test_f4_share_serves_paper_from_the_private_folder(monkeypatch):
    from core import share
    monkeypatch.delenv("CZ_SIM", raising=False)
    monkeypatch.setattr(share.os.path, "isdir", lambda p: p == "/boot/firmware")
    f = share.folders()
    assert f["paper"] == "/var/lib/czwallet/paper"
    svc = open(os.path.join(os.path.dirname(__file__), "..", "deploy", "czshare.service")).read()
    assert "BindPaths=-/var/lib/czwallet/paper" in svc and "InsertCoin-paper" not in svc


# The project's donation address (README and website), checked on the
# owner's device. The only real address allowed outside the tests.
DONATION = "bc1qca69hxsnkwtqn7j9wmjeyg5tutwx38rdwfzx8k"


def test_no_real_addresses_in_the_app():
    """Example addresses copied from tutorials belong to someone (one well-known
    one holds coins people sent by mistake). Only tests may contain real
    mainnet ones; testnet examples (worthless coins) are fine in the tools."""
    import os
    import re
    from embit.script import address_to_scriptpubkey
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pat = re.compile(r"\bbc1[02-9ac-hj-np-z]{38,58}\b|\b[13][1-9A-HJ-NP-Za-km-z]{25,34}\b")
    found = []
    for top in ("core", "ui", "app.py", "sim.py", "deploy", "tools", "site"):
        base = os.path.join(root, top)
        paths = [base] if os.path.isfile(base) else [
            os.path.join(d, f) for d, _, fs in os.walk(base) for f in fs
            if f.endswith((".py", ".html", ".js", ".sh"))]
        for p in paths:
            for m in pat.findall(open(p, encoding="utf-8", errors="replace").read()):
                try:
                    address_to_scriptpubkey(m)
                    if m != DONATION:       # the project's own, on purpose
                        found.append((os.path.relpath(p, root), m))
                except Exception:  # noqa: BLE001
                    pass                    # not a valid address: fine
    assert found == []


def test_root_helpers_can_write_where_they_must():
    """The app starts cz-backup and cz-update with sudo, and they run inside
    the app's ProtectSystem=strict view, where every path is read-only unless
    the unit opens it. Paper wallets never reached PRINT because of this."""
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    unit = open(os.path.join(root, "deploy", "czwallet.service")).read()
    opened = []
    for line in unit.splitlines():
        if line.startswith("ReadWritePaths="):
            opened += [p.lstrip("-") for p in line.split("=", 1)[1].split()]
    from tools import copy_backup as cb
    from core import update
    needed = [cb.PAPER_DST, cb.PAPER_SRC, cb.PAPER_OLD, cb.DST, cb.EXPORT_DST,
              update.RELEASES, os.path.dirname(update.LINK)]
    for path in needed:
        assert any(path == o or path.startswith(o.rstrip("/") + "/") for o in opened), path
    # the card's folders must exist before the app starts, or they stay shut
    for d in (cb.DST, cb.EXPORT_DST):
        assert d in unit.split("ExecStartPre=-+/bin/mkdir -p", 1)[1].splitlines()[0]
    # and only the wallet's own folders on the card, never the boot files
    assert "/boot/firmware" not in opened and "/boot" not in opened and "/" not in opened


def test_a_failed_copy_to_the_card_is_reported(monkeypatch):
    import subprocess
    from core import backup, history_export
    monkeypatch.delenv("CZ_SIM", raising=False)
    monkeypatch.setattr("os.path.exists", lambda p: True)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1))
    import pytest
    for mod in (backup, history_export):
        with pytest.raises(RuntimeError):
            mod._copy_to_card("x")


def test_paper_move_reports_failure(monkeypatch):
    import subprocess
    from ui import flow
    monkeypatch.delenv("CZ_SIM", raising=False)
    monkeypatch.setattr("os.path.exists", lambda p: True)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1))
    assert flow.move_paper() is False
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0))
    assert flow.move_paper() is True
