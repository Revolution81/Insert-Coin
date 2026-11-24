"""
SETUP › TOOLS › TEMPORARY SEED and the SEED VAULT, on the signer's screens.

A temporary seed is used until LOCK (or BACK TO MAIN SEED) without touching
the stored one: check a backup's balance, sign for an old wallet, sweep a
paper wallet's seed. The PIN stays this device's PIN. The seed vault keeps
extra seeds encrypted with a key from your main seed (and in its card
backups), so switching is two clicks. The app only ever gets public keys.
"""
from __future__ import annotations

from core import seed as seedlib

from .widgets import ConfirmScreen, MenuScreen, MessageScreen, TextInputScreen, WordEntryScreen


def temporary_seed(c):
    st = c.signer.status()
    temp = st.get("temporary")

    def items():
        rows = []
        cur = c.signer.status().get("temporary")
        if cur:
            rows.append(("BACK TO MAIN SEED", back_to_main, "", "gold"))
        rows += [("TYPE SEED WORDS", type_words),
                 ("TYPE AN XPRV", type_xprv),
                 ("SEED XOR PARTS", xor_parts),
                 ("SEED VAULT", vault_list, f"{len(c.signer.seedvault())}", "cyan")]
        if cur and not any(e["active"] for e in c.signer.seedvault()):
            rows.append(("SAVE THIS ONE", save_current, "TO VAULT", "grey"))
        return rows

    info = ([f"In use: {temp['label']} ({temp['fingerprint'].upper()})",
             "LOCK also goes back to main."] if temp else
            ["Use another seed until LOCK.", "Your stored seed isn't changed."])
    c.push(MenuScreen("TEMPORARY SEED", items, info=info))


def _use(c, secret, passphrase, label):
    c.job("OPENING SEED...", lambda: c.signer.use_temporary(secret=secret, passphrase=passphrase,
                                                             label=label),
          lambda c, info: c.finish(info), lambda c, e: c.push(
              MessageScreen("CAN'T USE IT", [str(e)[:40]], border="red")))


def back_to_main(c):
    c.job("MAIN SEED...", lambda: c.signer.main_seed(), lambda c, info: c.finish(info),
          lambda c, e: c.fail(e))


def _name(c, then, initial=""):
    def got(c, text):
        if not text.strip():
            return "Give it a name"
        c.pop()
        then(c, text.strip())
        return None
    c.push(TextInputScreen("NAME", "Name in the vault:", got, max_len=16, initial=initial))


def _confirm(c, secret, passphrase, pops):
    """The seed's fingerprint (check it against your wallet's), then USE IT
    or USE + SAVE IN THE VAULT."""
    try:
        fp = seedlib.fingerprint(seedlib.root_key(secret, passphrase, c.signer.status()["network"]))
    except ValueError as e:
        c.push(MessageScreen("CAN'T USE IT", [str(e)[:40]], border="red"))
        return
    for _ in range(pops):
        c.pop()
    kind = "XPRV" if seedlib.is_xprv(secret) else f"{len(secret.split())} words"

    def save_and_use(c):
        def named(c, label):
            try:
                c.signer.seedvault_add(secret=secret, passphrase=passphrase, label=label)
            except Exception as e:  # noqa: BLE001 - shown; use it anyway?
                c.push(MessageScreen("NOT SAVED", [str(e)[:40]], border="red"))
                return
            _use(c, secret, passphrase, label)
        _name(c, named)

    c.push(MenuScreen("USE THIS SEED?", [("USE IT", lambda c: _use(c, secret, passphrase, "")),
                                         ("USE + SAVE IN VAULT", save_and_use)],
                      info=[f"Fingerprint {fp.upper()}", f"{kind}" + (", passphrase" if passphrase
                                                                       else ""),
                            "Check it matches your wallet."]))


def _passphrase(c, secret, pops):
    def none(c):
        _confirm(c, secret, "", pops + 1)

    def add(c):
        def got(c, pp):
            _confirm(c, secret, pp, pops + 2)
            return None
        c.push(TextInputScreen("PASSPHRASE", "Passphrase (25th word):", got, masked=True))

    c.push(MenuScreen("PASSPHRASE?", [("NO PASSPHRASE", none), ("ADD PASSPHRASE", add)],
                      info=["Not stored unless you save", "it in the seed vault."]))


