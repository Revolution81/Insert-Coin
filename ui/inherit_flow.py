"""
Inheritance wallet screens: set one up as the owner or the heir, refresh the
timer (owner), and the home-screen status lines.
"""
from __future__ import annotations

from core.inherit import BLOCKS_PER_DAY, DELAYS, TEST_DELAYS, build, describe_delay
from core.multisig import PolicyError, parse_key

from .qrscreens import ScanScreen
from .widgets import ConfirmScreen, MenuScreen, MessageScreen

WARN_BLOCKS = 30 * BLOCKS_PER_DAY        # owner gets a warning a month before


def summary_line(e: dict) -> str:
    who = "You own it" if e["role"] == "owner" else "You're the heir"
    return f"{who}; heir waits {describe_delay(e['delay']).lower()}"


def days(blocks: int) -> str:
    d = blocks / BLOCKS_PER_DAY
    return f"{blocks} BLOCKS" if d < 1 else f"{round(d)} DAYS"


def home_lines(app) -> tuple[tuple[str, str], tuple[str, str] | None]:
    """(sub line, status line or None) for the home screen."""
    w = app.wallet
    role = "OWNER" if w.role == "owner" else "HEIR"
    sub = (f"{app.wallet_name.upper()[:12]}  INHERITANCE {role}", "gold")
    t = w.timer()
    if w.role == "owner":
        if t["unlocked"]:
            return sub, ("HEIR CAN SPEND NOW: REFRESH (W)", "red")
        if t["soonest"] is not None and t["soonest"] <= WARN_BLOCKS:
            return sub, (f"REFRESH SOON: HEIR IN ~{days(t['soonest'])}", "gold")
        if t["unknown"]:
            return sub, ("GO ONLINE TO CHECK THE TIMER", "grey")
        if t["soonest"] is not None:
            return sub, (f"HEIR LOCKED OUT ~{days(t['soonest'])}", "grey")
        return sub, None
    if t["unlocked"]:
        return sub, (f"UNLOCKED {t['unlocked']:,} SATS", "green")
    if t["soonest"] is not None:
        return sub, (f"NEXT UNLOCK IN ~{days(t['soonest'])}", "cyan")
    if t["unknown"]:
        return sub, ("GO ONLINE TO CHECK THE TIMER", "grey")
    return sub, None


def new_inheritance(app):
    mine = app.public["multisig_key"]
    test_net = app.settings.network != "bitcoin"

    def pick_role(role):
        other = "HEIR" if role == "owner" else "OWNER"

        def go(app):
            def got(app, res):
                try:
                    key = parse_key(res.text or res.address, app.settings.network)
                except PolicyError as e:
                    app.push(MessageScreen("NOT A KEY", [str(e)], border="red"))
                    return
                if key[1:9] == mine[1:9]:
                    app.push(MessageScreen("SAME DEVICE", ["That's this device's own key.",
                                                           f"Scan the {other.lower()}'s key."],
                                           border="red"))
                    return
                owner, heir = (mine, key) if role == "owner" else (key, mine)
                pick_delay(app, owner, heir)
            app.push(ScanScreen(f"{other} KEY", got, accept=("text", "address"),
                                hint=f"SCAN THE {other}'S KEY QR"))
        return go

    def pick_delay(app, owner, heir):
        from .multisig_flow import _confirm_and_register

        def choose(blocks):
            return lambda app: _confirm_and_register(app, build(owner, heir, blocks), "Inheritance")
        options = (TEST_DELAYS if test_net else []) + DELAYS
        app.push(MenuScreen("HEIR WAITS", [(label, choose(b), f"{b:,} BLK", "grey")
                                           for label, b in options],
                            info=["Per coin, from its last move.", "Refreshing restarts it."]))

    app.push(MenuScreen("INHERITANCE", [
        ("I'M THE OWNER", pick_role("owner")),
        ("I'M THE HEIR", pick_role("heir")),
    ], info=["Owner spends any time. The", "heir only after coins sit", "unmoved for a while."]))


def refresh_timer(app):
    """Owner: move every coin back into this wallet to restart the timer."""
    from .flow import sign_and_send
    app.job("BUILDING...", lambda: app.wallet.refresh(app.fees()["normal"]), sign_and_send)


__all__ = ["new_inheritance", "refresh_timer", "home_lines", "summary_line", "ConfirmScreen"]
