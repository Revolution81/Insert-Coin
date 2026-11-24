"""
SETUP › TOOLS › ADDRESS EXPLORER: every address of the open wallet.

RECEIVE or CHANGE, then a list: index, the start and end of the address,
and USED / its balance. OK opens one: its QR, the whole address in groups,
L to label it, V to have Sir Signer derive it himself and show it on his
screen (a mismatch means something on the device is wrong). MORE ADDRESSES
at the bottom lists 20 further.

Addresses come from the wallet's public keys only; the signer is asked
only for V.
"""
from __future__ import annotations

from . import theme as t
from .input import Kind
from .theme import BODY, TINY, H, W, fmt_sats
from .widgets import MenuScreen, MessageScreen, Screen, TextInputScreen, groups


def short(addr: str) -> str:
    return f"{addr[:8]}..{addr[-4:]}"


def explorer_menu(app):
    if app.wallet is None:
        app.push(MessageScreen("NO WALLET", ["Open a wallet first."]))
        return
    app.push(MenuScreen("ADDRESS EXPLORER", [
        ("RECEIVE ADDRESSES", lambda app: address_list(app, 0)),
        ("CHANGE ADDRESSES", lambda app: address_list(app, 1)),
    ], info=[f"Wallet: {app.wallet_name[:20]}", "OK on an address: QR, label,",
             "V to check it with Sir Signer."]))


def address_list(app, branch: int):
    extra = {"n": 0}

    def items():
        rows = app.wallet.addresses(branch)
        if extra["n"]:
            rows += app.wallet.addresses(branch, start=len(rows), count=extra["n"])
        out = []
        for r in rows:
            if r["sats"]:
                right, color = fmt_sats(r["sats"]), "gold"
            elif r["used"]:
                right, color = "USED", "grey"
            else:
                right, color = "", "grey"
            if r["label"] and not r["sats"]:
                right, color = r["label"][:10], "cyan"
            out.append((f"#{r['index']} {short(r['address'])}",
                        (lambda rr: lambda app: app.push(
                            AddressDetailScreen(branch, rr["index"], rr["address"])))(r),
                        right, color))
        out.append(("MORE ADDRESSES", more, "+20", "grey"))
        return out

    def more(app):
        extra["n"] += 20
        menu.items = items()

    menu = MenuScreen("RECEIVE" if branch == 0 else "CHANGE", items)
    app.push(menu)


class AddressDetailScreen(Screen):
    """One address: QR, full text, label, and V to verify on the signer."""

    def __init__(self, branch: int, index: int, address: str):
        self.branch, self.index, self.addr = branch, index, address

    def handle(self, ev, app):
        if ev.kind is Kind.HOTKEY and ev.value == "V":
            from .flow import verify_address
            verify_address(app, self.index, self.addr, change=self.branch == 1)
        elif ev.kind is Kind.HOTKEY and ev.value == "L":
            def ok(app, text):
                app.wallet.set_label("addr", self.addr, text)
                app.pop()
                return None
            app.push(TextInputScreen("LABEL", "Label for this address:", ok, max_len=60,
                                     initial=app.wallet.label("addr", self.addr), empty_ok=True))
        else:
            super().handle(ev, app)

    def render(self, app, frame):
        img, d = t.new_canvas()
        t.sky(img, d, hills=False)
        app.status_bar(img, d, self)
        size = t.qr(img, self.addr.upper(), (8, 18), module=3, border=3)
        title = f"{'CHANGE' if self.branch else 'ADDR'} #{self.index}"
        t.window(d, (size + 16, 22, W - 5, H - 8), title)
        x = size + 28
        gs = groups(self.addr)
        per = 3 if len(gs) <= 15 else 4
        step = 40 if per == 3 else 36
        for n, g in enumerate(gs):
            line, col = divmod(n, per)
            t.text(d, (x + col * step, 38 + line * 14), g, BODY,
                   "gold" if n % 2 == 0 else "light")
        label = app.wallet.label("addr", self.addr)
        if label:
            t.text(d, (x, 98), label[:22], BODY, "cyan")
        t.text(d, (x, 112), "Match first + last groups", TINY, "grey")
        t.text(d, (x, 126), "V VERIFY   L LABEL", TINY, "light")
        return img
