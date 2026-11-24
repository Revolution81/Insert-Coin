"""
SETUP › TOOLS › KEY TELEPORT, on the signer's screens (COLDCARD's protocol,
core/teleport.py).

RECEIVE shows a QR and an 8-digit RECEIVER PASSWORD. The sender scans the
QR, gets the 8 digits by another channel (a call), picks what to send and
shows a QR back plus an 8-character TELEPORT PASSWORD. The receiver scans
that (Q on the home screen) and types the password.

What can go: a quick message, notes & passwords, a seed vault entry, the
seed in use (after the PIN). A multisig PSBT goes to a co-signer with no
setup at all (the wallet's own keys do the job).
"""
from __future__ import annotations

import base64

from core import notes as nl
from core import seed as seedlib
from core import teleport as kt
from core.qr import bbqr_encode

from .qrscreens import AnimatedQRScreen
from .widgets import ConfirmScreen, MenuScreen, MessageScreen, TextInputScreen

PW_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567abcdefghijklmnopqrstuvwxyz018 "


def _end(c, screen, value=None):
    screen.on_hide = lambda c: c.finish(value)
    c.push(screen)


def _err(c, title, lines):
    c.push(MessageScreen(title, lines, border="red"))


def _group(text: str, n: int = 4) -> str:
    return " ".join(text[i:i + n] for i in range(0, len(text), n))


# ================================================================ receive
def receive_start(c):
    """Show the receive QR. An unused key is shown again (so a sender who
    already scanned it still works) unless NEW KEY is picked."""
    def show(c, new):
        secret = c.signer.kt_rx_key(new=new)
        code, enc = kt.rx_code(secret)
        frames = bbqr_encode(enc, "R", compress=False)
        _end(c, AnimatedQRScreen("KEY TELEPORT", frames, [
            "RECEIVER PASSWORD:", _group(code), "Say it to the", "sender by phone,",
            "not with the QR.", "Then scan theirs", "with Q."]))

    if c.signer.kt_rx_key(make=False) is None:
        show(c, False)
        return
    c.push(MenuScreen("KEY TELEPORT", [("SHOW MY QR", lambda c: (c.pop(), show(c, False))),
                                       ("NEW KEY", lambda c: (c.pop(), show(c, True)), "", "grey")],
                      info=["A receive key is waiting.", "NEW KEY makes the old QR", "useless."]))


def receive(c, kind: str, payload: bytes):
    """A scanned S (from a receive key) or E (multisig PSBT) package."""
    found = None
    if kind == "S":
        if len(payload) < 36:
            raise ValueError("too short")
        rx = c.signer.kt_rx_key(make=False)
        if rx is None:
            _end(c, MessageScreen("NOT EXPECTED", [
                "No receive key is waiting.", "Start with SETUP › TOOLS ›", "KEY TELEPORT › RECEIVE",
                "and show the sender its QR."], border="red"))
            return
        found = kt.open_outer(rx, payload[:33], payload[33:])
    elif kind == "E":
        if len(payload) < 7:
            raise ValueError("too short")
        nonce = int.from_bytes(payload[:4], "big")
        for _fp, mine, his in c.signer.kt_ms_candidates(nonce):
            found = kt.open_outer(mine, his, payload[4:])
            if found:
                break
    else:
        raise ValueError("unknown package")
    if not found:
        _end(c, MessageScreen("CAN'T OPEN IT", [
            "Meant for another device, a", "wrong receiver password on", "the sender's side, or the",
            "QR didn't scan whole."] if kind == "S" else [
            "Not from a co-signer of a", "multisig wallet registered", "on this device."], border="red"))
        return
    sk, inner = found

    def typed(c, text):
        try:
            got = kt.open_inner(sk, kt.password_bytes(text), inner)
        except kt.TeleportError as e:
            return str(e)
        if got is None:
            return "Wrong teleport password"
        c.pop()
        if kind == "S":
            c.signer.kt_rx_done()
        _received(c, *got)
        return None
    c.push(TextInputScreen("KEY TELEPORT", "Teleport password (8 chars):", typed,
                           allowed=PW_CHARS, max_len=11))


