"""
TOOLS > PAPER WALLETS > PRINT: a paper wallet straight to a printer on your
home Wi-Fi (core/printing.py does the work, in its own service).

  1. pick the paper wallet (by address)
  2. pick a printer found on the network, or type its IP address
  3. a printer used for the first time: compare its fingerprint, TRUST
  4. a warning: this sends a private key to that printer
  5. sent

Online mode only: printing uses your Wi-Fi.
"""
from __future__ import annotations

from core import printing

from .widgets import ConfirmScreen, MenuScreen, MessageScreen, TextInputScreen


def _err(app, title):
    def show(app, e):
        app.push(MessageScreen(title, [str(e)[:160]], border="red"))
    return show


def print_menu(app):
    if not app.online:
        app.push(MessageScreen("VAULT MODE", ["Printing uses your Wi-Fi.",
                                              "Go Online first (MODE)."]))
        return
    ctl = printing.controller()

    def files():
        from .flow import move_paper
        move_paper()                    # any the last MAKE NEW couldn't move
        return ctl.call("files")

    def got_files(app, names):
        if not names:
            app.push(MessageScreen("PRINT", ["No paper wallets on this", "device yet:",
                                             "MAKE NEW first."]))
            return
        items = [(n[:12] + "..." + n[-6:], (lambda a: lambda app: pick_printer(app, ctl, a))(n))
                 for n in names]
        app.push(MenuScreen("PRINT WHICH?", items, info=[
            "Pick the paper wallet to print", "(by its deposit address)."]))
    app.job("LOOKING...", files, got_files, _err(app, "PRINT"))


def pick_printer(app, ctl, address):
    def found(app, printers):
        items = []
        for p in printers or []:
            if p["encrypted"]:
                items.append((p["name"][:20], (lambda p: lambda app: check(
                    app, ctl, address, p["host"], p["port"], p["path"]))(p),
                    "TRUSTED" if p.get("trusted") else "", "green" if p.get("trusted") else "grey"))
            else:
                items.append((p["name"][:20], lambda app: app.push(MessageScreen(
                    "NOT ENCRYPTED", ["This printer only takes", "unencrypted jobs. A private",
                                      "key would cross your network", "readable to anyone on it.",
                                      "Not printing."], border="red")), "NO TLS", "red"))
        items.append(("TYPE ADDRESS", lambda app: type_address(app, ctl, address)))
        items.append(("SCAN AGAIN", lambda app: (app.pop(), pick_printer(app, ctl, address))))
        info = ["Printers on your network." if printers else "No printers found.",
                "Or type the printer's IP", "address (on its settings page)."]
        app.push(MenuScreen("PRINTER", items, info=info))
    app.job("FINDING PRINTERS...", lambda: ctl.call("scan"), found, _err(app, "PRINTER"))


def type_address(app, ctl, address):
    def ok(app, text):
        host, _, port = text.strip().partition(":")
        if port and not port.isdigit():
            return "Use IP or IP:PORT"
        app.pop()
        check(app, ctl, address, host, int(port or 631), "ipp/print")
        return None
    app.push(TextInputScreen("PRINTER", "Printer's IP address:", ok, max_len=45,
                             allowed="0123456789.:abcdefABCDEF[]",
                             info=lambda s: "e.g. 192.168.1.40"))


def check(app, ctl, address, host, port, path):
    def got(app, info):
        where = f"{info['host']}:{info['port']}" if info["port"] != 631 else info["host"]
        if not info.get("format"):
            said = ", ".join(f.split("/")[-1] for f in info.get("formats", [])[:6]) or "nothing"
            app.push(MessageScreen("CAN'T PRINT", [info["model"], "takes none of the page",
                                                   "formats driverless printers",
                                                   "use (JPEG, PDF, PWG or Apple",
                                                   "raster). It listed:", said[:34],
                                                   said[34:68]], border="red"))
            return
        if info.get("changed"):
            app.push(ConfirmScreen("CERTIFICATE CHANGED", [
                f"{info['model']} at {where}",
                "has a different certificate",
                "than when you trusted it. Could",
                "be a reset, or someone in",
                "between. New fingerprint:", info["short"]],
                lambda app: trust(app, ctl, address, info), danger=True,
                yes="TRUST NEW", no="CANCEL"))
            return
        if not info.get("trusted"):
            app.push(ConfirmScreen("NEW PRINTER", [
                f"{info['model']}", f"at {where}", "Fingerprint:", info["short"],
                "Trust this printer?"],
                lambda app: trust(app, ctl, address, info), yes="TRUST", no="CANCEL"))
            return
        warn(app, ctl, address, info)
    app.job("ASKING PRINTER...", lambda: ctl.call("check", host=host, port=port, path=path),
            got, _err(app, "PRINTER"))


def trust(app, ctl, address, info):
    def done(app, _):
        warn(app, ctl, address, info)
    app.job("SAVING...", lambda: ctl.call("trust", host=info["host"], port=info["port"],
                                          fingerprint=info["fingerprint"]),
            done, _err(app, "PRINTER"))


def warn(app, ctl, address, info):
    def go(app):
        def sent(app, result):
            app.push(MessageScreen("SENT TO PRINTER", [
                f"Job {result.get('job') or ''} on {result.get('model', '')[:22]}",
                "",
                "Collect the page now. Then",
                "delete the file if you don't",
                "need it: SHARE FILES, or",
                "keep it until the coins move."]))
        app.job("PRINTING...", lambda: ctl.call(
            "print", address=address, host=info["host"], port=info["port"], path=info["path"]),
            sent, _err(app, "NOT PRINTED"))
    app.push(ConfirmScreen("PRINT PRIVATE KEY?", [
        "This sends a private key to",
        f"{info['model'][:30]}.",
        "Use a printer you trust, at",
        "home. Anyone who sees the page",
        "can take its coins."], go, danger=True, yes="PRINT", no="CANCEL"))