def type_words(c):
    def pick(n):
        def go(c):
            def done(c, words):
                m = " ".join(words)
                if not seedlib.is_valid(m):
                    return "Checksum failed: a word is wrong"
                _passphrase(c, m, 2)                 # back past the words and the menu
                return None
            c.push(WordEntryScreen("TEMP SEED", list(range(1, n + 1)), done))
        return go
    c.push(MenuScreen("SEED WORDS", [(f"{n} WORDS", pick(n)) for n in (12, 18, 24)]))


def type_xprv(c):
    net = c.signer.status()["network"]

    def ok(c, text):
        try:
            k = seedlib.check_xprv(text, net)
        except ValueError as e:
            return str(e)[:40]
        _confirm(c, k, "", 1)
        return None
    c.push(TextInputScreen("TEMP XPRV", "Master key (xprv...):", ok, allowed=seedlib.XPRV_CHARS,
                           max_len=120, info=lambda s: f"{len(s)}/111 characters"))


def xor_parts(c):
    """All the parts of a Seed XOR backup, combined here and used for now."""
    from core import seedxor

    def setup(n_parts, n_words):
        def go(c):
            got: list[str] = []

            def ask(c):
                def done(c, words):
                    m = " ".join(words)
                    if not seedlib.is_valid(m):
                        return "Checksum failed: a word is wrong"
                    if m in got:
                        return "You've typed this part already"
                    got.append(m)
                    c.pop()
                    if len(got) < n_parts:
                        ask(c)
                        return None
                    try:
                        seed = seedxor.combine(got)
                    except seedxor.SeedXorError as e:
                        got.clear()
                        c.push(MessageScreen("CAN'T COMBINE", [str(e)], border="red"))
                        return None
                    got.clear()
                    _passphrase(c, seed, 2)          # back past the two menus
                    return None
                c.push(WordEntryScreen(f"PART {len(got) + 1} OF {n_parts}",
                                       list(range(1, n_words + 1)), done))
            ask(c)
        return go

    c.push(MenuScreen("SEED XOR", [(f"{n} PARTS", (lambda n: lambda c: c.push(MenuScreen(
        f"{n} PARTS", [(f"{w} WORDS", setup(n, w)) for w in (12, 18, 24)])))(n))
        for n in seedxor.PARTS]))


def save_current(c):
    secret, pp = c.signer.temporary_secret()

    def named(c, label):
        try:
            c.signer.seedvault_add(secret=secret, passphrase=pp, label=label)
        except Exception as e:  # noqa: BLE001 - shown
            c.push(MessageScreen("NOT SAVED", [str(e)[:40]], border="red"))
            return
        c.push(MessageScreen("SAVED", [f"\"{label}\" is in the vault,", "encrypted with your main seed."]))
    _name(c, named, c.signer.status()["temporary"]["label"])


def vault_list(c):
    def items():
        rows = []
        for i, e in enumerate(c.signer.seedvault()):
            right = "IN USE" if e["active"] else e["kind"]
            rows.append((e["label"], (lambda i: lambda c: vault_entry(c, i))(i), right,
                         "gold" if e["active"] else "grey"))
        if not rows:
            rows.append(("(EMPTY)", None, "", "grey"))
        return rows
    c.push(MenuScreen("SEED VAULT", items,
                      info=["Extra seeds, encrypted with", "your main seed. In card backups."]))


def vault_entry(c, index):
    e = c.signer.seedvault()[index]

    def use(c):
        c.job("OPENING SEED...", lambda: c.signer.seedvault_use(index=index),
              lambda c, info: c.finish(info),
              lambda c, err: c.push(MessageScreen("CAN'T USE IT", [str(err)[:40]], border="red")))

    def rename(c):
        def named(c, label):
            c.signer.seedvault_rename(index=index, label=label)
            c.pop()                                   # this entry's menu (old name)
        _name(c, named, e["label"])

    def delete(c):
        def yes(c):
            c.signer.seedvault_delete(index=index)
            c.pop()
        c.push(ConfirmScreen("DELETE?", [f"\"{e['label']}\" ({e['fp'].upper()})", "",
                                         "Only from the vault: keep its", "own backup if it holds coins."],
                             yes, danger=True, yes="DELETE"))

    c.push(MenuScreen(e["label"], [("USE IT", use, "", "gold"), ("RENAME", rename),
                                   ("DELETE", delete, "", "red")],
                      info=[f"Fingerprint {e['fp'].upper()}",
                            e["kind"].replace("W", " words") + (", passphrase" if e["passphrase"]
                                                                else "")]))
