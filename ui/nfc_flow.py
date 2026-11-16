"""
Tap cards over NFC (the Cap CC1101 & NFC): T on the home screen.

SATSCARD   see the current slot's address and balance, check the card is
           genuine, SWEEP its coins into the open wallet (unseals the slot:
           permanent), then SET UP NEXT SLOT.
TAPSIGNER  use it as a multisig cosigner: hand its key to NEW MULTISIG, or
           SIGN PENDING TX for a multisig that still needs a signature.

The NFC field is on only while a TAP screen or a card's menu is up.

Where the trust sits: the card talks to this app process, not the signer
(the reader is on the app's SPI bus). A SATSCARD's key is the card's, not
your seed's, so the sweep is signed here; a TAPSIGNER's signature is
checked against the key the PSBT names before it's added.
"""
from __future__ import annotations

import base64
import time

from core import cktap
from core.hw.nfc import NFCError, SimReader

from . import theme as t
from .input import Kind
from .qrscreens import AnimatedQRScreen
from .theme import BODY, H, PAL, TINY, W
from .widgets import ConfirmScreen, MenuScreen, MessageScreen, Screen, TextInputScreen, fmt_sats


class Session:
    """A card in the field, re-found if the reader lost it."""

    def __init__(self, reader, tag):
        self.reader, self.tag = reader, tag
        self.card = cktap.Card(tag.transmit)

    def ensure(self):
        try:
            self.card.refresh()
        except (NFCError, cktap.CardError, OSError):
            tag = self.reader.poll()
            if tag is None:
                raise cktap.CardError("hold the card to the reader")
            self.tag = tag
            self.card = cktap.Card(tag.transmit)
            self.card.select()

    def root(self) -> bytes:
        """The factory key to check certificates against (the simulator's own)."""
        if isinstance(self.reader, SimReader):
            return self.reader.card.root_pub
        return cktap.FACTORY_ROOT

    def close(self):
        try:
            self.reader.stop()
        except Exception:  # noqa: BLE001
            pass