def _received(c, dtype: str, body: bytes):
    net = c.signer.status()["network"]
    if dtype == "p":
        _end(c, MessageScreen("PSBT RECEIVED", ["A PSBT arrived. Next you'll", "see it and can sign it."]),
             {"psbt": base64.b64encode(body).decode()})
        return
    if dtype == "n":
        try:
            entries = kt.notes_in(body)
        except kt.TeleportError as e:
            _end(c, MessageScreen("CAN'T READ IT", [str(e)], border="red"))
            return
        if len(entries) == 1:                         # one note, password or quick message
            e = entries[0]
            from .notes_flow import NoteScreen, PasswordScreen

            def save(c):
                n = _save_notes(c, entries)
                _end(c, MessageScreen("SAVED", [f"{n} saved in NOTES &", "PASSWORDS."]))
            c.push(MenuScreen("RECEIVED", [("SAVE IN NOTES", save), ("DONE", lambda c: c.finish(None))],
                              info=[f"\"{e['title'][:24]}\"", "Not saved unless you say so."]))
            c.push(PasswordScreen(e) if e["kind"] == "password" else NoteScreen(e))
            return
        n = _save_notes(c, entries)
        _end(c, MessageScreen("RECEIVED", [f"{n} of {len(entries)} saved in", "NOTES & PASSWORDS",
                                           "(copies already there skipped)."]))
        return
    label = ""
    try:
        if dtype == "s":
            secret = kt.decode_secret(body, _xprv_version(net))
        elif dtype == "v":
            raw, label = kt.vault_in(body)
            secret = kt.decode_secret(raw, _xprv_version(net))
        elif dtype == "x":
            secret = kt.decode_secret(body, _xprv_version(net))
        elif dtype == "b":
            raw, notes = kt.backup_in(body)
            secret = kt.decode_secret(raw, _xprv_version(net))
            if notes:
                _save_notes(c, kt.notes_in(__import__("json").dumps(notes).encode()))
        else:
            raise kt.TeleportError("unknown package")
    except (kt.TeleportError, ValueError) as e:
        _end(c, MessageScreen("CAN'T READ IT", [str(e)[:40]], border="red"))
        return
    _got_seed(c, secret, label, net)


def _xprv_version(net):
    from core.signer import EMBIT_NETS, NETWORKS
    return EMBIT_NETS[NETWORKS[net][1]]["xprv"]


def _save_notes(c, entries) -> int:
    mine = c.signer.notes()
    n = 0
    for e in entries:
        try:
            e = nl.clean(e)
        except nl.NoteError:
            continue
        if any(m.get("title") == e["title"] and m.get("text") == e.get("text")
               and m.get("password") == e.get("password") for m in mine):
            continue
        try:
            c.signer.note_save(entry=e)
            n += 1
        except Exception:  # noqa: BLE001 - full, or bad: skip it
            break
    return n


def _got_seed(c, secret: str, label: str, net: str):
    fp = seedlib.fingerprint(seedlib.root_key(secret, "", net)).upper()
    kind = "XPRV" if seedlib.is_xprv(secret) else f"{len(secret.split())} words"

    def use(c):
        c.job("OPENING SEED...", lambda: c.signer.use_temporary(secret=secret, label=label or "TELEPORT"),
              lambda c, info: c.finish({"temporary": info}),
              lambda c, e: _err(c, "CAN'T USE IT", [str(e)[:40]]))

    def save(c):
        def named(c, name):
            if not name.strip():
                return "Give it a name"
            c.pop()
            try:
                c.signer.seedvault_add(secret=secret, label=name.strip())
            except Exception as e:  # noqa: BLE001 - shown
                _err(c, "NOT SAVED", [str(e)[:40]])
                return None
            c.push(MessageScreen("SAVED", [f"\"{name.strip()[:16]}\" is in", "the seed vault."]))
            return None
        c.push(TextInputScreen("NAME", "Name in the vault:", named, max_len=16, initial=label))

    c.push(MenuScreen("SEED RECEIVED", [("USE AS TEMPORARY", use, "UNTIL LOCK", "gold"),
                                        ("SAVE IN SEED VAULT", save),
                                        ("DONE", lambda c: c.finish(None))],
                      info=[f"Fingerprint {fp}", kind + (f", \"{label}\"" if label else ""),
                            "Your own seed isn't changed."]))


# =================================================================== send
def send(c, rx: bytes):
    """After scanning someone's receive QR: their 8 digits, then what to send."""
    def typed(c, code):
        pub = kt.decrypt_rx("".join(code.split()), rx)
        if pub is None:
            return "Wrong receiver password"
        c.pop()
        _what(c, pub)
        return None
    c.push(TextInputScreen("KEY TELEPORT", "Receiver password (8 digits):", typed,
                           allowed="0123456789 ", max_len=9))


