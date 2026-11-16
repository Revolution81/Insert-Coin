"""
SETUP > SECURITY: LOGIN COUNTDOWN, WIPE PIN and SPENDING POLICY.

Every change here is made by the signer on its own (gold) screen with your
PIN; with the policy locked, its bypass PIN first. These menus only choose
what to ask for.
"""
from __future__ import annotations

from .theme import fmt_sats

from .widgets import MenuScreen, MessageScreen, TextInputScreen


def _mins(seconds: int) -> str:
    if not seconds:
        return "OFF"
    m = seconds // 60
    if m >= 60 and m % 60 == 0:
        return f"{m // 60} H"
    return f"{m} MIN"


def _sats(v: int) -> str:
    return "NO LIMIT" if not v else f"{fmt_sats(v)}"


def _done(app, _=None):
    """Back in a menu after the signer's screen: show the new values."""
    top = app.top
    if hasattr(top, "on_show"):
        top.on_show(app)


# ------------------------------------------------------------ login countdown
COUNTDOWNS = [("5 MIN", 300), ("15 MIN", 900), ("30 MIN", 1800), ("1 HOUR", 3600)]


def countdown_menu(app):
    cur = app.signer.status().get("countdown_s", 0)

    def set_to(seconds):
        def go(app):
            def done(app, v):
                app.pop()
                app.push(MessageScreen("LOGIN COUNTDOWN", [
                    f"Login countdown: {_mins(int(v))}."] + (
                    ["After each correct PIN the", "wallet waits this long before",
                     "it opens. Someone forcing you",
                     "to unlock has to wait too."] if v else [])))
            app.prompt("countdown_prompt", done, seconds=seconds)
        return go

    def custom(app):
        def ok(app, text):
            if not text.isdigit() or not 1 <= int(text) <= 1440:
                return "Type minutes: 1 to 1440"
            app.pop()
            set_to(int(text) * 60)(app)
            return None
        app.push(TextInputScreen("CUSTOM", "Wait how many minutes?", ok,
                                 allowed="0123456789", max_len=4))

    def mark(v):
        return "NOW" if v == cur else ""
    items = [(label, set_to(v), mark(v), "green") for label, v in COUNTDOWNS]
    is_custom = cur and cur not in [v for _, v in COUNTDOWNS]
    items.append(("CUSTOM", custom, _mins(cur) if is_custom else "", "green"))
    items.append(("OFF", set_to(0), mark(0), "grey"))
    app.push(MenuScreen("LOGIN COUNTDOWN", items, info=[
        "Wait this long after the right", "PIN before the wallet opens.",
        "Any change needs your PIN."]))


# ------------------------------------------------------------ wipe PIN
def wipe_pin_menu(app):
    on = app.signer.status().get("wipe_pin")

    def ask(action):
        def go(app):
            app.prompt("wipe_pin_prompt", lambda app, _: app.pop(), action=action)
        return go
    if on:
        items = [("CHANGE IT", ask("set")), ("TURN OFF", ask("clear"))]
        info = ["A wipe PIN is set. Entered at", "unlock, it erases the wallet",
                "and the device starts like new."]
    else:
        items = [("SET ONE UP", ask("set"), "", "red")]
        info = ["A PIN that erases everything", "when entered at unlock. The",
                "device then starts like new."]
    app.push(MenuScreen("WIPE PIN", items, info=info))


# ------------------------------------------------------------ spending policy
def policy_menu(app):
    def ask(field, value):
        app.prompt("policy_prompt", _done, field=field, value=value)

    def amount(title, prompt, field, current):
        def go(app):
            def ok(app, text):
                if not text.isdigit():
                    return "Numbers only (0 = no limit)"
                app.pop()
                ask(field, int(text))
                return None
            app.push(TextInputScreen(title, prompt, ok, allowed="0123456789", max_len=12,
                                     initial=str(current or ""), empty_ok=False,
                                     info=lambda s: "0 = no limit" if not s or s == "0"
                                     else f"= {fmt_sats(int(s))} sats"))
        return go

    def cooldown(app):
        cur = app.signer.policy_info()["cooldown_s"]
        choices = [("10 MIN", 600), ("1 HOUR", 3600), ("6 HOURS", 21600), ("24 HOURS", 86400)]

        def set_to(v):
            return lambda app: (app.pop(), ask("cooldown_s", v))

        def custom(app):
            def ok(app, text):
                if not text.isdigit() or not 1 <= int(text) <= 10080:
                    return "Type minutes: 1 to 10080"
                app.pop()
                app.pop()
                ask("cooldown_s", int(text) * 60)
                return None
            app.push(TextInputScreen("COOLDOWN", "Minutes between payments:", ok,
                                     allowed="0123456789", max_len=5))
        items = [(label, set_to(v), "NOW" if v == cur else "", "green") for label, v in choices]
        is_custom = cur and cur not in [v for _, v in choices]
        items.append(("CUSTOM", custom, _mins(cur) if is_custom else "", "green"))
        items.append(("OFF", set_to(0), "NOW" if not cur else "", "grey"))
        app.push(MenuScreen("COOLDOWN", items, info=["Least time between two", "payments."]))

    def max_fee(app):
        cur = app.signer.policy_info()["max_fee"]

        def ok(app, text):
            if not text.isdigit() or not 2 <= int(text) <= 5000:
                return "2 to 5000 sat/vB"
            app.pop()
            ask("max_fee", int(text))
            return None
        app.push(TextInputScreen("MAX FEE", "Highest fee rate (sat/vB):", ok,
                                 allowed="0123456789", max_len=4, initial=str(cur)))

    def items():
        p = app.signer.policy_info()
        return [
            ("PER PAYMENT", amount("PER PAYMENT", "Most one payment may send:", "per_tx",
                                   p["per_tx"]), _sats(p["per_tx"]), "gold"),
            ("PER DAY", amount("PER DAY", "Most all payments may send a day:", "per_day",
                               p["per_day"]), _sats(p["per_day"]), "gold"),
            ("COOLDOWN", cooldown, _mins(p["cooldown_s"]), "gold"),
            ("ALLOWED ADDRESSES", allow_menu,
             (f"ON ({len(p['allow'])})" if p["allow_on"] else
              f"OFF ({len(p['allow'])})" if p["allow"] else "OFF"),
             "green" if p["allow_on"] else "grey"),
            ("PHONE 2FA", totp_menu, ("ON" if not p["totp_above"] else
                                      f"> {fmt_sats(p['totp_above'])}") if p["totp"] else "OFF",
             "green" if p["totp"] else "grey"),
            ("MAX FEE", max_fee, f"{p['max_fee']} SAT/VB", "gold"),
            ("BYPASS PIN", bypass_menu, "LOCKED" if p["locked"] else "OFF",
             "red" if p["locked"] else "grey"),
        ]
    app.push(MenuScreen("SPENDING POLICY", items, info=[
        "Checked by the signer before", "every payment, Vault or Online.",
        "Change, self-sends: always OK."]))


