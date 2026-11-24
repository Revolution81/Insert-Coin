"""
SETUP › SECURITY › CO-SIGN, on the signer's screens (COLDCARD's CCC idea).

Key C lives on this device with its own spending rules. With a 2-of-3
wallet of A (your seed), C and B (a backup key kept elsewhere), payments
inside C's rules need only this device; bigger ones need B. Seeing,
changing or removing the rules takes C's seed words, not the PIN.

The prompt returns only public keys: {"a", "c", "b" (or None to scan B)}.
"""
from __future__ import annotations

import random

from core import seed as seedlib

from .secure_flows import _final
from .widgets import (ConfirmScreen, MenuScreen, MessageScreen, ShowWordsScreen, TextInputScreen,
                      WordEntryScreen, fmt_btc)

DEFAULT_RULES = {"per_tx": 0, "per_day": 1_000_000, "cooldown_s": 0, "max_fee": 200}


def _sats(v):
    return "NO LIMIT" if not v else f"{fmt_btc(v)} BTC"


def cosign(c):
    st = c.signer.cosign_status()
    if st["on"]:
        _manage(c, st)
        return
    c.push(MenuScreen("CO-SIGN", [
        ("SET IT UP", _pick_c, "", "gold"),
        ("HOW IT WORKS", lambda c: c.push(MessageScreen("CO-SIGN", [
            "2 of 3 keys: A (your seed),", "C (kept here, with rules)",
            "and B (a backup, far away).", "Small payments: A + C sign",
            "here. Bigger ones need B.", "Rules change only with C's", "words, never the PIN."]))),
        ("NOT NOW", lambda c: c.finish(None))],
        info=["A second key with spending", "rules (COLDCARD's CCC)."]))


# ------------------------------------------------------------------ setup
def _write_and_check(c, words, title, then):
    """Show new words, then ask for 2 of them back."""
    def quiz(c):
        picks = sorted(random.sample(range(1, len(words) + 1), 2))

        def check(c, entered):
            c.pop()
            if all(words[p - 1] == w for p, w in zip(picks, entered)):
                then(c)
            else:
                c.push(MessageScreen("NOT QUITE", ["A word doesn't match.", "Look at them again."],
                                     border="red", then=lambda c: c.push(
                                         ShowWordsScreen(words, quiz, title=title))))
            return None
        c.push(WordEntryScreen("CHECK " + title, picks, check))
    c.push(ShowWordsScreen(words, quiz, title=title))


def _pick_c(c):
    def new(c):
        words = seedlib.new_mnemonic(12).split()
        c.push(MessageScreen("KEY C", ["12 new words follow. Write", "them down and keep them",
                                       "apart from your seed: they", "are what lets you change",
                                       "the rules, and half of", "every big recovery."],
                             then=lambda c: _write_and_check(c, words, "KEY C",
                                                             lambda c: _rules(c, " ".join(words)))))

    def typed(c):
        def n(count):
            def go(c):
                def done(c, entered):
                    m = " ".join(entered)
                    if not seedlib.is_valid(m):
                        return "Checksum failed: a word is wrong"
                    c.pop()
                    _rules(c, m)
                    return None
                c.push(WordEntryScreen("KEY C", list(range(1, count + 1)), done))
            return go
        c.push(MenuScreen("KEY C WORDS", [(f"{k} WORDS", n(k)) for k in (12, 18, 24)]))

    def vault(c):
        rows = []
        for i, e in enumerate(c.signer.seedvault()):
            def go(c, i=i):
                try:
                    m = c.signer.seedvault_words(index=i)
                except Exception as ex:  # noqa: BLE001 - shown
                    c.push(MessageScreen("CAN'T USE IT", [str(ex)[:40]], border="red"))
                    return
                c.push(MessageScreen("REMEMBER", ["Delete it from the vault once", "this is set up, or the",
                                                  "rules can be changed by", "anyone with your PIN."],
                                     border="orange", then=lambda c: _rules(c, m)))
            rows.append((e["label"], go, e["kind"]))
        if not rows:
            c.push(MessageScreen("EMPTY", ["The seed vault is empty."]))
            return
        c.push(MenuScreen("FROM SEED VAULT", rows))

    def child(c):
        def number(c, text):
            from .secure_flows import _pin_job
            idx = int(text)
            c.pop()
            _pin_job(c, "KEY C", "DERIVING...",
                     lambda pin: c.signer.child_seed(pin=pin, words=12, index=idx),
                     lambda c, m: (c.pop(), _rules(c, m)), note=f"BIP85 child #{idx} as key C")
            return None

        def yes(c):
            c.push(TextInputScreen("BIP85 CHILD", "Child number for key C:", number,
                                   allowed="0123456789", max_len=6, initial="0"))
        c.push(ConfirmScreen("WEAKER: READ", [
            "Key C made from your seed", "means your seed backup alone", "holds 2 of the 3 keys, and",
            "anyone with your PIN can get", "key C and change the rules.", "Only if you accept that."],
            yes, danger=True, yes="I ACCEPT"))

    c.push(MenuScreen("KEY C", [("NEW 12 WORDS", new, "BEST", "gold"),
                                ("TYPE ITS WORDS", typed),
                                ("FROM SEED VAULT", vault),
                                ("BIP85 FROM MY SEED", child, "WEAKER", "red")],
                      info=["Where key C comes from."]))


def _rules(c, secret: str):
    rules = dict(DEFAULT_RULES)
    _rules_menu(c, "KEY C RULES", rules, save=lambda c: _save(c, secret, rules))


