"""
Screens the signer draws itself (see ui/console.py). Every function here
runs in the signer process, on its Console `c`, with `c.signer` the real
Signer. Each one ends with c.finish(result) or c.fail(error); backing out
of the first screen cancels.

Arguments come from the app, which isn't trusted: they're checked here, and
nothing on these screens is text the app chose, except the wallet name you
typed when registering a multisig.
"""
from __future__ import annotations

import random
import time

from core import seed as seedlib
from core import split as splitlib
from core.vault import GAME_MODES, ChipMissing, GamePinUsed, Wiped, WrongPin

from .console import Cancelled

from . import theme as t
from .input import Kind, Mode
from .qrscreens import SeedQRScreen
from .theme import BODY, H, TINY, W
from .widgets import (ConfirmScreen, MenuScreen, MessageScreen, PinScreen, Screen,
                      ShowWordsScreen, TextInputScreen, WordEntryScreen, fmt_btc, fmt_sats,
                      groups, short_addr, wrap)


PIN_HINT = "4-16 letters, numbers or symbols"   # shown where a new PIN is chosen


class BadRequest(Exception):
    pass


# ============================================================ screens
class SecurePinScreen(PinScreen):
    """PinScreen plus your security words (the app can't know them) and a
    line saying what the PIN is for."""

    def __init__(self, title, on_pin, words=("", ""), note="", **kw):
        super().__init__(title, on_pin, **kw)
        self.words = words
        self.note = note

    def render(self, app, frame):
        img = super().render(app, frame)
        d = t.ImageDraw.Draw(img)
        if self.note and not self.error:
            t.text(d, (W // 2, 110), self.note[:40], TINY, "light", align="center")
        if self.words[0]:
            t.text(d, (W // 2, 156), f"SECURITY WORDS: {self.words[0].upper()} "
                   f"{self.words[1].upper()}", TINY, "gold", align="center")
        return img


class ConfirmSendScreen(Screen):
    """Everything shown here comes from the signer's own reading of the PSBT."""

    def __init__(self, built, summary, on_yes=None, question=None, title="CONFIRM SEND"):
        self.built, self.s = built, summary
        self.on_yes = on_yes
        self.question = question or "Sign this?"
        self.title = title
        self.sel = 1      # cursor on NO

    def handle(self, ev, app):
        if ev.kind is Kind.NAV:
            self.sel ^= 1
        elif ev.kind is Kind.TAB or (ev.kind is Kind.HOTKEY and ev.value == "A"):
            sends = [o for o in self.s["outputs"] if o["kind"] != "change"]
            if not sends:                  # a merge: no address leaves the wallet
                return
            lines = []
            for o in sends:
                lines += [" ".join(groups(o["address"])), f"{fmt_sats(o['sats'])} sats", ""]
            app.push(MessageScreen("FULL ADDRESS", lines))
        elif ev.kind is Kind.SELECT:
            if self.sel == 1:
                app.pop()
            elif self.s["cap_ok"] and self.on_yes:
                self.on_yes(app)
        else:
            super().handle(ev, app)

    def render(self, app, frame):
        img, d = t.new_canvas()
        t.sky(img, d, hills=False)
        app.status_bar(img, d, self)
        s = self.s
        sends = [o for o in s["outputs"] if o["kind"] != "change"]
        border = "gold" if s["cap_ok"] and not s["warnings"] else "red"
        t.window(d, (4, 20, W - 5, 114), self.title, border)
        first = sends[0] if sends else {"address": "YOUR WALLET", "sats": 0}
        to = short_addr(first["address"]) + (f" +{len(sends) - 1}" if len(sends) > 1 else "")
        rows = [("TO", to, "light"),
                ("AMOUNT", f"{fmt_btc(s['spend'])} BTC", "gold"),
                ("FEE", f"{s['fee_rate']:g} sat/vB {fmt_sats(s['fee'])}", "light"),
                ("TOTAL", f"{fmt_btc(s['spend'] + s['fee'])} BTC", "gold")]
        if not sends:                    # nothing leaves: coins merged into your own
            kept = sum(o["sats"] for o in s["outputs"])
            rows = [("TO", "YOUR WALLET", "green"),
                    ("MERGE", f"{s.get('inputs', 0)} COINS INTO {len(s['outputs'])}", "light"),
                    ("FEE", f"{s['fee_rate']:g} sat/vB {fmt_sats(s['fee'])}", "light"),
                    ("YOU KEEP", f"{fmt_btc(kept)} BTC", "gold")]
        for i, (k, v, c) in enumerate(rows):
            y = 32 + i * 15
            t.text(d, (14, y), k, BODY, "grey")
            t.text(d, (88, y), v, BODY, c)
        note = ""
        if s.get("blocked"):
            note = s["blocked"]
        elif not s["cap_ok"]:
            left = max(0, s["cap"]["cap"] - s["cap"]["spent"])
            note = f"OVER ONLINE CAP ({fmt_sats(left)} LEFT). USE VAULT MODE."
        elif s["warnings"]:
            note = s["warnings"][0].upper()
        cs = s.get("cosign")
        if note:
            t.text(d, (14, 98), note[:52], TINY, "red")
        elif s.get("payjoin_max"):       # PayJoin: the receiver may add a coin
            t.text(d, (14, 98), f"PAYJOIN: UP TO {s['payjoin_max']:,} SATS MORE FEE"[:52], TINY,
                   "cyan")
        elif cs:                         # CO-SIGN: will key C add its signature?
            t.text(d, (14, 98), ("CO-SIGN: KEY C SIGNS TOO" if cs["will"] else
                                 "CO-SIGN: NO. " + cs["why"])[:52], TINY,
                   "green" if cs["will"] else "orange")
        elif any(o["kind"] != "change" for o in s["outputs"]):
            t.text(d, (14, 98), "TAB: SHOW FULL ADDRESS", TINY, "grey")
        t.dialog(img, d, [self.question], box=(4, 120, 214, H - 4))
        labels = ["YES", "NO"] if s["cap_ok"] else ["--", "NO"]
        t.menu(img, d, (220, 120, W - 5, H - 4), labels, self.sel, frame=frame, row_h=16)
        return img


class AddressScreen(Screen):
    """An address derived by the signer, to compare with what the app shows
    and what the payer's wallet shows."""

    def __init__(self, address: str, label: str):
        self.addr, self.label = address, label
        self.title = "CHECK ADDRESS"

    def handle(self, ev, app):
        if ev.kind in (Kind.SELECT, Kind.BACK, Kind.HOME):
            app.pop()

    def render(self, app, frame):
        img, d = t.new_canvas()
        t.sky(img, d, hills=False)
        app.status_bar(img, d, self)
        size = t.qr(img, self.addr.upper(), (8, 18), module=3, border=3)
        t.window(d, (size + 16, 22, W - 5, H - 8), self.label, "gold")
        x = size + 28
        gs = groups(self.addr)
        per = 3 if len(gs) <= 15 else 4             # Taproot/multisig: 62+ chars
        step = 40 if per == 3 else 36
        for n, g in enumerate(gs):
            line, col = divmod(n, per)
            t.text(d, (x + col * step, 38 + line * 14), g, BODY,
                   "gold" if n % 2 == 0 else "light")
        t.text(d, (x, 112), "Derived by the signer", TINY, "green")
        t.text(d, (x, 124), "from your keys.", TINY, "green")
        t.text(d, (x, 140), "OK DONE", TINY, "light")
        return img


# ============================================================ helpers
def _final(c, screen, value=None):
    """Push the last screen of a prompt: leaving it finishes with value."""
    screen.on_hide = lambda c: c.finish(value)
    c.push(screen)


def _tries(c):
    st = c.signer.status()
    if st.get("has_seed") and st.get("max_tries"):
        return st["tries_left"], st["max_tries"]
    return None


def _pin_job(c, title, label, work, done, note="", prompt="YOUR PIN"):
    """Ask for the PIN; run work(pin) behind a busy screen; done(c, result).
    A wrong PIN stays on the PIN screen with a heart less."""
    scr = SecurePinScreen(title, None, words=c.words, note=note, prompt=prompt, tries=_tries(c))

    def failed(c, err):
        if isinstance(err, WrongPin):
            scr.tries = (err.tries_left, scr.tries[1] if scr.tries else err.tries_left)
            scr.error = f"Wrong PIN. {err.tries_left} tries left"
        elif isinstance(err, (Wiped, GamePinUsed)):
            c.fail(err)                      # the app takes it from here
        elif isinstance(err, ChipMissing):
            scr.error = "Secure chip not connected"
        else:
            scr.error = str(err)[:40]

    def got(c, pin):
        c.job(label, lambda: work(pin), done, failed)
        return None

    scr.on_pin = got
    c.push(scr)
    return scr


def _network(network) -> str:
    from core.config import NETWORKS
    if network not in NETWORKS:
        raise BadRequest("unknown network")
    return network


# ============================================================ unlock
def unlock(c):
    st = c.signer.status()
    if not st["has_seed"]:
        c.fail(BadRequest("no seed on this device"))
        return
    scr = SecurePinScreen("UNLOCK", None, words=c.words, prompt="ENTER PIN", tries=_tries(c))

    def failed(c, err):
        if isinstance(err, WrongPin):
            scr.tries = (err.tries_left, scr.tries[1])
            scr.error = f"Wrong PIN. {err.tries_left} tries left"
        elif isinstance(err, (Wiped, GamePinUsed)):
            c.fail(err)                      # the app takes it from here
        elif isinstance(err, ChipMissing):
            scr.error = "Secure chip not connected"
        else:
            scr.error = str(err)[:40]

    def opened(c, info):
        if isinstance(info, dict) and "countdown" in info:
            c.push(CountdownScreen(c.signer))
            return
        c.finish(info)

    def run(c, pin, pp):
        # checked on the PIN screen itself (c.top is the PIN screen, or the
        # passphrase screen when one is asked): no window pops up for the
        # second the check takes
        c.job("UNLOCKING...", lambda: c.signer.unlock(pin=pin, passphrase=pp),
              opened, failed, inline=scr if c.top is scr else None)

    def got(c, pin):
        if st.get("passphrase_stored", True):
            run(c, pin, None)
        else:
            def with_pp(c, pp):
                c.pop()
                run(c, pin, pp)
            c.push(TextInputScreen("PASSPHRASE", "Passphrase (empty = none):",
                                   lambda c, pp: with_pp(c, pp), masked=True, empty_ok=True))
        return None

    scr.on_pin = got
    c.push(scr)


class CountdownScreen(Screen):
    """LOGIN COUNTDOWN: the PIN was right, the wallet opens when this hits
    zero. Keys can't skip it; HOME gives up (the PIN is asked again)."""
    title = "LOGIN COUNTDOWN"
    animated = True

    def __init__(self, signer):
        self.signer = signer
        self.done = False

    def handle(self, ev, c):
        if ev.kind in (Kind.HOME, Kind.BACK):
            self.signer.cancel_login()
            c.fail(Cancelled("countdown cancelled"))

    def render(self, c, frame):
        left = self.signer.login_wait()
        if left == 0 and not self.done:
            self.done = True
            c.job("UNLOCKING...", lambda: self.signer.finish_login(),
                  lambda c, info: c.finish(info), lambda c, e: c.fail(e))
        img, d = t.new_canvas()
        t.sky(img, d)
        t.window(d, (6, 22, W - 7, H - 6), self.title, "gold")
        m, sec = divmod(max(0, left), 60)
        h, m = divmod(m, 60)
        clock = f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"
        big = getattr(t, "BIG", None) or getattr(t, "HEAD", BODY)
        t.text(d, (W // 2, 48), clock, big, "gold", align="center")
        for i, line in enumerate(["PIN accepted. The wallet opens", "when the countdown ends.",
                                  "Keep the device on.", "", "HOME: give up"]):
            t.text(d, (16, 88 + i * 12), line, BODY, "light")
        return img


def wipe_pin(c, action):
    """WIPE PIN: entered at unlock, it erases the wallet and the device
    starts like new. Set, change or remove here, with your PIN."""
    if action == "clear":
        _pin_job(c, "WIPE PIN", "SAVING...", lambda pin: c.signer.clear_wipe_pin(pin=pin),
                 lambda c, _: _final(c, MessageScreen("WIPE PIN OFF", ["No wipe PIN now."]),
                                     False), note="Turn the wipe PIN off")
        return
    if action != "set":
        raise BadRequest("unknown action")

    def got_pin(c, pin):
        def first(c, w1):
            if w1 == pin:
                return "Must differ from your PIN"

            def again(c, w2):
                if w2 != w1:
                    return "PINs don't match"
                c.pop()
                c.pop()
                c.job("SAVING...", lambda: c.signer.set_wipe_pin(pin=pin, wipe_pin=w1),
                      lambda c, _: _final(c, MessageScreen("WIPE PIN ON", [
                          "Entering it at unlock erases",
                          "everything here: the seed,",
                          "settings, paper wallets. The",
                          "device starts like new. Only",
                          "your seed words bring it back."], border="red"), True),
                      lambda c, e: c.fail(e))
                return None
            c.push(SecurePinScreen("WIPE PIN", again, words=c.words, prompt="REPEAT IT"))
            return None
        c.push(SecurePinScreen("WIPE PIN", first, words=c.words, prompt="NEW WIPE PIN",
                               note=PIN_HINT))

    _pin_job(c, "WIPE PIN", "CHECKING PIN...", lambda pin: c.signer.check_pin(pin=pin) and pin,
             got_pin, note="Your PIN first")


def game_pin(c, action, mode="game"):
    """GAME PIN: entered at unlock, it opens the arcade game instead of the
    wallet (mode "game"), or erases everything first and then shows the game
    ("game+wipe"). For a border crossing: the device looks like a toy."""
    if action == "clear":
        _pin_job(c, "GAME PIN", "SAVING...", lambda pin: c.signer.clear_game_pin(pin=pin),
                 lambda c, _: _final(c, MessageScreen("GAME PIN OFF", ["No game PIN now."]),
                                     False), note="Turn the game PIN off")
        return
    if action != "set" or mode not in GAME_MODES:
        raise BadRequest("unknown action")

    def got_pin(c, pin):
        def first(c, g1):
            if g1 == pin:
                return "Must differ from your PIN"

            def again(c, g2):
                if g2 != g1:
                    return "PINs don't match"
                c.pop()
                c.pop()
                lines = (["Entering it at unlock opens", "the game. The wallet stays",
                          "locked and untouched.", "",
                          "In the game, hold HOME to", "get back to the lock screen."]
                         if mode == "game" else
                         ["Entering it at unlock ERASES", "everything here, then opens",
                          "the game. Afterwards the", "device starts like new. Only",
                          "your seed words bring it back."])
                c.job("SAVING...", lambda: c.signer.set_game_pin(pin=pin, game_pin=g1, mode=mode),
                      lambda c, _: _final(c, MessageScreen(
                          "GAME PIN ON", lines, border="gold" if mode == "game" else "red"), True),
                      lambda c, e: c.fail(e))
                return None
            c.push(SecurePinScreen("GAME PIN", again, words=c.words, prompt="REPEAT IT"))
            return None
        c.push(SecurePinScreen("GAME PIN", first, words=c.words, prompt="NEW GAME PIN",
                               note=PIN_HINT))

    what = "game only" if mode == "game" else "game + wipe"
    _pin_job(c, "GAME PIN", "CHECKING PIN...", lambda pin: c.signer.check_pin(pin=pin) and pin,
             got_pin, note=f"Your PIN first ({what})")


def countdown(c, seconds):
    if isinstance(seconds, bool) or not isinstance(seconds, int) or \
            not (seconds == 0 or 60 <= seconds <= 24 * 3600):
        raise BadRequest("countdown out of range")
    what = "Login countdown: off" if not seconds else \
        f"Wait {seconds // 60} min after each PIN"
    _pin_job(c, "LOGIN COUNTDOWN", "SAVING...",
             lambda pin: c.signer.set_countdown(pin=pin, seconds=seconds),
             lambda c, v: c.finish(v), note=what)


def _policy_pins(c, title, note, work, done):
    """Your PIN, and first the bypass PIN if the policy is locked."""
    locked = c.signer.policy_locked()
    if not locked:
        _pin_job(c, title, "SAVING...", lambda pin: work(pin, None), done, note=note)
        return

    def got_bypass(c, bp):
        c.pop()
        _pin_job(c, title, "SAVING...", lambda pin: work(pin, bp), done, note=note)
        return None
    c.push(SecurePinScreen(title, got_bypass, words=c.words, note=note, prompt="BYPASS PIN"))


def policy(c, field, value):
    """One spending policy change, shown here before it's made."""
    from core import policy as pol
    if field not in pol.FIELDS:
        raise BadRequest("unknown policy setting")
    note = pol.describe_change(field, value)[:30]
    _policy_pins(c, "SPENDING POLICY", note,
                 lambda pin, bp: c.signer.set_policy(pin=pin, field=field, value=value,
                                                     bypass_pin=bp),
                 lambda c, v: c.finish(v))


def bypass(c, action):
    """BYPASS PIN: locks the policy. Set (twice), or remove (bypass + PIN)."""
    if action == "clear":
        def got_bp(c, bp):
            c.pop()
            _pin_job(c, "BYPASS PIN", "SAVING...",
                     lambda pin: c.signer.clear_bypass(pin=pin, bypass_pin=bp),
                     lambda c, v: _final(c, MessageScreen("POLICY UNLOCKED", [
                         "No bypass PIN now: your PIN",
                         "alone changes the policy."]), v), note="Remove the bypass PIN")
            return None
        c.push(SecurePinScreen("BYPASS PIN", got_bp, words=c.words, prompt="BYPASS PIN"))
        return
    if action != "set":
        raise BadRequest("unknown action")
    locked = c.signer.policy_locked()

    def ask_new(c, old_bp):
        def first(c, b1):
            def again(c, b2):
                if b2 != b1:
                    return "PINs don't match"
                c.pop()
                c.pop()
                _pin_job(c, "BYPASS PIN", "SAVING...",
                         lambda pin: c.signer.set_bypass(pin=pin, new_bypass=b1,
                                                         bypass_pin=old_bp),
                         lambda c, v: _final(c, MessageScreen("POLICY LOCKED", [
                             "Changing the policy now needs",
                             "the bypass PIN, then yours.",
                             "Seed words can't be shown or",
                             "exported until you remove it.",
                             "Keep it apart from your PIN."], border="gold"), v),
                         note="Your PIN to finish")
                return None
            c.push(SecurePinScreen("BYPASS PIN", again, words=c.words, prompt="REPEAT IT"))
            return None
        c.push(SecurePinScreen("BYPASS PIN", first, words=c.words, prompt="NEW BYPASS PIN",
                               note=PIN_HINT))

    if locked:
        def got_old(c, bp):
            c.pop()
            ask_new(c, bp)
            return None
        c.push(SecurePinScreen("BYPASS PIN", got_old, words=c.words, prompt="CURRENT BYPASS"))
    else:
        ask_new(c, None)


def totp(c, action):
    """PHONE 2FA: show a QR for an authenticator app, then check a code."""
    from core import policy as pol
    if action == "off":
        _policy_pins(c, "PHONE 2FA", "Turn phone 2FA off",
                     lambda pin, bp: c.signer.set_totp(pin=pin, secret=None, bypass_pin=bp),
                     lambda c, v: c.finish(v))
        return
    if action != "on":
        raise BadRequest("unknown action")
    if not pol.clock_ok():
        c.push(MessageScreen("CLOCK NOT SET", [
            "2FA codes depend on the time,",
            "and this device's clock isn't",
            "set yet. Go Online once (MODE)",
            "so it can set the time."], border="red", then=lambda c: c.cancel()))
        return
    secret = pol.new_secret()

    def code_entered(c, code):
        c.pop()
        _policy_pins(c, "PHONE 2FA", "Turn phone 2FA on",
                     lambda pin, bp: c.signer.set_totp(pin=pin, secret=secret, code=code,
                                                       bypass_pin=bp),
                     lambda c, v: _final(c, MessageScreen("PHONE 2FA ON", [
                         "Payments now need a code from",
                         "your phone. Lose the phone?",
                         "Remove 2FA with your PIN" + (" and" if c.signer.policy_locked()
                                                        else "."),
                         "bypass PIN" if c.signer.policy_locked() else ""]), v))
        return None

    def scanned(c):
        c.push(TextInputScreen("PHONE 2FA", "6-digit code from the app:", code_entered,
                               allowed="0123456789", max_len=6))
    from .qrscreens import AnimatedQRScreen
    c.push(AnimatedQRScreen("SCAN WITH PHONE", [pol.otpauth_uri(secret)],
                            ["Scan with an authenticator", "app (Aegis, Google, 2FAS...)",
                             "then press OK."], then=scanned))


# ============================================================ new seeds
def create(c, network, camera=""):
    """NEW WALLET: words made and shown here, checked, then a PIN.

    The device's sources go in automatically (Linux RNG, camera noise, the
    secure chip if connected). You can stack your own on top: dice, key
    mashing, coin flips, a shuffled deck. Or choose DICE ONLY to make a seed
    you can check with another tool.

    camera: hex of a camera-noise hash the app gathered through czscan. It
    only ever adds randomness (see seed.new_mnemonic), so the app being
    wrong or hostile about it can't weaken the seed."""
    network = _network(network)
    try:
        cam = bytes.fromhex(camera) if isinstance(camera, str) and len(camera) <= 64 else b""
    except ValueError:
        cam = b""
    chip = c.signer.chip_entropy()
    auto = ["LINUX RNG"] + (["CAMERA"] if cam else []) + (["CHIP"] if chip else [])
    added: dict[str, tuple[bytes, float]] = {}       # name -> (data, bits)

    def info():
        lines = ["Mixed in: " + ", ".join(auto)]
        if added:
            total = sum(b for _, b in added.values())
            n = len(added)
            lines.append(f"You added {n} source{'s' if n > 1 else ''}: {total:.0f} bits")
        else:
            lines.append("Optional: add your own below.")
        return lines

    def refresh(c):
        menu.info = info()
        for k, item in enumerate(menu.items):
            key = item[0].replace("ADD ", "")
            if key in added:
                menu.items[k] = (item[0], item[1], f"{added[key][1]:.0f} BITS", "gold")

    def add(name, data, bits):
        def done(c):
            added[name] = (data, bits)
            refresh(c)
        return done

    def start(n):
        def go(c):
            user = {k.lower(): d for k, (d, _) in added.items() if k != "DICE ROLLS"}
            dice = added.get("DICE ROLLS", (b"", 0))[0]
            words = seedlib.new_mnemonic(n, dice, camera=cam, chip=chip, user=user).split()
            c.push(ShowWordsScreen(words, lambda c: _quiz(c, words, network)))
        return go

    def dice(c):
        def ok(c, r):
            c.pop()
            add("DICE ROLLS", seedlib.dice_entropy(r), seedlib.dice_bits(r))(c)
            return None
        c.push(TextInputScreen("DICE ROLLS", "Roll a die, type each result:", ok,
                               allowed="123456", max_len=200,
                               info=lambda r: f"{len(r)} rolls = {seedlib.dice_bits(r):.0f} bits"))

    def keys(c):
        def ok(c, data, bits):
            c.pop()
            add("KEY MASHING", data, bits)(c)
        c.push(KeyMashScreen(ok))

    def coins(c):
        def ok(c, f):
            c.pop()
            add("COIN FLIPS", seedlib.coin_entropy(f), len(f))(c)
            return None
        c.push(TextInputScreen("COIN FLIPS", "Flip a coin, type H or T:", ok,
                               allowed="HTht", max_len=256,
                               info=lambda f: f"{len(f)} flips = {len(f)} bits"))

    def cards(c):
        def ok(c, text):
            try:
                deck = seedlib.parse_cards(text)
            except ValueError as e:
                return str(e)[:40]
            c.pop()
            add("CARD SHUFFLE", " ".join(deck).encode(), seedlib.card_bits(len(deck)))(c)
            return None

        def count(text):
            try:
                n = len(seedlib.parse_cards(text))
            except ValueError:
                return "Cards like AS 7H TD KC"
            return f"{n} cards = {seedlib.card_bits(n):.0f} bits"
        c.push(TextInputScreen("CARD SHUFFLE", "Shuffle well, type cards in order:", ok,
                               allowed="A23456789TJQKSHDCatjqkshdc10 ", max_len=160,
                               info=count))

    menu = MenuScreen("NEW WALLET", [
        ("12 WORDS", start(12)),
        ("18 WORDS", start(18)),
        ("24 WORDS", start(24)),
        ("ADD DICE ROLLS", dice),
        ("ADD KEY MASHING", keys),
        ("ADD COIN FLIPS", coins),
        ("ADD CARD SHUFFLE", cards),
        ("DICE ONLY", lambda c: _dice_only(c, network), "ADVANCED", "grey"),
    ], info=info())
    c.push(menu)


class KeyMashScreen(Screen):
    """Type at random. What counts is the exact timing between key presses
    (microseconds), more than which keys: people type in patterns. Credited
    conservatively at 2 bits a key, none for a held key auto-repeating."""
    mode = Mode.TEXT
    title = "KEY MASHING"
    BITS_PER_KEY = 2
    TARGET = 128

    def __init__(self, on_done):
        self.on_done = on_done
        self.data = bytearray()
        self.bits = 0.0
        self.count = 0
        self._last = None          # (value, time)
        self._gap = 0

    def handle(self, ev, app):
        if ev.kind is Kind.SUBMIT:
            if self.count:
                self.on_done(app, bytes(self.data), min(self.bits, 256))
            else:
                app.pop()
            return
        if ev.kind in (Kind.CANCEL, Kind.HOME):
            app.pop()
            return
        now = time.perf_counter_ns()
        value = f"{ev.kind.value}:{ev.value}"
        self.data += value.encode() + b"\0" + now.to_bytes(8, "big")
        self.count += 1
        if self._last is not None:
            gap = now - self._last[1]
            repeat = value == self._last[0] and gap < 60_000_000 and abs(gap - self._gap) < 5_000_000
            if not repeat:
                self.bits += self.BITS_PER_KEY
            self._gap = gap
        else:
            self.bits += self.BITS_PER_KEY
        self._last = (value, now)

    def render(self, app, frame):
        img, d = t.new_canvas()
        t.sky(img, d)
        app.status_bar(img, d, self)
        t.window(d, (10, 22, W - 11, H - 10), "KEY MASHING")
        t.text(d, (W // 2, 38), "Type anything, as randomly", BODY, "light", align="center")
        t.text(d, (W // 2, 52), "as you can. Vary the rhythm.", BODY, "light", align="center")
        frac = min(1.0, self.bits / self.TARGET)
        t.bar(d, (30, 74, W - 31, 86), frac, "gold" if frac >= 1 else "cyan")
        t.text(d, (W // 2, 96), f"{self.count} KEYS  {min(self.bits, 256):.0f} BITS",
               BODY, "gold", align="center")
        t.text(d, (W // 2, 116), "*" * min(self.count % 33, 32), TINY, "grey", align="center")
        t.text(d, (W // 2, 138), "OK DONE   HOME CANCEL", TINY, "light", align="center")
        return img


def _dice_only(c, network):
    """A seed from dice alone, checkable with another tool."""
    def pick(n):
        def go(c):
            need = seedlib.DICE_ONLY_MIN[n]

            def ok(c, rolls):
                try:
                    m = seedlib.dice_only_mnemonic(rolls, n)
                except ValueError as e:
                    return str(e)[:40]
                words = m.split()
                c.pop()
                c.push(ShowWordsScreen(words, lambda c: _quiz(c, words, network)))
                return None
            c.push(TextInputScreen("DICE ONLY", f"Type {need}+ real dice rolls:", ok,
                                   allowed="123456", max_len=300,
                                   info=lambda r: f"{len(r)}/{need} rolls = "
                                                  f"{seedlib.dice_bits(r):.0f} bits"))
        return go

    c.push(MessageScreen("DICE ONLY", [
        "Your seed comes from your rolls",
        "and nothing else, so you can",
        "check it with another tool:",
        "SHA-256 of the rolls as typed.",
        "Use real dice, roll honestly,",
        "never pick numbers yourself."],
        then=lambda c: c.push(MenuScreen("DICE ONLY", [
            ("12 WORDS", pick(12), "50+ ROLLS", "grey"),
            ("18 WORDS", pick(18), "75+ ROLLS", "grey"),
            ("24 WORDS", pick(24), "99+ ROLLS", "grey")]))))


def _quiz(c, words, network):
    picks = sorted(random.sample(range(1, len(words) + 1), 3))

    def check(c, entered):
        if all(words[p - 1] == w for p, w in zip(picks, entered)):
            c.pop()
            _passphrase(c, " ".join(words), network)
            return None
        c.pop()
        c.push(MessageScreen("NOT QUITE", ["One of those words doesn't match.",
                                          "Let's look at your backup again."],
                             then=lambda c: c.push(ShowWordsScreen(
                                 words, lambda c: _quiz(c, words, network))), border="red"))
        return None

    c.push(WordEntryScreen("CHECK BACKUP", picks, check))


def restore(c, network, words=12):
    """Type the seed words here."""
    network = _network(network)
    if words not in (12, 18, 24):
        raise BadRequest("12, 18 or 24 words")

    def done(c, entered):
        m = " ".join(entered)
        if not seedlib.is_valid(m):
            return "Checksum failed: a word is wrong"
        c.pop()
        _passphrase(c, m, network)
        return None
    c.push(WordEntryScreen("RESTORE", list(range(1, words + 1)), done))


def import_seed(c, network, mnemonic):
    """A seed the app scanned (SeedQR): it passed through the app, so say so."""
    network = _network(network)
    if seedlib.is_xprv(str(mnemonic)):
        try:
            k = seedlib.check_xprv(str(mnemonic), network)
        except ValueError as e:
            c.fail(BadRequest(str(e)))
            return
        c.push(MessageScreen("XPRV READ", ["Master key, checksum OK.", f"Starts {k[:12]}...", "",
                                          "Scanned keys pass through", "the app process. Typing",
                                          "it here is more private."],
                             then=lambda c: _set_pin(c, k, network, "", True)))
        return
    m = seedlib.normalize(str(mnemonic))
    if not seedlib.is_valid(m):
        c.fail(BadRequest("not a valid seed"))
        return
    n = len(m.split())
    c.push(MessageScreen("SEEDQR READ", [f"{n} words, checksum OK.",
                                         f"Starts with {m.split()[0].upper()}...",
                                         "", "Scanned seeds pass through", "the app process. Typing",
                                         "them here is more private."],
                         then=lambda c: _passphrase(c, m, network)))


class XprvScreen(Screen):
    """An imported master key: the text in groups of 4, LEFT/RIGHT for its
    QR. Shown only here, on the signer's screen, after the PIN."""

    def __init__(self, key: str):
        self.key, self.page = key, 0

    def handle(self, ev, app):
        if ev.kind is Kind.NAV and ev.value in ("LEFT", "RIGHT"):
            self.page ^= 1
        elif ev.kind in (Kind.SELECT, Kind.BACK, Kind.HOME):
            self.key = ""
            app.pop()

    def render(self, app, frame):
        img, d = t.new_canvas()
        t.sky(img, d, hills=False)
        app.status_bar(img, d, self)
        if self.page:
            size = t.qr(img, self.key, (8, 18), module=3, border=2)
            t.window(d, (size + 16, 22, W - 5, H - 8), "XPRV QR")
            for i, line in enumerate(["Your master key.", "Anyone who scans", "it can spend.",
                                      "", "LEFT/RIGHT: TEXT"]):
                t.text(d, (size + 26, 40 + i * 14), line, TINY, "light")
            return img
        t.window(d, (5, 22, W - 5, H - 8), "XPRV (THE BACKUP)")
        gs = [self.key[i:i + 4] for i in range(0, len(self.key), 4)]
        for n, g in enumerate(gs):
            row, col = divmod(n, 7)
            t.text(d, (16 + col * 42, 38 + row * 13), g, BODY, "gold" if n % 2 == 0 else "light")
        t.text(d, (16, H - 22), "LEFT/RIGHT: QR   OK: DONE", TINY, "grey")
        return img


def restore_xprv(c, network):
    """IMPORT XPRV, typed here: a master extended private key (xprv, or tprv
    on test networks) instead of seed words. No passphrase: the key is
    everything. Back it up as carefully as words."""
    network = _network(network)

    def ok(c, text):
        try:
            k = seedlib.check_xprv(text, network)
        except ValueError as e:
            return str(e)[:40]
        c.pop()
        c.push(MessageScreen("NO SEED WORDS", [
            "This wallet will have no seed", "words: the key IS the backup.",
            "Keep it as safely as words.", "SHOW SEED WORDS shows it, and",
            "card backups still work."], then=lambda c: _set_pin(c, k, network, "", True)))
        return None
    c.push(TextInputScreen("IMPORT XPRV", "Master key (xprv...):", ok, allowed=seedlib.XPRV_CHARS,
                           max_len=120, info=lambda s: f"{len(s)}/111 characters"))


def seed_xor(c, network):
    """Rebuild a seed from its Seed XOR parts (COLDCARD's format), typed
    here: every part is needed, in any order."""
    from core import seedxor
    network = _network(network)

    def setup(n_parts, n_words):
        def go(c):
            got: list[str] = []

            def ask(c):
                i = len(got) + 1

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
                    c.pop()                     # the WORDS menu
                    c.pop()                     # the PARTS menu
                    c.push(MessageScreen("SEED XOR", [f"{n_parts} parts combined.",
                                                      f"{n_words} words, checksum OK.", "",
                                                      "Next: passphrase and PIN."],
                                         then=lambda c: _passphrase(c, seed, network)))
                    return None
                c.push(WordEntryScreen(f"PART {i} OF {n_parts}", list(range(1, n_words + 1)), done))
            ask(c)
        return go

    def parts(n_parts):
        return lambda c: c.push(MenuScreen(f"{n_parts} PARTS", [
            (f"{w} WORDS", setup(n_parts, w)) for w in (12, 18, 24)],
            info=["How many words in each part?"]))

    c.push(MenuScreen("SEED XOR", [(f"{n} PARTS", parts(n)) for n in seedxor.PARTS],
                      info=["COLDCARD's split backup: all", "parts needed, any order."]))


def shares(c, network):
    """Rebuild a seed from SLIP39 shares typed here."""
    network = _network(network)
    collected: list[list[str]] = []
    need = {"k": None}

    def ask(n_words):
        def go(c):
            def done(c, words):
                try:
                    info = splitlib.check_share(words)
                except splitlib.SplitError as e:
                    return str(e)
                for prev in collected:
                    other = splitlib.check_share(prev)
                    if other.identifier != info.identifier:
                        return "Shares are from different backups"
                    if other.index == info.index:
                        return "That share was already entered"
                collected.append(words)
                need["k"] = info.threshold
                c.pop()
                if len(collected) < info.threshold:
                    c.push(MessageScreen(f"SHARE {len(collected)} OF {info.threshold}",
                                         ["Good. Now the next share."], then=ask(n_words)))
                    return None
                try:
                    m = splitlib.combine(collected)
                except splitlib.SplitError as e:
                    collected.clear()
                    c.push(MessageScreen("CAN'T REBUILD", [str(e)], border="red"))
                    return None
                collected.clear()
                _passphrase(c, m, network)
                return None
            label = f"SHARE {len(collected) + 1}" + (f"/{need['k']}" if need["k"] else "")
            c.push(WordEntryScreen(label, list(range(1, n_words + 1)), done,
                                   complete=splitlib.completions))
        return go

    c.push(MenuScreen("SPLIT SHARES", [
        ("20-WORD SHARES", ask(20)), ("27-WORD SHARES", ask(27)), ("33-WORD SHARES", ask(33))],
        info=["Type 4 letters of each", "word, then SPACE."]))


def _passphrase(c, mnemonic, network):
    def none(c):
        _set_pin(c, mnemonic, network, "", True)   # "no passphrase" is stored: unlock won't ask

    def add(c):
        def first(c, pp):
            def second(c, pp2):
                if pp2 != pp:
                    return "Doesn't match"
                c.pop()
                c.pop()
                c.push(MenuScreen("KEEP PASSPHRASE?", [
                    ("ASK EVERY UNLOCK", lambda c: _set_pin(c, mnemonic, network, pp, False)),
                    ("STORE ENCRYPTED", lambda c: _set_pin(c, mnemonic, network, pp, True)),
                ], info=["Asking each time is safer:", "a stolen card can't open it."]))
                return None
            c.push(TextInputScreen("PASSPHRASE", "Type it again:", second, masked=True))
            return None
        c.push(TextInputScreen("PASSPHRASE", "Optional 25th word:", first, masked=True))

    c.push(MenuScreen("PASSPHRASE?", [("NO PASSPHRASE", none), ("ADD PASSPHRASE", add)],
                      info=["A passphrase makes a whole", "different wallet. Lose it = lose coins."]))


def _set_pin(c, mnemonic, network, passphrase, store):
    def first(c, pin):
        def second(c, pin2):
            if pin2 != pin:
                return "PINs don't match"
            c.job("SECURING SEED...",
                  lambda: c.signer.setup(pin=pin, mnemonic=mnemonic, network=network,
                                         passphrase=passphrase, store_passphrase=store),
                  lambda c, info: c.finish(info), lambda c, e: c.fail(e))
            return None
        c.push(SecurePinScreen("SET PIN", second, words=c.words, prompt="REPEAT PIN",
                               note="Real PIN screens show these words"))
        return None

    c.push(SecurePinScreen("SET PIN", first, words=c.words, prompt="NEW PIN",
                           note=PIN_HINT))


# ============================================================ signing
def sign(c, psbt, payjoin=False):
    """Read the PSBT, show what it does, sign only on YES. payjoin: the
    payment request offered PayJoin (BIP-78); the signer then keeps the
    original to check the receiver's proposal against."""
    if not isinstance(psbt, str) or len(psbt) > 2_000_000:
        raise BadRequest("not a PSBT")
    if not isinstance(payjoin, bool):
        raise BadRequest("bad payjoin flag")

    def described(c, s):
        sends = [o for o in s["outputs"] if o["kind"] == "send"]
        if payjoin and len(sends) == 1 and isinstance(s.get("wallet_id"), (str, type(None))) \
                and s.get("sigs_needed", 1) == 1 and not s.get("silent"):
            from core.payjoin import EXTRA_VB
            s["payjoin_max"] = int(s["fee_rate"] * EXTRA_VB)
        if s.get("replaces_signed"):
            title = "REPLACE TX"
        elif not sends:
            title = "MOVE TO SELF"
        elif s.get("wallet_id") is None and s["wallet"] == "Single-sig":
            title = "CONFIRM SEND"
        else:
            title = s["wallet"].upper()[:16]
        q = "Sign this?"
        if s.get("sigs_needed", 1) > 1:
            mine = 2 if (s.get("cosign") or {}).get("will") else 1
            q = f"Sign? ({min(s['sigs_present'] + mine, s['sigs_needed'])} of {s['sigs_needed']})"

        def go(c, code=None):
            c.job("SIGNING...", lambda: c.signer.sign(psbt=s.get("psbt") or psbt, digest=s["digest"],
                                                      code=code, payjoin=payjoin),
                  lambda c, out: c.finish({"psbt": out, "summary": s}),
                  lambda c, e: c.fail(e))

        def yes(c):
            if not s.get("needs_code"):
                go(c)
                return

            def entered(c, code):
                c.pop()
                go(c, code)
                return None
            c.push(TextInputScreen("PHONE 2FA", "Code from your phone:", entered,
                                   allowed="0123456789", max_len=6))
        c.push(ConfirmSendScreen(None, s, on_yes=yes, title=title, question=q))

    c.job("CHECKING TX...", lambda: c.signer.describe(psbt=psbt), described,
          lambda c, e: c.fail(e))


# ============================================================ secrets
def show_words(c):
    def show(c, m):
        if seedlib.is_xprv(m):
            _final(c, XprvScreen(m))
            return
        _final(c, ShowWordsScreen(m.split(), None, title="BACKUP"))
    _pin_job(c, "BACKUP", "CHECKING PIN...", lambda pin: c.signer.reveal_words(pin=pin), show,
             note="To show your seed words")


def seedqr(c):
    def show(c, m):
        if seedlib.is_xprv(m):
            _final(c, MessageScreen("NO SEEDQR", ["This wallet came from an", "XPRV: it has no seed words.",
                                                  "SHOW SEED WORDS shows the", "key itself."]))
            return
        n = len(m.split())
        if n not in (12, 24):                  # SeedQR is defined for 12 and 24 only
            _final(c, MessageScreen("NO SEEDQR", [
                f"SeedQR is made for 12 or 24", f"words; this seed has {n}.",
                "Keep your words (or a split", "backup) instead."]))
            return
        _final(c, SeedQRScreen(seedlib.seedqr_encode(m), n))
    _pin_job(c, "SEEDQR", "CHECKING PIN...", lambda pin: c.signer.reveal_words(pin=pin), show,
             note="To show your seed as a QR")


def account_rule(c, account, rule, name=""):
    """Turn an account's vault-only rule on or off. Either way needs the PIN
    here; turning it off is what a thief would want, so it's never silent."""
    from core import accounts as accts
    try:
        acct = accts.make_id(*accts.parse(account))
    except accts.AccountError as e:
        raise BadRequest(str(e))
    if rule not in ("vault", "normal"):
        raise BadRequest("unknown rule")
    who = accts.label(acct) + (f' "{str(name)[:12]}"' if name else "")
    what = "TURN VAULT-ONLY ON" if rule == "vault" else "TURN VAULT-ONLY OFF"
    _pin_job(c, "ACCOUNT RULE", "SAVING...",
             lambda pin: c.signer.set_account_rule(pin=pin, account=acct, rule=rule),
             lambda c, rules: c.finish(rules), note=who[:30], prompt=what)


def restore_backup(c, blob):
    """Show what a card backup would bring back; merge it on YES. The file
    only opens with this seed's key, which also proves nobody changed it."""
    from core import backup
    if not isinstance(blob, str) or len(blob) > backup.MAX_BYTES * 2:
        raise BadRequest("not a backup")
    payload = c.signer.open_backup(blob_b64=blob)

    def yes(c):
        c.job("RESTORING...", lambda: c.signer.apply_backup(payload),
              lambda c, r: c.finish({"app": payload.get("app", {}), "result": r}),
              lambda c, e: c.fail(e))
    c.push(ConfirmScreen("RESTORE BACKUP?", backup.summary(payload) +
                         ["Adds to what's here;", "removes nothing."], yes, yes="RESTORE"))


def notes(c):
    """NOTES & PASSWORDS (ui/notes_flow.py): all on this screen."""
    from .notes_flow import notes as run
    run(c)


def temporary_seed(c):
    """TEMPORARY SEED and the SEED VAULT (ui/tempseed_flow.py)."""
    if c.signer.status()["policy"]["locked"]:
        _final(c, MessageScreen("POLICY LOCKED", [
            "Temporary seeds are off while", "the spending policy is locked:",
            "your main seed could be loaded", "as one to walk around it."], border="red"))
        return
    from .tempseed_flow import temporary_seed as run
    run(c)


def _b64(v, most=200_000) -> bytes:
    import base64
    if not isinstance(v, str) or len(v) > most:
        raise BadRequest("bad data")
    try:
        return base64.b64decode(v, validate=True)
    except ValueError:
        raise BadRequest("bad data")


def cosign(c):
    """SETUP › SECURITY › CO-SIGN (ui/cosign_flow.py)."""
    if c.signer.status().get("temporary"):
        _final(c, MessageScreen("TEMPORARY SEED", ["Co-sign belongs to your main", "seed: go back to it first."],
                                border="red"))
        return
    from .cosign_flow import cosign as run
    run(c)


def teleport_receive(c):
    """KEY TELEPORT › RECEIVE: the receive QR and its 8 digits."""
    if c.signer.status()["policy"]["locked"]:
        _final(c, MessageScreen("POLICY LOCKED", [
            "Only multisig PSBTs come in", "by teleport while the", "spending policy is locked."],
            border="red"))
        return
    from .teleport_flow import receive_start
    receive_start(c)


def teleport_send(c, rx):
    """KEY TELEPORT › SEND, after the app scanned someone's receive QR."""
    rx = _b64(rx, 200)
    if len(rx) != 33:
        raise BadRequest("not a receive QR")
    if c.signer.status()["policy"]["locked"]:
        _final(c, MessageScreen("POLICY LOCKED", [
            "Nothing leaves by teleport", "while the spending policy", "is locked."], border="red"))
        return
    from .teleport_flow import send
    send(c, rx)


def teleport_incoming(c, kind, payload):
    """A scanned S or E package."""
    if kind not in ("S", "E"):
        raise BadRequest("unknown package")
    data = _b64(payload)
    if kind == "S" and c.signer.status()["policy"]["locked"]:
        _final(c, MessageScreen("POLICY LOCKED", [
            "Only multisig PSBTs come in", "by teleport while the", "spending policy is locked."],
            border="red"))
        return
    from .teleport_flow import receive
    try:
        receive(c, kind, data)
    except ValueError:
        raise BadRequest("damaged package")


def teleport_psbt(c, psbt):
    """A multisig PSBT to one co-signer (KEY TELEPORT, no setup needed)."""
    _b64(psbt)
    from .teleport_flow import send_psbt
    send_psbt(c, psbt)


def verify_backup(c, files):
    """VERIFY BACKUP: open each copy of this seed's card backups with the
    seed's key and say which are whole, changing nothing. AES-GCM fails on
    any changed byte, so OK means the file is exactly as it was saved.
    files: up to 12 [label, base64] pairs from the app (labels are only
    shown, after being cleaned)."""
    from core import backup
    if not isinstance(files, list) or not 0 < len(files) <= 12:
        raise BadRequest("bad file list")
    lines, ok, newest = [], [], None
    for item in files:
        if (not isinstance(item, list) or len(item) != 2 or not all(isinstance(x, str) for x in item)
                or len(item[1]) > backup.MAX_BYTES * 2):
            raise BadRequest("bad file list")
        label = "".join(ch for ch in item[0] if ch.isalnum() or ch in " -:")[:22]
        try:
            payload = c.signer.open_backup(blob_b64=item[1])
            ok.append(True)
            newest = newest or payload
            lines.append(f"OK    {label}")
        except Exception:  # noqa: BLE001 - damaged, changed or not this seed's
            ok.append(False)
            lines.append(f"BAD   {label}")
    if newest is not None:
        lines += ["", "Newest holds:"] + backup.summary(newest)[1:4]
    bad = ok.count(False)
    head = ["Every copy opens and is", "unchanged."] if not bad else [
        f"{bad} of {len(ok)} won't open: damaged,", "changed, or another seed's."]
    _final(c, MessageScreen("BACKUP CHECK", (head + [""] + lines)[:12],
                            border="red" if bad else "green"), {"ok": ok})


def child_seed(c):
    """BIP85: a separate seed for another wallet, re-creatable from this one."""
    def pick(n):
        def go(c):
            def got_index(c, text):
                index = int(text)
                c.pop()

                def show(c, m):
                    def use(c):
                        c.job("OPENING SEED...", lambda: c.signer.use_temporary(
                            secret=m, passphrase="", label=f"CHILD #{index}"),
                            lambda c, info: c.finish({"words": n, "index": index,
                                                      "temporary": info}),
                            lambda c, e: c.push(MessageScreen("CAN'T USE IT", [str(e)[:40]],
                                                              border="red")))

                    def after(c):
                        c.push(MenuScreen(f"CHILD #{index}", [
                            ("DONE", lambda c: c.finish({"words": n, "index": index})),
                            ("USE AS TEMPORARY", use, "UNTIL LOCK", "grey"),
                            ("SHOW AS SEEDQR", lambda c: c.push(
                                SeedQRScreen(seedlib.seedqr_encode(m), n))),
                            ("SHOW WORDS AGAIN", lambda c: c.push(ShowWordsScreen(
                                m.split(), None, title=f"CHILD #{index}")))],
                            info=[f"{n} words, child number {index}.",
                                  "Remember the number: with your",
                                  "main seed it re-creates this one."]))
                    c.push(ShowWordsScreen(m.split(), after, title=f"CHILD #{index}"))
                _pin_job(c, "CHILD SEED", "DERIVING...",
                         lambda pin: c.signer.child_seed(pin=pin, words=n, index=index),
                         show, note=f"Show child seed #{index}")
                return None
            c.push(TextInputScreen("CHILD NUMBER", "Which child? (0, 1, 2...)", got_index,
                                   allowed="0123456789", max_len=6, initial="0"))
        return go

    c.push(MenuScreen("CHILD SEEDS", [("12 WORDS", pick(12)), ("18 WORDS", pick(18)),
                                      ("24 WORDS", pick(24))],
                      info=["A separate seed for another", "wallet. Your main backup can",
                            "always re-create it (BIP85)."]))


def split(c):
    """SPLIT BACKUP: K-of-N SLIP39 shares, each checked."""
    def write(c, m, k, n):
        if seedlib.is_xprv(m):
            _final(c, MessageScreen("NO SPLIT", ["Split backups are made from", "seed words; this wallet",
                                                 "came from an XPRV."]))
            return
        _write_shares(c, splitlib.split(m, k, n), k)

    def write_xor(c, m, n):
        if seedlib.is_xprv(m):
            _final(c, MessageScreen("NO SPLIT", ["Seed XOR is made from seed", "words; this wallet",
                                                 "came from an XPRV."]))
            return
        from core import seedxor
        _write_shares(c, [p.split() for p in seedxor.split(m, n)], n, xor=True)

    def xor(n):
        def go(c):
            _pin_job(c, "SEED XOR", "CHECKING PIN...",
                     lambda pin: c.signer.reveal_words(pin=pin),
                     lambda c, m: write_xor(c, m, n),
                     note=f"To write {n} Seed XOR parts")
        return go

    def scheme(k, n):
        def go(c):
            _pin_job(c, "SPLIT BACKUP", "CHECKING PIN...",
                     lambda pin: c.signer.reveal_words(pin=pin),
                     lambda c, m: write(c, m, k, n),
                     note=f"To write {k}-of-{n} shares")
        return go

    c.push(MenuScreen("SPLIT BACKUP", [
        (f"{k} OF {n}", scheme(k, n), f"{n} PAPERS", "grey") for k, n in splitlib.SCHEMES] + [
        (f"SEED XOR {n} PARTS", xor(n), "ALL NEEDED", "grey") for n in (2, 3, 4)],
        info=["Any K papers rebuild your", "seed; fewer reveal nothing.",
              "Keep them in different places."]))


def _write_shares(c, all_shares: list[list[str]], k: int, xor: bool = False):
    n = len(all_shares)
    word = "PART" if xor else "SHARE"

    def show(i):
        def go(c):
            if i == n:
                _check_shares(c, all_shares, k, xor)
                return
            c.push(ShowWordsScreen(all_shares[i], show(i + 1), title=f"{word} {i + 1} OF {n}"))
        return go

    if xor:
        intro = [f"You'll write {n} parts of", f"{len(all_shares[0])} words each. Label",
                 "them PART 1, PART 2...", "", f"ALL {n} are needed. Each one", "alone looks like any seed."]
    else:
        intro = [f"You'll write {n} papers of", f"{len(all_shares[0])} words each. Label",
                 "them SHARE 1, SHARE 2...", "", f"Any {k} rebuild your wallet."]
    c.push(MessageScreen("SEED XOR" if xor else "SPLIT BACKUP", intro, then=show(0)))


def _check_shares(c, all_shares, k, xor: bool = False):
    """Two random words from every paper, so each one is known good."""
    n = len(all_shares)
    word = "PART" if xor else "SHARE"

    def check(i):
        def go(c):
            if i == n:
                all_shares.clear()
                first = ([f"{n} parts written. All {n}", "rebuild your wallet (Seed XOR,",
                          "as on COLDCARD)."] if xor else
                         [f"{n} shares written, any {k}", "rebuild your wallet."])
                _final(c, MessageScreen("ALL CHECKED", first + [
                    "", "Your seed words still work", "too. Destroy them only if",
                    "you want this to be the", "only way back."]), {"k": k, "n": n})
                return
            picks = sorted(random.sample(range(1, len(all_shares[i]) + 1), 2))

            def done(c, words):
                if [all_shares[i][p - 1] for p in picks] != words:
                    c.pop()
                    c.push(MessageScreen("NOT QUITE", [f"{word.title()} {i + 1} doesn't match.",
                                                       "Let's look at it again."],
                                         border="red", then=lambda c: c.push(ShowWordsScreen(
                                             all_shares[i], check(i), title=f"{word} {i + 1} OF {n}"))))
                    return None
                c.pop()
                check(i + 1)(c)
                return None
            c.push(WordEntryScreen(f"CHECK {word} {i + 1}", picks, done,
                                   complete=None if xor else splitlib.completions))
        return go

    check(0)(c)


# ============================================================ PIN-guarded settings
def set_cap(c, cap_sats):
    try:
        cap = int(cap_sats)
    except (TypeError, ValueError):
        raise BadRequest("cap must be a number of sats")
    if not 0 <= cap <= 21_000_000 * 100_000_000:
        raise BadRequest("cap out of range")
    _pin_job(c, "SPEND CAP", "SAVING...", lambda pin: c.signer.set_cap(pin=pin, cap_sats=cap),
             lambda c, st: c.finish(st), note=f"New cap {fmt_sats(cap)} sats/day")


def change_pin(c):
    def check_old(pin):
        c.signer.check_pin(pin=pin)          # proves it (and counts a try)
        return pin

    def new(c, old):
        def first(c, p1):
            def again(c, p2):
                if p1 != p2:
                    return "PINs don't match"
                c.job("SAVING...", lambda: c.signer.change_pin(old_pin=old, new_pin=p1),
                      lambda c, _: _final(c, MessageScreen("DONE", ["PIN changed."])),
                      lambda c, e: c.fail(e))
                return None
            c.push(SecurePinScreen("CHANGE PIN", again, words=c.words, prompt="REPEAT PIN"))
            return None
        c.push(SecurePinScreen("CHANGE PIN", first, words=c.words, prompt="NEW PIN",
                               note=PIN_HINT))

    _pin_job(c, "CHANGE PIN", "CHECKING PIN...", check_old, new, note="Your current PIN first")


def wipe(c):
    def really(c):
        _pin_job(c, "WIPE", "WIPING...", lambda pin: c.signer.wipe(pin=pin),
                 lambda c, _: c.finish(True), note="This erases the seed")
    c.push(ConfirmScreen("WIPE DEVICE?", ["Erases the seed from this device.",
                                          "Only your written words can bring",
                                          "the coins back."], really, danger=True))


def duress(c):
    on = c.signer.status().get("duress")

    def remove(c):
        _pin_job(c, "DURESS PIN", "SAVING...", lambda pin: c.signer.clear_duress(pin=pin),
                 lambda c, _: _final(c, MessageScreen("DURESS PIN OFF",
                                                      ["The decoy wallet is gone."]), False))

    def setup(c):
        def check(pin):
            c.signer.check_pin(pin=pin)
            return pin

        def got_pin(c, pin):
            def got_duress(c, dpin):
                if dpin == pin:
                    return "Must differ from your PIN"

                def again(c, d2):
                    if d2 != dpin:
                        return "PINs don't match"

                    def save(erase):
                        return lambda c: c.job(
                            "SAVING...",
                            lambda: c.signer.set_duress(pin=pin, duress_pin=dpin, erase_real=erase),
                            lambda c, _: _final(c, MessageScreen("DURESS PIN ON", [
                                "Unlock once with the duress PIN",
                                "and send a small amount to the",
                                "decoy so it looks lived in.",
                                "Your PIN opens your real wallet."]), True),
                            lambda c, e: c.fail(e))
                    c.pop()
                    c.pop()
                    c.push(MenuScreen("WHEN USED", [
                        ("OPEN DECOY ONLY", save(False)),
                        ("DECOY + ERASE REAL", save(True), "", "red"),
                    ], info=["Erase: the real seed is gone", "for good. Only your backup",
                             "brings it back."]))
                    return None
                c.push(SecurePinScreen("DURESS PIN", again, words=c.words, prompt="REPEAT IT"))
                return None
            c.push(SecurePinScreen("DURESS PIN", got_duress, words=c.words, prompt="DURESS PIN",
                                   note=PIN_HINT))

        _pin_job(c, "DURESS PIN", "CHECKING PIN...", check, got_pin, note="Your real PIN first")

    if on:
        c.push(MenuScreen("DURESS PIN", [("CHANGE IT", setup), ("TURN OFF", remove)],
                          info=["A duress PIN is set."]))
    else:
        c.push(MenuScreen("DURESS PIN", [("SET ONE UP", setup)], info=[
            "If someone forces you to", "unlock, enter the duress PIN:", "it opens a decoy wallet."]))


def bind_chip(c):
    _pin_job(c, "SECURE CHIP", "BINDING TO CHIP...", lambda pin: c.signer.bind_chip(pin=pin),
             lambda c, st: c.finish(st), note="Bind the seed to this chip")


def unbind_chip(c):
    _pin_job(c, "SECURE CHIP", "UNBINDING...", lambda pin: c.signer.unbind_chip(pin=pin),
             lambda c, st: c.finish(st), note="Stop using the chip")


def quick_unlock(c, hours):
    if hours not in (0, 1, 4, 8):
        raise BadRequest("hours must be 0, 1, 4 or 8")
    note = "Turn quick unlock off" if not hours else f"Finger reopens for {hours} h"
    _pin_job(c, "QUICK UNLOCK", "SAVING...",
             lambda pin: c.signer.set_quick_unlock(pin=pin, hours=hours),
             lambda c, h: c.finish(h), note=note)


def auto_lock(c, seconds):
    """SETUP > AUTO LOCK, made longer or OFF: the signer forgets the seed
    after this long without a key press, so only the PIN can stretch it."""
    if isinstance(seconds, bool) or not isinstance(seconds, int) or \
            not (seconds == 0 or 60 <= seconds <= 24 * 3600):
        raise BadRequest("auto lock must be 1 minute to 24 hours, or off")
    if seconds == 0:
        note = "Never lock by itself"
    else:
        m = seconds // 60
        note = f"Lock after {m} min idle" if m < 120 else f"Lock after {m // 60} h idle"
    _pin_job(c, "AUTO LOCK", "SAVING...",
             lambda pin: c.signer.set_auto_lock(pin=pin, seconds=seconds),
             lambda c, v: c.finish(v), note=note)


# ============================================================ wallets
def register(c, descriptor, name=""):
    """Show what the signer understood of a multisig or inheritance wallet;
    save it only on SAVE."""
    if not isinstance(descriptor, str) or len(descriptor) > 20_000:
        raise BadRequest("not a descriptor")
    name = str(name)[:16]
    info = c.signer.check_multisig(descriptor=descriptor)
    mine = c.signer.public_info()["multisig_key"][1:9]
    if info.get("kind") == "inherit":
        from core.inherit import describe_delay
        o, h = info["cosigners"]
        lines = [f"OWNER {o}" + ("  THIS DEVICE" if o == mine else ""),
                 f"HEIR  {h}" + ("  THIS DEVICE" if h == mine else ""),
                 f"Heir waits {describe_delay(info['delay'])}",
                 f"({info['delay']:,} blocks) per coin."]
    elif info.get("kind") == "miniscript":
        from embit.descriptor import Descriptor
        letters = "ABCDEFGHIJKLMNOPQRST"
        lines = [("Taproot" if info.get("taproot") else "SegWit") + f" miniscript, {info['n']} keys:"]
        for i, fp in enumerate(info["cosigners"]):
            lines.append(f"  {letters[i]} = {fp}" + ("  THIS DEVICE" if fp == mine else ""))
        try:
            rules = Descriptor.from_string(info["descriptor"]).full_policy
        except Exception:  # noqa: BLE001 - shown as is
            rules = "?"
        lines += ["", "Who can spend:", rules, ""]
    else:
        lines = [f"{info['k']} of {info['n']} signatures" +
                 (", Taproot (no key path)." if info.get("taproot") else ", SegWit.")]
        for fp in info["cosigners"]:
            lines.append(f"  {fp}" + ("  THIS DEVICE" if fp == mine else ""))

    def yes(c):
        c.job("SAVING...", lambda: c.signer.register_multisig(descriptor=descriptor, name=name),
              lambda c, e: c.finish(e), lambda c, e: c.fail(e))
    c.push(ConfirmScreen("CHECK SIGNERS", lines + ["Match the fingerprints!"], yes, yes="SAVE"))


class SignMessageScreen(Screen):
    """The address and the whole message, then YES/NO. UP/DOWN scroll."""
    title = "SIGN MESSAGE"
    ROWS = 5

    def __init__(self, address, message, fmt, on_yes):
        self.addr, self.message, self.fmt, self.on_yes = address, message, fmt, on_yes
        self.lines = []
        for para in message.split("\n"):
            for part in wrap(para, 44) or [""]:
                # a single long "word" (no spaces) is cut, never hidden
                self.lines += [part[i:i + 44] for i in range(0, len(part), 44)] or [""]
        self.top, self.sel = 0, 1

    def handle(self, ev, app):
        if ev.kind is Kind.NAV and ev.value in ("UP", "DOWN"):
            step = -1 if ev.value == "UP" else 1
            self.top = max(0, min(max(0, len(self.lines) - self.ROWS), self.top + step))
        elif ev.kind is Kind.NAV:
            self.sel ^= 1
        elif ev.kind is Kind.SELECT:
            if self.sel == 1:
                app.pop()
            elif self.seen_all:
                self.on_yes(app)
        else:
            super().handle(ev, app)

    @property
    def seen_all(self) -> bool:
        return self.top + self.ROWS >= len(self.lines)

    def render(self, app, frame):
        img, d = t.new_canvas()
        t.sky(img, d, hills=False)
        app.status_bar(img, d, self)
        t.window(d, (4, 20, W - 5, 114), "SIGN MESSAGE", "gold")
        t.text(d, (12, 30), "AS " + short_addr(self.addr) + "  " + self.fmt.upper(), TINY, "gold")
        for i, line in enumerate(self.lines[self.top:self.top + self.ROWS]):
            t.text(d, (12, 44 + i * 12), line, TINY, "white")
        if not self.seen_all:
            t.text(d, (W - 14, 104), "v MORE", TINY, "cyan", align="right")
        q = "Sign this message?" if self.seen_all else "Scroll down to read it all."
        t.dialog(img, d, [q], box=(4, 120, 214, H - 4))
        labels = ["YES", "NO"] if self.seen_all else ["--", "NO"]
        t.menu(img, d, (220, 120, W - 5, H - 4), labels, self.sel, frame=frame, row_h=16)
        return img


def sign_message(c, index, message, fmt="electrum", account=None):
    """Prove you control an address by signing a text with its key. The
    text comes from the app, so all of it is shown here before YES works."""
    from core import message as msg
    if not isinstance(index, int) or not 0 <= index < 2 ** 31:
        raise BadRequest("bad index")
    if fmt not in msg.FORMATS:
        raise BadRequest("unknown signature format")
    try:
        msg.check_text(message)
    except msg.MessageError as e:
        raise BadRequest(str(e))
    from core import accounts as accts
    try:
        kind, _ = accts.parse(account)
    except accts.AccountError as e:
        raise BadRequest(str(e))
    if kind == "tr" and fmt != "bip322":
        raise BadRequest("Taproot addresses sign with BIP322 only")
    addr = c.signer.address(wallet_id=account, index=index)

    def yes(c):
        c.job("SIGNING...", lambda: c.signer.sign_message(index=index, message=message, fmt=fmt,
                                                          account=account),
              lambda c, out: c.finish(out), lambda c, e: c.fail(e))
    c.push(SignMessageScreen(addr, message, fmt, yes))


def show_address(c, wallet_id=None, index=0, change=False):
    if wallet_id is not None and not isinstance(wallet_id, str):
        raise BadRequest("bad wallet id")
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < 2 ** 31:
        raise BadRequest("bad index")
    if not isinstance(change, bool):
        raise BadRequest("bad change flag")
    addr = c.signer.address(wallet_id=wallet_id, index=index, change=change)
    _final(c, AddressScreen(addr, f"{'CHANGE' if change else 'ADDR'} #{index}"), addr)


__all__ = ["BadRequest", "ConfirmSendScreen", "SecurePinScreen", "AddressScreen"]


# ================================================================ paper wallets
def paper(c):
    """Paper wallets, like COLDCARD's: a brand-new key, unrelated to the seed,
    kept on the device as <address>.txt (with text QR codes) and a printable
    <address>.pdf. Made here in the signer; the app only learns the address.
    Make as many as you like; DONE hands the list back to the app, which has
    the files moved to the paper folder (only SHARE FILES can read it)."""
    from core import paper as paperlib
    made: list[dict] = []
    opts = {"kind": "segwit", "pdf": True}

    def finish(c):
        c.finish({"made": made})

    def make(c, rolls=""):
        def done(c, r):
            made.append(r)
            short = r["address"]
            c.push(MenuScreen("PAPER WALLET", [
                ("MAKE ANOTHER", lambda c: c.pop()),
                ("DONE", finish)],
                info=["Saved: " + short[:22], "       " + short[22:44],
                      "Address, private key + QR codes",
                      "(.txt" + (" and .pdf)" if opts["pdf"] else ")") + ". SMALL AMOUNTS!"],
                back=False))
        c.job("MAKING KEY...", lambda: c.signer.paper_wallet(
            kind=opts["kind"], rolls=rolls, with_pdf=opts["pdf"]), done)

    def dice(c):
        need = paperlib.DICE_MIN

        def ok(c, rolls):
            if len(rolls) < need:
                return f"Need {need} rolls, have {len(rolls)}"
            c.pop()
            make(c, rolls)
            return None
        c.push(TextInputScreen("USE DICE", f"Type {need}+ real dice rolls:", ok,
                               allowed="123456", max_len=300,
                               info=lambda r: f"{len(r)}/{need} rolls = "
                                              f"{seedlib.dice_bits(r):.0f} bits"))

    def kind(c, step):
        opts["kind"] = "classic" if opts["kind"] == "segwit" else "segwit"

    def pdf(c, step):
        opts["pdf"] = not opts["pdf"]

    def items():
        return [
            ("ADDRESS TYPE", None, paperlib.KINDS[opts["kind"]], "cyan", kind),
            ("PRINTABLE PDF", None, "ON" if opts["pdf"] else "OFF",
             "green" if opts["pdf"] else "grey", pdf),
            ("USE DICE", dice, "99+ ROLLS", "grey"),
            ("GENERATE WALLET", lambda c: make(c), "", "gold"),
            ("DONE", finish),
        ]

    def menu(c):
        c.push(MenuScreen("PAPER WALLETS", items,
                          info=["A new key, NOT from your seed.",
                                "Get it with SHARE FILES."]))

    c.push(MessageScreen("PAPER WALLETS", [
        "Makes a new private key that has",
        "nothing to do with your seed,",
        "kept on this device as a text file",
        "with QR codes and a PDF to print.",
        "Download it with SHARE FILES.",
        "SMALL AMOUNTS ONLY: anyone who",
        "sees the paper can take the coins."], then=menu))
