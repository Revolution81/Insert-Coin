"""
SETUP › FILES › USB DISK: the wallet's public files as a read-only USB
stick on a PC (core/usbdisk.py). On while its screen is up, at most 10
minutes; OK, DEL or HOME takes the stick away.
"""
from __future__ import annotations

import time

from core import usbdisk

from . import theme as t
from .input import Kind
from .theme import BODY, H, TINY, W
from .widgets import ConfirmScreen, MessageScreen, Screen, paginate

LIMIT_S = 600


def usb_disk(app):
    def go(app):
        def work():
            files = usbdisk.prepare(app)
            usbdisk.start()
            return files

        def started(app, files):
            app.push(UsbDiskScreen(files))

        def failed(app, e):
            usbdisk.stop()
            if str(e) == "no-udc":
                app.push(MessageScreen("USB DISK", [
                    "The device can't be a USB", "stick right now.", "",
                    "1. Flip the USB switch on the", "left side to SLAVE.",
                    "2. Plug the right USB-C into", "your PC.", "",
                    "First time only: install with", "--usb-disk (or add",
                    "dtoverlay=dwc2,dr_mode=otg", "to config.txt on the card),", "then restart.", "",
                    "On SLAVE, the LAN port and", "USB-A are off."], border="orange"))
            else:
                app.push(MessageScreen("USB DISK", ["Couldn't start:", str(e)[:60]], border="red"))
        app.job("PREPARING...", work, started, failed)

    app.push(ConfirmScreen("USB DISK?", [
        "Your PC sees a read-only stick:", "history (CSV), PSBTs, signed",
        "transactions, labels and the", "watch-only descriptor. No keys,",
        "but it shows your history."], go, yes="SHOW IT"))


class UsbDiskScreen(Screen):
    """The stick is attached while this is up."""
    keep_awake = True

    def __init__(self, files: list[tuple[str, int]]):
        self.title = "USB DISK"
        self.files = files
        self.until = time.monotonic() + LIMIT_S
        self.page = 0

    def _end(self, app, why: str):
        try:
            usbdisk.stop()
        except Exception:  # noqa: BLE001 - shown below either way
            pass
        app.pop()
        app.last_input = time.monotonic()
        app.push(MessageScreen("USB DISK OFF", [why, "", "Eject it on the PC first next", "time, if it asks."]))

    def handle(self, ev, app):
        if ev.kind is Kind.NAV and ev.value in ("UP", "DOWN", "LEFT", "RIGHT"):
            step = -1 if ev.value in ("UP", "LEFT") else 1
            self.page = max(0, min(self.page + step, getattr(self, "_pages", 1) - 1))
        elif ev.kind in (Kind.SELECT, Kind.BACK, Kind.HOME):
            self._end(app, "The stick is gone from the PC.")

    def render(self, app, frame):
        left = int(self.until - time.monotonic())
        if left <= 0:
            self._end(app, "Time's up (10 minutes).")
            return t.new_canvas()[0]
        if frame % 40 == 0:
            try:
                app.signer.touch()                  # AUTO LOCK waits while it's on
            except Exception:  # noqa: BLE001
                pass
        img, d = t.new_canvas()
        t.sky(img, d)
        app.status_bar(img, d, self)
        t.window(d, (6, 22, W - 7, H - 8), self.title, "cyan")
        m, s = divmod(left, 60)
        t.text(d, (16, 36), f"ON: {len(self.files)} files, read only", BODY, "green")
        t.text(d, (W - 18, 36), f"{m}:{s:02d}", BODY, "gold", align="right")
        names = [n for n, _ in self.files]
        pages = paginate(names, W - 40, 7)
        self._pages = len(pages)
        self.page = min(self.page, len(pages) - 1)
        for i, n in enumerate(pages[self.page]):
            t.text(d, (16, 52 + i * 12), n, BODY, "light")
        hint = "OK REMOVE" + (f"   < > {self.page + 1}/{len(pages)}" if len(pages) > 1 else "")
        t.text(d, (16, H - 22), hint, TINY, "grey")
        return img
