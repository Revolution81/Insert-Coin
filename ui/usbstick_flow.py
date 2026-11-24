"""
SETUP › FILES › SAVE TO USB STICK (core/usbstick.py): plug a USB stick
into the USB-A port, OK, and the wallet's files land in InsertCoin/ on it.
"""
from __future__ import annotations

from core import usbstick

from .widgets import ConfirmScreen, MessageScreen

PROBLEMS = {
    "no-stick": ["No USB stick found.", "", "Plug one into the USB-A port",
                 "(the USB switch on HOST, as", "usual) and try again."],
    "no-fs": ["That stick isn't FAT or exFAT.", "", "Format it as FAT32 or exFAT",
              "on a PC (most sticks come", "that way) and try again."],
}


def save_to_usb_stick(app):
    def go(app):
        def work():
            files = usbstick.prepare(app)
            try:
                return usbstick.save(), files
            finally:
                usbstick.clear()

        def saved(app, r):
            where, files = r
            app.push(MessageScreen("SAVED", [f"{len(files)} files saved to", f"{where[:28]},",
                                             "folder InsertCoin.", "",
                                             "It's safe to unplug the stick."]))

        def failed(app, e):
            app.push(MessageScreen("USB STICK", PROBLEMS.get(str(e), [
                "Couldn't save to the stick:", str(e)[:60]]), border="orange"))
        app.job("SAVING...", work, saved, failed)

    app.push(ConfirmScreen("SAVE TO USB STICK?", [
        "History (CSV), PSBTs, signed", "transactions, labels and the",
        "watch-only descriptor go on", "the stick. No keys, but it", "shows your history."],
        go, yes="SAVE"))
