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


def found_chip(app, m5: bool) -> dict | None:
    """M5Stack's chip (Unit ID, a Trust&GO at 0x35) or any other (a blank
    ATECC608 on the GPIO header), from the last scan."""
    for c in app.hw["chip"].get("found") or []:
        if bool(c.get("m5")) == m5:
            return c
    return None


# The word says where (GROVE side, GPIO top); its colour says the state:
# white found, gold the seed needs it, blue a blank chip or quick unlock on.
def place(where: str, color: str = "light") -> tuple[str, str]:
    return where, color


def _chip_row(app, m5: bool):
    c = found_chip(app, m5)
    main = app.hw["chip"]
    if c is None:
        if main.get("in_use") and m5:                   # the seed needs it (always a Trust&GO)
            return "MISSING", "red"
        return "NONE", "grey"
    if main.get("in_use") and c.get("serial") == main.get("serial"):
        return place(c.get("where", "?"), "gold")
    return place(c.get("where", "?"), "cyan" if c.get("state") == "blank" else "light")


def hardware_menu(app):
    fs = finger_state(app)
    fl, fc = ("NONE", "grey") if fs == "absent" else place("GROVE", "cyan" if fs == "on" else "light")

    def rescan(app):
        app.job("SCANNING...", app.refresh_hardware, lambda app, _: (app.pop(), hardware_menu(app)))

    app.push(MenuScreen("HARDWARE", [
        ("M5 SECURE CHIP", lambda app: chip_menu(app, m5=True), *_chip_row(app, True)),
        ("SECURE CHIP", lambda app: chip_menu(app, m5=False), *_chip_row(app, False)),
        ("FINGERPRINT", finger_menu, fl, fc),
        ("NFC", _nfc_info, *nfc_label(app)),
        ("RESCAN", rescan),
    ], info=["GROVE = side, GPIO = top.", "Swap units, then RESCAN."]))


def nfc_label(app) -> tuple[str, str]:
    """Where the NFC reader is (Cap or Unit, it's all NFC): GPIO or GROVE."""
    st = app.hw.get("nfc", {})
    if st.get("state") != "ready":
        return "NONE", "grey"
    return place(st.get("where") or ("GROVE" if st.get("reader") == "unit" else "GPIO"))


def _nfc_info(app):
    from .nfc_flow import nfc_info
    nfc_info(app)


def _reopen(app):
    app.home()
    from .flow import settings_screen
    settings_screen(app, at="HARDWARE")
    hardware_menu(app)


def chip_menu(app, m5: bool = True):
    main = app.hw["chip"]
    c = found_chip(app, m5)
    if c is None:
        lines = (["No M5 chip found. Plug the", "Unit ID into the side",
                  "(Grove) port, then RESCAN."] if m5 else
                 ["No chip found on the GPIO", "header (pins 3 SDA, 1 SCL,", "power pin 6, GND pin 4).",
                  "Not with the Cap fitted:", "they share those pins."])
        if m5 and main.get("in_use") and not found_chip(app, True):
            lines = ["Your seed needs this chip,", "but it isn't connected.", ""] + lines
        app.push(MessageScreen("M5 SECURE CHIP" if m5 else "SECURE CHIP", lines,
                               border="red" if main.get("in_use") and m5 else "light"))
        return
    c = dict(c)
    if main.get("in_use") and c.get("serial") == main.get("serial"):
        c["in_use"] = True
    state = ("in_use" if c.get("in_use") else c.get("state", "absent"))
    info = [f"{c.get('kind', 'Secure chip')} at 0x{c.get('address', 0):02x}",
            f"On the {'side (GROVE)' if c.get('where') == 'GROVE' else 'top (GPIO)'} port."]
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
    app.push(MenuScreen("M5 SECURE CHIP" if m5 else "SECURE CHIP",
                        items or [("BACK", lambda app: app.pop())], info=info))


def finger_allowed(app) -> bool:
    """ADD A FINGER / QUICK UNLOCK are ADVANCED tools (a convenience that
    keeps the seed unlocked for hours, and the sensor takes the side port
    the M5 chip could use), unless already in use: saved fingers or quick
    unlock on stay reachable, so they can be turned off and deleted."""
    f = app.hw.get("finger", {})
    return bool(app.settings.advanced or app.settings.finger_unlock or f.get("saved"))


def finger_menu(app):
    f = app.hw["finger"]
    if f.get("state") != "ready":
        app.push(MessageScreen("FINGERPRINT", ["No sensor found. Plug the",
                                               "Unit Fingerprint2 into the",
                                               "side (Grove) port, then", "RESCAN.", ""]
                               + list(f.get("errors") or [])))
        return
    on = app.settings.finger_unlock
    if not finger_allowed(app):
        app.push(MessageScreen("FINGERPRINT", [
            "Sensor found. Fingers and", "QUICK UNLOCK are power-user",
            "tools: turn on SETUP ›", "DEVICE › ADVANCED to use", "them.", "",
            "Quick unlock keeps your seed", "unlocked for hours; a finger",
            "only reopens the screen."]))
        return

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
            ok = app.finger.delete_all()
            left = app.finger.settled_count()
            app.hw["finger"]["saved"] = left if left is not None else (0 if ok else
                                                                         app.hw["finger"].get("saved", 0))
            app.settings.finger_unlock = False
            app.settings.save()
            if not ok or left:
                app.push(MessageScreen("NOT DELETED", ["The sensor didn't clear.",
                                                       f"{left if left is not None else '?'} finger(s) still saved.",
                                                       "Try DELETE ALL again."], border="red",
                                       then=lambda app: _reopen(app)))
                return
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
            before = f.settled_count()
            slot = before if before is not None else app.hw["finger"].get("saved", 0)
            if f.template() and f.store(slot):
                f.led(2)
                after = f.settled_count()
                app.hw["finger"]["saved"] = after if after is not None else slot + 1
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