def _what(c, pub: bytes):
    def quick(c):
        def got(c, text):
            if not text.strip():
                return "Type something"
            c.pop()
            _seal(c, pub, b"n" + kt.notes_out([{"kind": "note", "title": "Quick Message",
                                                 "text": text}]))
            return None
        c.push(TextInputScreen("QUICK MESSAGE", "Message:", got, max_len=nl.LIMITS["text"]))

    def one_note(c):
        rows = [(e["title"][:20], (lambda e: lambda c: _seal(c, pub, b"n" + kt.notes_out([e])))(e),
                 "PW" if e["kind"] == "password" else "NOTE") for e in c.signer.notes()]
        if not rows:
            _err(c, "NO NOTES", ["Nothing in NOTES &", "PASSWORDS yet."])
            return
        c.push(MenuScreen("WHICH ONE?", rows))

    def all_notes(c):
        entries = c.signer.notes()
        if not entries:
            _err(c, "NO NOTES", ["Nothing in NOTES &", "PASSWORDS yet."])
            return
        _seal(c, pub, b"n" + kt.notes_out(entries))

    def vault(c):
        rows = []
        for i, e in enumerate(c.signer.seedvault()):
            def go(c, i=i):
                def pinned(c):
                    from .secure_flows import _pin_job
                    _pin_job(c, "SEND SEED", "CHECKING PIN...",
                             lambda pin: c.signer.seedvault_secret(index=i, pin=pin),
                             lambda c, r: (c.pop(), _seal(c, pub, b"v" + kt.vault_out(r[1], r[0], r[2]))),
                             note="To send a seed from the vault")
                _warn(c, pinned)
            rows.append((e["label"], go, e["kind"]))
        if not rows:
            _err(c, "EMPTY", ["The seed vault is empty."])
            return
        c.push(MenuScreen("SEED VAULT", rows))

    def master(c):
        def go(c):
            from .secure_flows import _pin_job
            _pin_job(c, "SEND SEED", "CHECKING PIN...",
                     lambda pin: c.signer.teleport_secret(pin=pin),
                     lambda c, r: (c.pop(), _seal(c, pub, b"s" + r[0])),
                     note="To send the seed in use")
        _warn(c, go)

    c.push(MenuScreen("SEND WHAT?", [("QUICK MESSAGE", quick), ("A NOTE OR PASSWORD", one_note),
                                     ("ALL NOTES & PASSWORDS", all_notes),
                                     ("FROM SEED VAULT", vault, "", "orange"),
                                     ("THE SEED IN USE", master, "PIN", "red")],
                      info=["Encrypted for that device", "only, plus a password."]))


def _warn(c, then):
    c.push(ConfirmScreen("SEND A SEED?", [
        "Anyone who gets it can spend", "everything it holds. Send it",
        "only to a device you trust,", "and say the passwords out", "loud, not in a message."],
        then, danger=True, yes="SEND IT"))


def _seal(c, pub: bytes, cleartext: bytes):
    noid, pw = kt.new_password()
    try:
        payload = kt.seal(kt.new_secret(), pub, noid, cleartext)
    except kt.TeleportError as e:
        _err(c, "CAN'T SEND", [str(e)])
        return
    _show(c, bbqr_encode(payload, "S", max_chars=280, compress=False), pw)


def _show(c, frames, pw):
    qr = AnimatedQRScreen("KEY TELEPORT", frames, [
        "TELEPORT PASSWORD:", _group(pw), "Let them scan it,", "then say the",
        "password by phone."])
    _end(c, qr)
    c.push(MessageScreen("TELEPORT PASSWORD", [_group(pw), "", "Write it down or say it:",
                                               "the receiver types it after", "scanning the next QR."],
                         border="gold"))


# ============================================================ multisig PSBT
def send_psbt(c, psbt_b64: str):
    """A PSBT of a registered multisig wallet, to one of its co-signers."""
    try:
        d = c.signer.describe(psbt=psbt_b64)
    except Exception as e:  # noqa: BLE001 - shown
        _end(c, MessageScreen("CAN'T SEND IT", [str(e)[:40]], border="red"))
        return
    wid = d.get("wallet_id")
    if not wid:
        _end(c, MessageScreen("NOT MULTISIG", ["Only a multisig PSBT goes", "to a co-signer."],
                              border="red"))
        return
    mine = c.signer.public_info()["fingerprint"]
    signed = _signed_by(psbt_b64)
    rows = []
    for fp in c.signer.ms_cosigners(wallet_id=wid):
        if fp == mine:
            continue
        rows.append((fp.upper(), (lambda fp: lambda c: _psbt_to(c, wid, fp, psbt_b64))(fp),
                     "SIGNED" if fp in signed else "", "green" if fp in signed else "grey"))
    c.push(MenuScreen("SEND TO WHICH?", rows, info=[d.get("wallet", "")[:28],
                                                    f"{d.get('sigs_needed')} signatures needed."]))


def _signed_by(psbt_b64: str) -> set[str]:
    from embit.psbt import PSBT
    out = set()
    try:
        p = PSBT.from_string(psbt_b64)
        for inp in p.inputs:
            for pub in inp.partial_sigs:
                der = inp.bip32_derivations.get(pub)
                if der:
                    out.add(der.fingerprint.hex())
    except Exception:  # noqa: BLE001 - just no SIGNED marks
        pass
    return out


def _psbt_to(c, wid, fp, psbt_b64):
    nonce = kt.new_nonce()
    mine, his = c.signer.kt_ms_target(wallet_id=wid, fingerprint=fp, nonce=nonce)
    noid, pw = kt.new_password()
    body = kt.seal(mine, his, noid, b"p" + base64.b64decode(psbt_b64), with_pubkey=False)
    _show(c, bbqr_encode(nonce.to_bytes(4, "big") + body, "E", max_chars=280, compress=False), pw)