class TapScreen(Screen):
    """Field on; waits for a card, then hands over a Session."""
    animated = True
    title = "TAP CARD"

    def __init__(self, on_card, want: str | None = None):
        self.on_card = on_card
        self.want = want                  # "satscard" / "tapsigner" / None
        self.error = ""
        self._last = 0.0
        self._done = False

    def on_show(self, app):
        if app.nfc is None:
            self.error = "No NFC reader. Fit the Cap, then SETUP > HARDWARE > RESCAN."

    def on_hide(self, app):
        if not self._done and app.nfc is not None:
            app.nfc.stop()

    def poll(self, app) -> bool:
        if app.nfc is None or self._done:
            return False
        now = time.monotonic()
        if now - self._last < 0.3 and not app.sync_jobs:
            return False
        self._last = now
        try:
            tag = app.nfc.poll()
        except NFCError as e:
            self.error = str(e)[:60]
            return True
        if tag is None:
            return False
        self._done = True
        session = Session(app.nfc, tag)

        def read():
            session.card.select()
            return session

        def got(app, s):
            kind = s.card.kind
            if self.want and kind != self.want:
                s.close()
                app.push(MessageScreen("WRONG CARD", [f"That's a {kind.upper()},",
                                                      f"not a {self.want.upper()}."], border="red"))
                return
            self.on_card(app, s)

        def failed(app, e):
            session.close()
            app.push(MessageScreen("CAN'T READ IT", [str(e)[:60], "", "Hold it flat on the back",
                                                     "and keep it still."], border="red"))
        app.pop()
        app.job("READING CARD...", read, got, failed)
        return True

    def handle(self, ev, app):
        if ev.kind in (Kind.BACK, Kind.HOME, Kind.SELECT):
            app.pop()

    def render(self, app, frame):
        img, d = t.new_canvas()
        t.sky(img, d)
        app.status_bar(img, d, self)
        t.window(d, (30, 24, W - 31, H - 14), "TAP CARD")
        t.sprite(img, t.NFC, (W // 2 - 16, 42), {"w": PAL["cyan"] if frame else PAL["light"]},
                 scale=4)
        if self.error:
            for i, line in enumerate(_wrap(self.error, 34)[:3]):
                t.text(d, (W // 2, 86 + i * 12), line, TINY, "red", align="center")
        else:
            what = {"satscard": "a SATSCARD", "tapsigner": "a TAPSIGNER"}.get(self.want,
                                                                           "a SATSCARD or TAPSIGNER")
            t.text(d, (W // 2, 88), f"Hold {what}", BODY, "white", align="center")
            t.text(d, (W // 2, 102), "flat on the back.", BODY, "white", align="center")
        t.text(d, (W // 2, 138), "HOME CANCEL", TINY, "grey", align="center")
        return img


def _wrap(text, n):
    from .widgets import wrap
    return wrap(text, n)


def tap_card(app, want: str | None = None, then=None):
    """T on the home screen (want=None), or a flow that needs one kind."""
    app.push(TapScreen(then or _dispatch, want))


def _dispatch(app, s: Session):
    if s.card.kind == "tapsigner":
        tapsigner_menu(app, s)
    else:
        satscard_menu(app, s)


def _ask_cvc(app, title, then):
    def ok(app, code):
        if len(code) != 6:
            return "The code is 6 digits"
        app.pop()
        then(app, code)
        return None
    app.push(TextInputScreen(title, "6-digit code on the back:", ok, masked=True,
                             allowed="0123456789", max_len=6))


def _card_error(app, e, retry=None):
    code = getattr(e, "code", 0)
    lines = [str(e)[:60]]
    if code == 401:
        lines += ["", "Check the code on the back.", "After 3 wrong tries the card",
                  "makes you wait 15 seconds."]
    elif code == 429:
        lines += ["", "Keep the card on the reader", "about 15 seconds, then retry."]
    app.push(MessageScreen("CARD SAYS NO", lines, border="red", then=retry))


# ================================================================ SATSCARD
def satscard_menu(app, s: Session):
    from embit.networks import NETWORKS as EMBIT_NETS

    def read():
        c = s.card
        cur, total = c.slot()
        info = {"slot": cur, "total": total, "addr": "", "state": c.slot_used(),
                "genuine": None, "utxos": None, "testnet": c.testnet}
        if info["state"] == "sealed":
            net = EMBIT_NETS["test" if c.testnet else "main"]
            info["addr"] = c.address(net)
        try:
            info["genuine"] = c.genuine(s.root())
        except (cktap.CardError, NFCError):
            info["genuine"] = False
        if info["addr"] and app.online and app.wallet:
            from embit.script import address_to_scriptpubkey
            try:
                info["utxos"] = app.wallet.foreign_utxos(address_to_scriptpubkey(info["addr"]))
            except Exception:  # noqa: BLE001
                info["utxos"] = None
        return info

    def show(app, info):
        mainnet_wallet = app.settings.network == "bitcoin"
        wrong_net = info["testnet"] == mainnet_wallet
        lines = [f"Slot {info['slot'] + 1} of {info['total']}: {info['state'].upper()}"]
        if info["addr"]:
            lines.append(f"{info['addr'][:14]}...{info['addr'][-8:]}")
        lines.append({True: "Genuine Coinkite card", False: "NOT VERIFIED AS GENUINE",
                      None: "?"}[info["genuine"]])
        if info["utxos"] is not None:
            lines.append(f"Balance: {fmt_sats(sum(u['value'] for u in info['utxos']))} sats")
        elif info["addr"]:
            lines.append("Go Online to see the balance")
        if wrong_net:
            lines.append("CARD IS FOR " + ("TESTNET" if info["testnet"] else "MAINNET"))
        items = []
        if info["addr"]:
            items.append(("SHOW ADDRESS", lambda app: app.push(AnimatedQRScreen(
                "SATSCARD", [info["addr"]], ["Slot address.", "Deposit here, or",
                                             "check it in a block", "explorer."]))))
            if not wrong_net:
                items.append(("SWEEP TO WALLET", lambda app: sweep(app, s, info), "", "gold"))
        if info["state"] == "unsealed" and info["slot"] + 1 < info["total"]:
            items.append(("SET UP NEXT SLOT", lambda app: next_slot(app, s)))
        items.append(("DONE", lambda app: app.pop()))
        menu = MenuScreen("SATSCARD", items, info=lines)
        menu.on_hide = lambda app: s.close()
        app.push(menu)
    app.job("READING SLOT...", read, show, lambda app, e: (s.close(), _card_error(app, e)))


def sweep(app, s: Session, info):
    """Unseal the slot and move everything in it to the open wallet."""
    if not app.online:
        app.push(MessageScreen("GO ONLINE FIRST", ["Sweeping needs the network", "to find the coins",
                                                   "and broadcast."]))
        return
    if not info["utxos"]:
        app.push(MessageScreen("NOTHING TO SWEEP", ["This slot holds no coins", "(or they're not",
                                                    "visible to your server yet)."]))
        return
    total = sum(u["value"] for u in info["utxos"])
    rate = app.fees()["normal"]

    def go(app):
        def with_code(app, cvc):
            def work():
                s.ensure()
                key = s.card.unseal(cvc)
                tx, fee, addr = app.wallet.sweep(key, info["utxos"], rate)
                try:
                    txid = app.wallet.broadcast(tx)
                    return "sent", txid, fee, addr
                except Exception:  # noqa: BLE001 - the key is out: keep the signed tx
                    app.save_outbox(tx)
                    return "saved", tx.txid, fee, addr

            def done(app, r):
                kind, txid, fee, addr = r
                app.home()
                app.refresh_balance()
                app.push(MessageScreen("SWEPT!" if kind == "sent" else "SWEEP SAVED", [
                    f"{fmt_sats(total - fee)} sats to {app.wallet_name}",
                    f"(fee {fmt_sats(fee)}).",
                    *(["Broadcast failed: it's in the", "outbox and retries Online."]
                      if kind == "saved" else []),
                    "", "TXID", txid[:32], txid[32:]], big="LOOT"))
            app.job("UNSEALING...", work, done,
                    lambda app, e: _card_error(app, e, retry=lambda app: go(app)))
        _ask_cvc(app, "UNSEAL SLOT", with_code)

    app.push(ConfirmScreen("SWEEP?", [
        f"{fmt_sats(total)} sats in slot {info['slot'] + 1},",
        f"to {app.wallet_name} (fee ~{rate} sat/vB).",
        "Unsealing is permanent: anyone",
        "holding the card afterwards can",
        "see this slot was opened."], go, danger=True, yes="SWEEP"))


def next_slot(app, s: Session):
    def with_code(app, cvc):
        def work():
            s.ensure()
            n = s.card.new_slot(cvc)
            return n, s.card.slot()[1]

        def done(app, r):
            n, total = r
            app.home()
            app.push(MessageScreen("NEXT SLOT READY", [f"Slot {n + 1} of {total} is sealed",
                                                       "and ready for deposits.", "",
                                                       "Tap again to see its address."]))
        app.job("SETTING UP...", work, done, lambda app, e: _card_error(app, e))
    _ask_cvc(app, "NEXT SLOT", with_code)


# =============================================================== TAPSIGNER
def tapsigner_menu(app, s: Session):
    c = s.card
    path = c.status.get("path")
    lines = ["Path " + ("m/" + "/".join(f"{p & 0x7FFFFFFF}{'h' if p & 0x80000000 else ''}"
                                        for p in path) if path else "not set up"),
             f"Backups made: {c.status.get('num_backups', 0)}"]
    items = [("USE AS MULTISIG KEY", lambda app: show_key(app, s))]
    if app.wallet and app.wallet.state.get("pending_psbt"):
        items.insert(0, ("SIGN PENDING TX", lambda app: sign_pending(app, s), "", "gold"))
    items.append(("DONE", lambda app: app.pop()))
    menu = MenuScreen("TAPSIGNER", items, info=lines)
    menu.on_hide = lambda app: s.close()
    app.push(menu)


def get_key(app, s: Session, then):
    """CVC, then the card's BIP48 multisig key: then(app, "[xfp/48h/...]xpub")."""
    def with_code(app, cvc):
        def work():
            s.ensure()
            return s.card.cosigner_key(cvc, app.settings.network)
        app.job("ASKING THE CARD...", work, then,
                lambda app, e: _card_error(app, e))
    _ask_cvc(app, "TAPSIGNER", with_code)


def show_key(app, s: Session):
    def got(app, key):
        app.push(AnimatedQRScreen("TAPSIGNER KEY", [key], [
            "Its multisig key (BIP48).", f"Fingerprint {key[1:9]}", "",
            "Scan into the wallet", "you're setting up."]))
    get_key(app, s, got)


def sign_pending(app, s: Session):
    from .multisig_flow import finish_pending
    pending = app.wallet.state.get("pending_psbt")

    def with_code(app, cvc):
        def work():
            s.ensure()
            return cktap.sign_psbt(s.card, cvc, pending, app.settings.network)

        def done(app, r):
            psbt, n = r
            app.pop() if isinstance(app.top, MenuScreen) else None
            finish_pending(app, base64.b64decode(psbt))
        app.job("CARD SIGNING...", work, done, lambda app, e: _card_error(app, e))
    _ask_cvc(app, "SIGN WITH CARD", with_code)


# ============================================================== hardware
def nfc_info(app):
    st = app.hw.get("nfc", {})
    sub = st.get("subghz", {})
    if st.get("state") == "ready":
        lines = [f"NFC: {st.get('kind', 'reader')}", "Ready. The field is on only",
                 "while a TAP screen is up.", ""]
    else:
        lines = ["NFC: not found", (st.get("error") or "Fit the Cap CC1101 & NFC")[:34], ""]
    lines += ["Sub-GHz radio (CC1101):",
              {"asleep": "ASLEEP, never used", "absent": "not found"}.get(sub.get("state"),
                                                                       "state unknown")]
    app.push(MessageScreen("NFC + SUB-GHZ", lines))