def _rules_menu(c, title, rules, save=None, change=None):
    """Edit the rules: locally before setup (save), or one by one (change)."""
    def setter(field, prompt, scale, unit):
        def go(c):
            def got(c, text):
                v = int(round(float(text or "0") * scale))
                if change:
                    try:
                        rules.update(change(field, v))
                    except Exception as e:  # noqa: BLE001 - shown
                        return str(e)[:40]
                else:
                    rules[field] = v
                c.pop()
                return None
            cur = rules[field] / scale
            c.push(TextInputScreen(title, prompt, got, allowed="0123456789.", max_len=12,
                                   initial=(f"{cur:.8f}".rstrip("0").rstrip(".") if scale > 1
                                            else str(int(cur))),
                                   info=lambda s: unit))
        return go

    def items():
        rows = [("PER PAYMENT", setter("per_tx", "Most per payment (BTC, 0 = none):", 10 ** 8, "BTC"),
                 _sats(rules["per_tx"]), "light"),
                ("PER DAY", setter("per_day", "Most per day (BTC, 0 = none):", 10 ** 8, "BTC"),
                 _sats(rules["per_day"]), "light"),
                ("WAIT BETWEEN", setter("cooldown_s", "Minutes between payments:", 60, "MINUTES"),
                 "OFF" if not rules["cooldown_s"] else f"{rules['cooldown_s'] // 60} MIN", "light"),
                ("MAX FEE", setter("max_fee", "Highest fee rate (sat/vB):", 1, "SAT/VB"),
                 f"{rules['max_fee']} SAT/VB", "light")]
        if save:
            rows.append(("SAVE", save, "", "gold"))
        else:
            rows.append(("DONE", lambda c: c.pop()))
        return rows
    c.push(MenuScreen(title, items, info=["Key C signs only inside these.",
                                          "Past them, use key B."]))


def _save(c, secret, rules):
    try:
        st = c.signer.cosign_setup(secret=secret, rules=rules)
    except Exception as e:  # noqa: BLE001 - shown
        c.push(MessageScreen("NOT SAVED", [str(e)[:40]], border="red"))
        return
    _key_b(c, st)


def _key_b(c, st):
    a = c.signer.public_info()["multisig_key"]

    def new(c):
        words = seedlib.new_mnemonic(12).split()

        def done(c):
            from core.multisig import cosigner_key
            b = cosigner_key(seedlib.root_key(" ".join(words), "", c.signer.status()["network"]),
                             c.signer.status()["network"])
            words.clear()                              # B is forgotten here: only you hold it
            # its fingerprint here, on the signer's screen, so CHECK SIGNERS
            # can be matched against it (the app builds the wallet from B's xpub)
            _final(c, MessageScreen("KEY B READY", [
                f"Key B fingerprint: {b[1:9].upper()}", f"Key C fingerprint: {st['fingerprint'].upper()}",
                "", "Next, CHECK SIGNERS must list", "exactly these two and THIS", "DEVICE. If not: don't save."],
                border="gold"), {"a": a, "c": st["key"], "b": b})
        c.push(MessageScreen("KEY B", ["12 words for key B follow.", "This device forgets them",
                                       "right after: keep them far", "from it (a safe, a relative).",
                                       "Big payments and recovery", "need them."],
                             then=lambda c: _write_and_check(c, words, "KEY B", done)))

    c.push(MenuScreen("KEY B", [("MAKE NEW KEY B", new, "WRITE DOWN", "gold"),
                                ("SCAN MY KEY B", lambda c: c.finish({"a": a, "c": st["key"], "b": None}),
                                 "XPUB QR", "grey")],
                      info=[f"Key C {st['fingerprint'].upper()} saved.", "Now the backup key, B."]))


# ----------------------------------------------------------------- manage
def _prove(c, then):
    """Key C's words, then then(c, words)."""
    def n(count):
        def go(c):
            def done(c, entered):
                m = " ".join(entered)
                try:
                    c.signer.cosign_rules(words=m)
                except Exception as e:  # noqa: BLE001 - wrong words (3 lock the signer)
                    if "locked" in str(e):
                        c.fail(e)
                        return None
                    return str(e)[:40]
                c.pop()
                c.pop()
                then(c, m)
                return None
            c.push(WordEntryScreen("KEY C WORDS", list(range(1, count + 1)), done))
        return go
    c.push(MenuScreen("PROVE KEY C", [(f"{k} WORDS", n(k)) for k in (12, 18, 24)],
                      info=["Type key C's words. Three", "wrong tries lock the device."]))


def _manage(c, st):
    def rules(c, words):
        cur = c.signer.cosign_rules(words=words)
        _rules_menu(c, "KEY C RULES", cur,
                    change=lambda f, v: c.signer.cosign_change(words=words, field=f, value=v))

    def off(c, words):
        def yes(c):
            c.signer.cosign_remove(words=words)
            c.finish(None)
        c.push(ConfirmScreen("TURN OFF?", ["Key C is forgotten here.", "The wallet stays; spend it",
                                           "with A + B, or C's words", "on another device."],
                             yes, danger=True, yes="TURN OFF"))

    c.push(MenuScreen("CO-SIGN", [
        ("SEE / CHANGE RULES", lambda c: _prove(c, rules), "KEY C", "gold"),
        ("MAKE THE WALLET", lambda c: _key_b(c, st), "2 OF 3"),
        ("TURN OFF", lambda c: _prove(c, off), "", "red"),
        ("DONE", lambda c: c.finish(None))],
        info=[f"On. Key C {st['fingerprint'].upper()}.", "Rules need key C's words."]))

