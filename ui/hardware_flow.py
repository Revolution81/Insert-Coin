"""
SETUP > HARDWARE: secure chip and fingerprint sensor: what's detected, and
turning each on or off. Also the fingerprint enroll screen and the
fingerprint-aware unlock screen.
"""
from __future__ import annotations

import threading
import time

from . import theme as t
from .input import Kind
from .theme import BIG, BODY, H, TINY, W
from .widgets import ConfirmScreen, MenuScreen, MessageScreen, Screen

CHIP_LABELS = {  # state -> (label, color)
    "in_use": ("IN USE", "gold"), "missing": ("MISSING", "red"), "ready": ("READY", "green"),
    "blank": ("BLANK", "cyan"), "absent": ("NONE", "grey"), "unusable": ("ERROR", "red"),
    "error": ("ERROR", "red"),
}


def chip_state(app) -> str:
    c = app.hw["chip"]
    if c.get("in_use"):
        return "in_use" if c.get("state") == "ready" else "missing"
    return c.get("state", "absent")


def finger_state(app) -> str:
    f = app.hw["finger"]
    if f.get("state") != "ready":
        return "absent"
    if app.settings.finger_unlock and f.get("saved"):
        return "on"
    return "ready"


def status_icons(app) -> list:
    icons = []
    cs = chip_state(app)
    if cs != "absent":
        icons.append((t.SHIELD, {"in_use": "gold", "missing": "red"}.get(cs, "grey")))
    fs = finger_state(app)
    if fs != "absent":
        icons.append((t.FINGER, "cyan" if fs == "on" else "grey"))
    if app.hw.get("nfc", {}).get("state") == "ready":
        on = bool(getattr(app.nfc, "field", False) or getattr(getattr(app.nfc, "chip", None),
                                                              "field", False))
        icons.append((t.NFC, "cyan" if on else "grey"))
    return icons


# ------------------------------------------------------------------ menu
def hardware_menu(app):
    cl, cc = CHIP_LABELS.get(chip_state(app), ("?", "grey"))
    fs = finger_state(app)
    f = app.hw["finger"]
    fl, fc = {"absent": ("NONE", "grey"), "ready": (f"{f.get('saved', 0)} SAVED", "light"),
              "on": ("QUICK UNLOCK", "cyan")}[fs]

    def rescan(app):
        app.job("SCANNING...", app.refresh_hardware, lambda app, _: (app.pop(), hardware_menu(app)))

    app.push(MenuScreen("HARDWARE", [
        ("SECURE CHIP", chip_menu, cl, cc),
        ("FINGERPRINT", finger_menu, fl, fc),
        ("NFC + SUB-GHZ", _nfc_info, *(("READY", "light") if app.hw.get("nfc", {}).get("state")
                                       == "ready" else ("NONE", "grey"))),
        ("RESCAN", rescan),
    ], info=["Add-ons on the Grove port", "or the EXT header."]))


def _nfc_info(app):
    from .nfc_flow import nfc_info
    nfc_info(app)


def _reopen(app):
    app.home()
    from .flow import settings_screen
    settings_screen(app, at="HARDWARE")
    hardware_menu(app)


def chip_menu(app):
    c, state = app.hw["chip"], chip_state(app)
    info = [f"{c.get('kind', 'Secure chip')} at 0x{c.get('address', 0):02x}"
            if state != "absent" else "No secure chip found."]
    items = []
    if state == "ready":
        info.append("Detected, not in use yet.")

        def use(app):
            app.prompt("bind_chip_prompt",
                       lambda app, st: (app.refresh_hardware(), _reopen(app), app.push(MessageScreen(
                           "CHIP IN USE", ["Your seed now needs this chip",
                                           "to unlock. A copied SD card",
                                           "is useless on its own.",
                                           "Set the duress PIN again if",
                                           "you had one."]))))
        items.append(("USE IT", lambda app: app.push(ConfirmScreen("USE CHIP?", [
            "Unlocking will need this chip.",
            "If it breaks or is lost, you",
            "restore from your seed words.",
            "Any duress PIN is cleared."], use))))
    elif state == "in_use":
        info.append("Your seed is bound to it.")

        def stop(app):
            app.prompt("unbind_chip_prompt",
                       lambda app, st: (app.refresh_hardware(), _reopen(app)))
        items.append(("STOP USING IT", stop, "", "red"))
    elif state == "missing":
        info += ["Your seed needs the chip,", "but it isn't connected."]
    elif state == "blank":
        info += ["A blank chip can enforce a", "hard PIN-try limit itself."]
        items.append(("ABOUT BLANK CHIPS", lambda app: app.push(MessageScreen("BLANK CHIP", [
            "Setting a blank chip up is",
            "permanent (its config locks",
            "forever), so this mode is",
            "finished and tested on a real",
            "chip first. A Trust&GO chip",
            "(M5Stack Unit ID) works now."]))))
    elif state in ("unusable", "error"):
        info.append(str(c.get("error", ""))[:30])
    app.push(MenuScreen("SECURE CHIP", items or [("BACK", lambda app: app.pop())], info=info))