def allow_menu(app):
    def ask(field, value, then=_done):
        app.prompt("policy_prompt", then, field=field, value=value)

    def add_typed(app):
        def ok(app, text):
            try:
                addr = app.wallet.validate_address(text.strip())
            except Exception as e:  # noqa: BLE001
                return str(e)[:40]
            app.pop()
            ask("allow_add", addr)
            return None
        app.push(TextInputScreen("ADD ADDRESS", "Address to allow:", ok, max_len=90))

    def add_scanned(app):
        from .qrscreens import ScanScreen

        def got(app, res):
            try:
                addr = app.wallet.validate_address(res.address)
            except Exception as e:  # noqa: BLE001
                app.pop()
                app.push(MessageScreen("NOT AN ADDRESS", [str(e)[:80]], border="red"))
                return
            app.pop()
            ask("allow_add", addr)
        app.push(ScanScreen("SCAN ADDRESS", got, accept=("address",),
                            hint="POINT AT AN ADDRESS QR"))

    def remove(addr):
        def go(app):
            ask("allow_remove", addr)
        return go

    def items():
        p = app.signer.policy_info()
        rows = [("ALLOW-LIST", None, "ON" if p["allow_on"] else "OFF",
                 "green" if p["allow_on"] else "grey",
                 lambda app, step: ask("allow_on", not p["allow_on"])),
                ("ADD BY QR", add_scanned),
                ("ADD BY TYPING", add_typed)]
        for a in p["allow"]:
            rows.append((a[:10] + "..." + a[-6:], remove(a), "REMOVE", "red"))
        return rows
    app.push(MenuScreen("ALLOWED ADDRESSES", items, info=[
        "When ON, payments may only go", "to these addresses. Pick one",
        "to remove it."]))


def totp_menu(app):
    p = app.signer.policy_info()

    def ask_above(app):
        def ok(app, text):
            if not text.isdigit():
                return "Numbers only (0 = every payment)"
            app.pop()
            app.prompt("policy_prompt", _done, field="totp_above", value=int(text))
            return None
        app.push(TextInputScreen("ASK ABOVE", "Ask for a code above (sats):", ok,
                                 allowed="0123456789", max_len=12,
                                 initial=str(p["totp_above"] or "0"),
                                 info=lambda s: "0 = every payment" if not s or s == "0"
                                 else f"= {fmt_sats(int(s))} sats"))

    def turn(action):
        return lambda app: (app.pop(), app.prompt("totp_prompt", _done, action=action))
    if p["totp"]:
        items = [("ASK ABOVE", ask_above, "EVERY" if not p["totp_above"]
                  else fmt_sats(p["totp_above"]), "gold"),
                 ("TURN OFF", turn("off"), "", "red")]
        info = ["Payments need a 6-digit code", "from your phone's app."]
    else:
        items = [("TURN ON", turn("on"), "", "green")]
        info = ["Payments need a 6-digit code", "from an authenticator app on",
                "your phone. Needs the clock", "set (go Online once)."]
    app.push(MenuScreen("PHONE 2FA", items, info=info))


def bypass_menu(app):
    locked = app.signer.policy_info()["locked"]

    def ask(action):
        return lambda app: (app.pop(), app.prompt("bypass_prompt", _done, action=action))
    if locked:
        items = [("CHANGE IT", ask("set")), ("REMOVE", ask("clear"), "", "red")]
        info = ["The policy is locked: changes", "need the bypass PIN, and seed",
                "words can't be shown."]
    else:
        items = [("SET ONE UP", ask("set"), "", "gold")]
        info = ["Locks the policy: your PIN", "alone can't change it, and the",
                "seed can't be shown or", "exported until it's removed."]
    app.push(MenuScreen("BYPASS PIN", items, info=info))
