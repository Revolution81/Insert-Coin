"""
SETUP > SHARE FILES: download history CSVs (and, if you switch them on, paper
wallets) on a phone or PC on the same Wi-Fi. See core/share.py.
"""
from __future__ import annotations

import os
import time

from core import share

from . import theme as t
from .input import Kind
from .qrscreens import AnimatedQRScreen
from .widgets import ConfirmScreen, MenuScreen, MessageScreen, TextInputScreen

MINUTES = (5, 10, 15, 30)


def _hash(app) -> str:
    s = app.settings
    return s.share_pass_hash or share.hash_password(share.DEFAULT_PASS)


def share_menu(app):
    s = app.settings
    opts = {"paper": False}

    def start(app):
        if not app.online and not os.environ.get("CZ_SIM_SHARE_ANY"):
            app.push(MessageScreen("GO ONLINE FIRST", [
                "Sharing needs Wi-Fi: choose MODE",
                "and go Online, then come back."]))
            return
        if opts["paper"] and s.share_pass_hash is None:
            # paper wallets are private keys: strongly suggest a password of
            # your own (the default is printed in the booklet), but it's your call
            app.push(ConfirmScreen("CHANGE PASSWORD?", [
                "Paper wallets hold private keys,",
                "and the password is still the",
                f"default ({share.DEFAULT_PASS}). Anyone on your",
                "Wi-Fi who knows it could take",
                "them. Better: set your own",
                "PASSWORD first."], go, danger=True, yes="SHARE ANYWAY", no="BACK"))
            return
        go(app)

    def go(app):
        if opts["paper"]:
            from .flow import move_paper
            move_paper()                # any the last MAKE NEW couldn't move
        app.push(SharingScreen(include_paper=opts["paper"]))

    def paper(app, step):
        opts["paper"] = not opts["paper"]

    def minutes(app, step):
        i = MINUTES.index(s.share_minutes) if s.share_minutes in MINUTES else 1
        s.share_minutes = MINUTES[(i + step) % len(MINUTES)]
        s.save()

    def change_pw(app):
        def ok(app, text):
            if text and len(text) < 6:
                return "At least 6 characters"
            s.share_pass_hash = share.hash_password(text) if text else None
            s.save()
            app.pop()
            app.push(MessageScreen("PASSWORD", [
                "Saved." if text else f"Back to the default: {share.DEFAULT_PASS}"]))
            return None
        app.push(TextInputScreen("SHARE PASSWORD", "New password (empty = bitcoin):", ok,
                                 max_len=40, empty_ok=True))

    def items():
        custom = s.share_pass_hash is not None
        return [
            ("START SHARING", start, "", "gold"),
            ("PAPER WALLETS", None, "INCLUDE" if opts["paper"] else "LEAVE OUT",
             "red" if opts["paper"] else "grey", paper),
            ("TIME LIMIT", None, f"{s.share_minutes} MIN", "cyan", minutes),
            ("LOGIN", None, s.share_user, "grey"),
            ("PASSWORD", change_pw, "YOURS" if custom else share.DEFAULT_PASS,
             "green" if custom else "red"),
        ]

    app.push(MenuScreen("SHARE FILES", items,
                        info=["Download your files on a phone or",
                              "PC on the same Wi-Fi. Download only."]))


class SharingScreen(AnimatedQRScreen):
    """Sharing is on while this screen is up: address QR, login, time left.
    OK, DEL or HOME stops it. AUTO LOCK waits while it's up (keep_awake), and
    the signer is told the wallet is in use, so the time limit you picked is
    the only thing that ends it."""
    keep_awake = True

    def __init__(self, include_paper: bool = False):
        super().__init__("SHARING", ["https://"], ["Starting..."], fps=1.0)
        self.done_hint = "OK STOP SHARING"
        self.include_paper = include_paper
        self.ctl = None
        self.error = ""
        self._url = ""

    def on_show(self, app):
        if self.ctl is not None:
            return
        from core import config
        self.ctl = share.controller(os.path.join(config.APP_DIR, "share"))
        try:
            self.ctl.start(app.settings.share_user, _hash(app), self.include_paper,
                           app.settings.share_minutes * 60)
        except Exception as e:  # noqa: BLE001 - port busy, service missing...
            self.error = str(e)[:60] or "couldn't start"
            self.ctl = None

    def on_hide(self, app):
        if self.ctl is not None:
            self.ctl.stop()
            self.ctl = None
        app.last_input = time.monotonic()      # AUTO LOCK counts from when sharing ends

    def handle(self, ev, app):
        if ev.kind in (Kind.SELECT, Kind.BACK, Kind.HOME):
            app.pop()
            app.push(MessageScreen("SHARING STOPPED", ["The page is closed. Nothing on",
                                                       "the network can reach it now."]))

    def render(self, app, frame):
        if self.error:
            self.lines = ["Couldn't start sharing:", self.error, "", "OK to go back."]
            self.frames = ["insert coin"]
            return super().render(app, frame)
        st = self.ctl.status() if self.ctl else None
        now = time.monotonic()
        if st and now - getattr(self, "_kept", 0) > 20:      # keep the seed unlocked too
            self._kept = now
            try:
                app.signer.touch()
            except Exception:  # noqa: BLE001
                pass
        if st is None and getattr(self, "_seen", False):     # it was up: time's up
            app.pop()
            app.push(MessageScreen("SHARING ENDED", ["Time's up: the page is closed."]))
            return t.new_canvas()[0]
        if st is None and not getattr(self, "_seen", False) and self.ctl is not None:
            started = getattr(self, "_started", None) or now
            self._started = started
            if now - started > 30:                          # never came up
                try:
                    self.ctl.stop()
                except Exception:  # noqa: BLE001  (it never started anyway)
                    pass
                self.ctl = None
                self.error = "the sharing service didn't start"
                return self.render(app, frame)
        if st:
            self._seen = True
            if st["url"] != self._url:
                self._url = st["url"]
                self.frames = [st["url"]]
                self._cache.clear()
            m, sec = divmod(st["left"], 60)
            pw = share.DEFAULT_PASS if app.settings.share_pass_hash is None else "(yours)"
            self.lines = [st["url"].split("//")[1], f"USER {app.settings.share_user}",
                          f"PASS {pw}", f"ENDS IN {m}:{sec:02d}",
                          "PAPER: " + ("SHARED!" if self.include_paper else "NOT SHARED"),
                          "FINGERPRINT", share.short_fingerprint(st["fingerprint"])]
        return super().render(app, frame)