def finger_menu(app):
    f = app.hw["finger"]
    if f.get("state") != "ready":
        app.push(MessageScreen("FINGERPRINT", ["No sensor found. Plug the",
                                               "Unit Fingerprint2 into the",
                                               "Grove port, then RESCAN."]))
        return
    on = app.settings.finger_unlock

    def toggle(app):
        if on:
            app.prompt("quick_unlock_prompt",
                       lambda app, _: (setattr(app.settings, "finger_unlock", False),
                                       app.settings.save(), _reopen(app)), hours=0)
            return
        if not f.get("saved"):
            app.push(MessageScreen("ADD A FINGER", ["Add a finger first."]))
            return

        def hours(h):
            def go(app):
                app.prompt("quick_unlock_prompt",
                           lambda app, _: (setattr(app.settings, "finger_unlock", True),
                                           app.settings.save(), _reopen(app)), hours=h)
            return go
        app.push(MenuScreen("QUICK UNLOCK", [("1 HOUR", hours(1)), ("4 HOURS", hours(4)),
                                             ("8 HOURS", hours(8))], info=[
            "After a PIN unlock, a finger", "reopens the screen for this",
            "long. Then the PIN again."]))

    def delete_all(app):
        def yes(app):
            app.finger.delete_all()
            app.settings.finger_unlock = False
            app.settings.save()
            app.refresh_hardware()
            _reopen(app)
        app.push(ConfirmScreen("DELETE FINGERS?", ["Remove every saved finger?"], yes, danger=True))

    app.push(MenuScreen("FINGERPRINT", [
        ("ADD A FINGER", lambda app: app.push(EnrollScreen())),
        ("QUICK UNLOCK", toggle, "ON" if on else "OFF", "cyan" if on else "grey"),
        ("DELETE ALL", delete_all, "", "red"),
    ], info=[f"{f.get('saved', 0)} finger(s) saved."]))


# ---------------------------------------------------------------- enroll
class EnrollScreen(Screen):
    """Place, lift, three times; then the sensor builds and stores a template."""
    animated = True
    PRESSES = 3

    def __init__(self):
        self.title = "ADD FINGER"
        self.step = 0
        self.lifting = False
        self.done = False
        self.error = ""

    def poll(self, app) -> bool:
        f = app.finger
        if self.done or f is None:
            return False
        if self.lifting:
            if not f.capture(enroll=True):
                self.lifting = False
                return True
            return False
        if not f.capture(enroll=True):
            return False
        if not f.features():
            self.error = "Blurry: try again"
            self.lifting = True
            return True
        self.error = ""
        self.step += 1
        self.lifting = True
        if self.step >= self.PRESSES:
            self.done = True
            slot = app.hw["finger"].get("saved", 0)
            if f.template() and f.store(slot):
                f.led(2)
                app.refresh_hardware()
                app.pop()
                app.push(MessageScreen("FINGER SAVED", [
                    f"Saved as finger #{slot + 1}.", "Turn on QUICK UNLOCK to",
                    "reopen the screen with it."]))
            else:
                f.led(4)
                app.pop()
                app.push(MessageScreen("DIDN'T TAKE", ["The presses didn't match.",
                                                       "Try again, same finger."], border="red"))
        return True

    def handle(self, ev, app):
        if ev.kind in (Kind.BACK, Kind.HOME):
            app.pop()

    def render(self, app, frame):
        img, d = t.new_canvas()
        t.sky(img, d, hills=False)
        app.status_bar(img, d, self)
        t.window(d, (40, 22, W - 41, H - 10), self.title)
        t.sprite(img, t.FINGER, (W // 2 - 16, 36),
                 {"w": t.PAL["cyan"] if not self.lifting else t.PAL["grey"], "k": t.PAL["ink"]},
                 scale=4)
        msg = "LIFT FINGER" if self.lifting else f"PRESS {self.step + 1}/{self.PRESSES}"
        t.text(d, (W // 2, 74), msg, BIG if len(msg) <= 12 else BODY, "white", align="center")
        t.bar(d, (70, 100, W - 71, 108), self.step / self.PRESSES, "cyan")
        if self.error:
            t.text(d, (W // 2, 116), self.error, TINY, "red", align="center")
        t.text(d, (W // 2, 132), "HOME CANCEL", TINY, "grey", align="center")
        return img


# ---------------------------------------------------------- finger unlock
class FingerWatcher:
    """Polls the sensor in the background while the soft-lock screen shows."""

    def __init__(self, sensor):
        self.sensor = sensor
        self.matched = False
        self._stop = threading.Event()

    def check(self) -> bool:
        try:
            if self.sensor.verify_once() is not None:
                self.matched = True
        except Exception:  # noqa: BLE001 - sensor glitches just mean "no match"
            pass
        return self.matched

    def start(self):
        def loop():
            while not self._stop.is_set() and not self.matched:
                self.check()
                time.sleep(0.25)
        threading.Thread(target=loop, daemon=True).start()

    def stop(self):
        self._stop.set()


def attach_finger(scr, app):
    """Give the LOCKED screen a fingerprint path (soft lock only: the
    signer is still unlocked, only the screen was closed)."""
    watcher = FingerWatcher(app.finger)
    scr.prompt = "FINGER, OR OK FOR PIN"
    original_render = scr.render

    def on_show(app):
        if not app.sync_jobs:
            watcher.start()

    def on_hide(app):
        watcher.stop()

    def poll(app):
        if app.sync_jobs:
            watcher.check()
        if not watcher.matched:
            return False
        watcher.matched = False
        if app.signer.touch():
            app.home()
        else:
            scr.error = "Session ended: PIN needed"
        return True

    def render(app, frame):
        img = original_render(app, frame)
        t.sprite(img, t.FINGER, (52, 32), {"w": t.PAL["cyan"]}, scale=2)
        return img

    scr.on_show, scr.on_hide, scr.poll, scr.render = on_show, on_hide, poll, render
    return scr
