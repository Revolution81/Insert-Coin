"""
The wallet's screens and how they connect: setup, unlock, home, receive,
send, history, coins, modes, Wi-Fi and settings.

Anything that needs your PIN, your seed words or your YES to a payment is
asked for by the signer on its own screen (app.prompt -> core/trusted.py,
ui/secure_flows.py). These screens only start those prompts and use what
comes back.
"""
from __future__ import annotations

import base64
import math
import os
import time

from core import seed as seedlib
from core.config import NETWORKS
from core.qr import bbqr_encode

from . import theme as t
from .input import Kind
from .theme import BIG, BODY, H, PAL, TINY, W
from .hardware_flow import hardware_menu
from .multisig_flow import finish_pending, wallets_menu
from .secure_flows import ConfirmSendScreen  # noqa: F401 - drawn by the signer; tests import it here
from .split_flow import split_backup
from .update_flow import update_screen
from .qrscreens import AnimatedQRScreen, ScanScreen
from .widgets import (ConfirmScreen, MenuScreen, MessageScreen, Screen, TextInputScreen,
                      fmt_btc, fmt_sats, groups)

from core.version import VERSION  # noqa: E402


# ============================================================== setup
def welcome(app):
    def cycle_net(app, step=1):
        names = list(NETWORKS)
        if os.environ.get("CZ_SIM") and not os.environ.get("CZ_SIM_ALLOW_MAINNET"):
            names.remove("bitcoin")          # the simulator is a PC: test coins only
        cur = names.index(app.settings.network) if app.settings.network in names else -step
        app.settings.network = names[(cur + step) % len(names)]
        app.settings.save()

    def items():
        net = app.settings.network.upper()
        return [
            ("CREATE NEW WALLET", create_flow),
            ("RESTORE FROM WORDS", restore_flow),
            ("NETWORK", None, net, "gold" if net == "BITCOIN" else "cyan", cycle_net),
        ]
    return MenuScreen("WELCOME", items,
                      info=["Your keys. Your coins.", "Pick an option to begin."], back=False)


def _ready(app, info):
    app.open_wallet(info)
    app.push(MessageScreen("READY!", ["Your wallet is set up.", "",
                                      f"Fingerprint {info['fingerprint']}",
                                      "Starting in VAULT mode."], big="LEVEL UP",
                           then=_offer_backup))


def _offer_backup(app):
    """After a restore: if the card has a backup for this seed, offer it."""
    from core import backup
    try:
        files = backup.find(app.signer.backup_tag())
    except Exception:  # noqa: BLE001
        return
    if files:
        app.push(ConfirmScreen("BACKUP FOUND", [
            "This card has a backup for", "this seed (accounts, multisig,",
            "labels, settings).", "Restore it now?"],
            lambda app: restore_card_backup(app, files[0]), yes="RESTORE"))


def card_backup_menu(app):
    from core import backup

    def save(app):
        def work():
            data = backup.collect(app)
            r = app.signer.make_backup(app_data=data)
            return backup.write(backup.unb64(r["blob"]), r["tag"])

        def saved(app, name):
            app.push(MessageScreen("BACKUP SAVED", [
                "Saved " + _stamp(name), "",
                "On this card, and copied to", "czwallet-backup/ on its PC-",
                "readable side. Only your seed", "can open it."]))
        app.job("SAVING BACKUP...", work, saved)

    def restore(app):
        files = backup.find(app.signer.backup_tag())
        if not files:
            others = len(backup.find())
            app.push(MessageScreen("NO BACKUP", [
                "No backup for this seed", "on the card or USB stick." +
                (f" ({others} other)" if others else "")]))
            return
        app.push(MenuScreen("RESTORE FROM", [
            (_stamp(os.path.basename(p)),
             (lambda p: lambda app: restore_card_backup(app, p))(p))
            for p in files[:6]], info=["Newest first."]))

    app.push(MenuScreen("CARD BACKUP", [
        ("SAVE BACKUP", save), ("RESTORE BACKUP", restore)],
        info=["Accounts, multisig wallets,", "rules, labels and settings.",
              "Not the seed: keep your words."]))


def _stamp(name: str) -> str:
    """'czw-<tag>-20261113-131731.bak' -> '2026-11-13 13:17'."""
    d, t = name[-19:-11], name[-10:-4]
    return f"{d[:4]}-{d[4:6]}-{d[6:]} {t[:2]}:{t[2:4]}"


def restore_card_backup(app, path):
    from core import backup
    try:
        blob = backup.b64(backup.read(path))
    except (OSError, backup.BackupError) as e:
        app.push(MessageScreen("CAN'T READ", [str(e)[:60]], border="red"))
        return

    def done(app, r):
        backup.apply(app, r["app"])
        app.home()
        res = r["result"]
        n_a, n_r = len(app.settings.accounts), len(res["rules"])
        app.push(MessageScreen("RESTORED", [
            f"{n_a} extra account{'s' if n_a != 1 else ''}",
            f"{res['multisig']} multisig/inheritance wallet{'s' if res['multisig'] != 1 else ''}",
            f"{n_r} vault-only rule{'s' if n_r != 1 else ''}",
            "Labels and settings",
            *([f"({res['skipped']} wallet(s) skipped)"] if res["skipped"] else []),
            "", "Set your spend cap again", "if you used one."], big="LOADED"))
    app.prompt("restore_backup_prompt", done, blob=blob)


def create_flow(app):
    """New words are made, shown and checked on the signer's screen. First
    a few camera frames are hashed for extra randomness (czscan does the
    camera; the signer mixes it in with its own sources)."""
    app.job("GATHERING RANDOMNESS...", app.camera_noise,
            lambda app, noise: app.prompt("create_prompt", _ready,
                                          network=app.settings.network, camera=noise.hex()))


def restore_flow(app):
    def words(n):
        return lambda app: app.prompt("restore_prompt", _ready, network=app.settings.network,
                                      words=n)

    def scan(app):
        def got(app, res):
            try:
                m = seedlib.seedqr_decode(res.text)
            except ValueError as e:
                app.push(MessageScreen("NOT A SEEDQR", [str(e)], border="red"))
                return
            app.pop()                         # the RESTORE menu
            app.prompt("import_prompt", _ready, network=app.settings.network, mnemonic=m)
        app.push(ScanScreen("SCAN SEEDQR", got, accept=("seedqr",), hint="POINT AT A SEEDQR"))

    app.push(MenuScreen("RESTORE", [
        ("12 WORDS", words(12)), ("24 WORDS", words(24)), ("SCAN SEEDQR", scan),
        ("SPLIT SHARES", lambda app: app.prompt("shares_prompt", _ready,
                                                network=app.settings.network))],
        info=["Type 4 letters of each", "word, then SPACE."]))


# ============================================================== unlock
class LockedScreen(Screen):
    """Shown while the signer is locked (or the screen is): OK asks the
    signer for your PIN on its own screen."""
    title = "LOCKED"

    def __init__(self):
        self.prompt = "OK TO UNLOCK"
        self.error = ""

    def handle(self, ev, app):
        if ev.kind is Kind.SELECT and not app.prompting:
            unlock(app)

    def render(self, app, frame):
        img, d = t.new_canvas()
        t.sky(img, d)
        app.status_bar(img, d, self)
        t.window(d, (40, 22, W - 41, 128), "LOCKED")
        t.sprite(img, t.LOCK, (W // 2 - 8, 40), {"k": PAL["purple_dk"], "w": PAL["white"],
                                                  "y": PAL["purple"]}, scale=2)
        t.text(d, (W // 2, 66), self.prompt, BODY, "white", align="center")
        t.text(d, (W // 2, 84), "The PIN is typed on the", TINY, "grey", align="center")
        t.text(d, (W // 2, 96), "signer's screen: gold shield", TINY, "grey", align="center")
        t.text(d, (W // 2, 108), "top left, your security words.", TINY, "grey", align="center")
        if self.error:
            t.text(d, (W // 2, 140), self.error[:40], TINY, "red", align="center")
        return img


def locked_screen(app, soft: bool = False):
    scr = LockedScreen()
    if soft and app.signer.status().get("unlocked"):
        from .hardware_flow import attach_finger
        attach_finger(scr, app)
    return scr


def unlock(app):
    app.prompt("unlock_prompt", lambda app, info: app.open_wallet(info))


# ================================================================ home
class HomeScreen(Screen):
    ITEMS = ["SEND", "RECEIVE", "COINS", "HISTORY", "MODE", "SETUP"]

    def __init__(self):
        self.sel = 0

    def handle(self, ev, app):
        if ev.kind is Kind.NAV:
            step = {"LEFT": -1, "RIGHT": 1, "UP": -3, "DOWN": 3}.get(ev.value, 0)
            self.sel = (self.sel + step) % 6
        elif ev.kind is Kind.SELECT:
            self.open(app, self.ITEMS[self.sel])
        elif ev.kind is Kind.HOTKEY:
            if ev.value == "L":
                app.lock()
                return
            if ev.value == "Q":
                scan_anything(app)
                return
            if ev.value == "W":
                wallets_menu(app)
                return
            if ev.value == "T":
                from .nfc_flow import tap_card
                tap_card(app)
                return
            for i, item in enumerate(self.ITEMS):
                if item.startswith(ev.value):
                    self.sel = i
                    self.open(app, item)
                    break

    def open(self, app, item):
        {"SEND": send_flow, "RECEIVE": lambda a: a.push(ReceiveScreen()),
         "COINS": coins_screen, "HISTORY": history_screen,
         "MODE": lambda a: a.push(ModeScreen()), "SETUP": settings_screen}[item](app)

    def render(self, app, frame):
        img, d = t.new_canvas()
        t.sky(img, d)
        app.status_bar(img, d, self)
        b = app.balance or {"total": 0, "pending": 0}
        total = b["total"]
        t.coin(img, (14, 22), scale=2)
        btc = fmt_btc(total)
        if app.settings.amount_style == "split":      # SETUP > APPEARANCE
            t.text(d, (56, 24), btc[:7], BIG, "gold")
            t.text(d, (56 + BIG.width(btc[:7]) + 2, 32), btc[7:], BODY, "gold_dk")
        else:
            t.text(d, (56, 24), btc, BIG, "gold")
        t.text(d, (56, 44), f"{fmt_sats(total)} sats", BODY, "light")
        status = None
        if app.wallet and app.wallet.kind == "inherit":
            from .inherit_flow import home_lines
            (sub, col), status = home_lines(app)
        elif app.wallet_id and app.wallet.kind == "single":
            kind = "TAPROOT" if app.wallet.taproot else "SEGWIT"
            name = app.wallet_name.upper()[:14]
            if app.vault_only:
                sub = f"{name}  VAULT ONLY" + ("  (SENDS NEED VAULT MODE)" if app.online else "")
            else:
                sub = f"{name}  {kind}" if kind not in name else name
                sub = f"{sub}  FP {app.fingerprint}"
            col = "gold"
        elif app.wallet_id:
            sub, col = f"{app.wallet_name.upper()}  {app.wallet.k} OF {app.wallet.n} MULTISIG", "gold"
        elif b.get("pending"):
            sub, col = f"{fmt_sats(b['pending'], True)} PENDING", "cyan"
        else:
            sub, col = f"{app.settings.network.upper()}  FP {app.fingerprint}", "grey"
        t.text(d, (56, 58), sub, TINY, col)
        t.menu(img, d, (6, 76, W - 7, 122), self.ITEMS, self.sel, cols=3, frame=frame)
        t.text(d, (W // 2, 132), app.net_line(), TINY, "grey", align="center")
        if app.wallet and app.wallet.state.get("pending_psbt"):
            t.text(d, (W // 2, 144), "TX NEEDS COSIGNER: Q TO SCAN IT BACK",
                   TINY, "gold", align="center")
        elif app.outbox_count():
            t.text(d, (W // 2, 144), f"{app.outbox_count()} SIGNED TX WAITING TO BROADCAST",
                   TINY, "gold", align="center")
        elif status:
            t.text(d, (W // 2, 144), status[0], TINY, status[1], align="center")
        t.text(d, (W // 2, 157), "Q SCAN  T TAP  W WALLETS  L LOCK", TINY, "dim", align="center")
        return img


# ============================================================= receive
class ReceiveScreen(Screen):
    def __init__(self):
        self.index, self.addr = None, ""

    def on_show(self, app):
        if not self.addr:
            self.index, self.addr = app.wallet.receive_address()

    def handle(self, ev, app):
        if ev.kind is Kind.HOTKEY and ev.value == "N":
            try:
                self.index, self.addr = app.wallet.new_receive_address()
            except Exception as e:
                app.push(MessageScreen("HOLD ON", [str(e)]))
        elif ev.kind is Kind.HOTKEY and ev.value == "L":
            def ok(app, text):
                app.wallet.set_label("addr", self.addr, text)
                app.pop()
                return None
            app.push(TextInputScreen("LABEL", "Who is paying you?", ok, max_len=60,
                                     initial=app.wallet.label("addr", self.addr), empty_ok=True))
        elif ev.kind is Kind.HOTKEY and ev.value == "V":
            verify_address(app, self.index, self.addr)
        elif ev.kind is Kind.HOTKEY and ev.value == "M":
            sign_message_flow(app, self.index)
        else:
            super().handle(ev, app)

    def render(self, app, frame):
        img, d = t.new_canvas()
        t.sky(img, d, hills=False)
        app.status_bar(img, d, self)
        size = t.qr(img, self.addr.upper(), (8, 18), module=3, border=3)
        t.window(d, (size + 16, 22, W - 5, H - 8), f"ADDR #{self.index}")
        x = size + 28
        gs = groups(self.addr)
        per = 3 if len(gs) <= 15 else 4             # Taproot/multisig: 62+ chars
        step = 40 if per == 3 else 36
        for n, g in enumerate(gs):
            line, col = divmod(n, per)
            t.text(d, (x + col * step, 38 + line * 14), g, BODY,
                   "gold" if n % 2 == 0 else "light")
        label = app.wallet.label("addr", self.addr)
        if label:
            t.text(d, (x, 98), label[:22], BODY, "cyan")
        t.text(d, (x, 112), "Match first + last groups", TINY, "grey")
        t.text(d, (x, 126), "N NEW   L LABEL", TINY, "light")
        t.text(d, (x, 138), "V VERIFY  M SIGN MSG", TINY, "light")
        return img


def verify_address(app, index, shown):
    """The signer derives the address itself and shows it on its screen;
    this app only learns whether they matched."""
    def done(app, addr):
        if addr != shown:
            app.push(MessageScreen("MISMATCH!", [
                "The signer derived a different", "address than this screen shows.",
                "Don't use it. Something on this", "device may be tampered with."],
                border="red"))
    app.prompt("address_prompt", done, wallet_id=app.wallet_id, index=index)


def _formats(app):
    """Message formats this account's addresses can sign with."""
    if app.wallet.taproot:
        return [("BIP322", "bip322", "TAPROOT", "grey")]
    return [("STANDARD", "electrum", "ELECTRUM, SPARROW", "grey"),
            ("BIP137", "bip137", "TREZOR", "grey"),
            ("BIP322", "bip322", "NEWEST", "grey")]


def sign_message_flow(app, index):
    """Sign a text message with this address's key (single-sig wallets)."""
    from core import message as msg
    if app.wallet.kind != "single":
        app.push(MessageScreen("SINGLE-SIG ONLY", ["Message signing works with",
                                                   "your single-sig wallet's",
                                                   "addresses."]))
        return

    def typed(app, text):
        try:
            msg.check_text(text)
        except msg.MessageError as e:
            return str(e)[:40]
        app.pop()

        def fmt(f):
            def go(app):
                app.pop()
                app.prompt("message_prompt", signed, index=index, message=text, fmt=f,
                           account=app.wallet_id)
            return go
        opts = _formats(app)
        if len(opts) == 1:                            # Taproot: BIP322 only
            app.prompt("message_prompt", signed, index=index, message=text, fmt=opts[0][1],
                       account=app.wallet_id)
            return None
        app.push(MenuScreen("FORMAT", [(lbl, fmt(f), r, c) for lbl, f, r, c in opts],
                            info=["If unsure, use STANDARD."]))
        return None

    def signed(app, r):
        block = msg.armored(r["address"], r["message"], r["signature"])
        note = ["Scan with the app", "that asked for it.", "TAB: other view"]
        app.push(AnimatedQRScreen("SIGNATURE", [r["signature"]], note, views=[
            ("SIGNATURE", "SIGNATURE", [r["signature"]], note),
            ("FULL", "SIGNED MSG", [block], ["Message, address and", "signature together."]),
        ]))

    app.push(TextInputScreen("SIGN MESSAGE", "Message to sign:", typed, max_len=msg.MAX_LEN,
                             tab=("SCAN", lambda app, scr: _scan_into(app, scr))))


def _scan_into(app, scr):
    """TAB on the message field: fill it from a QR instead of typing."""
    def got(app, res):
        text = "".join(ch for ch in res.text if ch >= " " or ch == "\n")[:scr.buf.max_len]
        scr.buf.text, scr.buf.pos = text, len(text)
    app.push(ScanScreen("SCAN MESSAGE", got, accept=("text", "address"),
                        hint="POINT AT THE MESSAGE QR"))


# ================================================================ send
def parse_amount(text: str, unit: str) -> int | None:
    """Exact: '0.0001' BTC -> 10000 sats. None if it isn't a valid amount."""
    from decimal import Decimal, InvalidOperation
    text = text.strip()
    if unit == "SATS":
        return int(text) if text.isdigit() else None
    if not text or text.count(".") > 1 or not text.replace(".", "").isdigit():
        return None
    if "." in text and len(text.split(".", 1)[1]) > 8:
        return None
    try:
        return int(Decimal(text) * 100_000_000)
    except InvalidOperation:
        return None


def send_flow(app, coins=None, prefill=None):
    """prefill: a Scanned address (from the camera) to skip typing."""
    avail = sum(c.sats for c in app.wallet.coins() if not coins or c.outpoint in coins) \
        if app.wallet else 0

    def got_addr(app, text):
        try:
            addr = app.wallet.validate_address(text)
        except Exception as e:
            return str(e)[:40]
        app.pop()
        ask_amount(app, addr)
        return None

    def scan(app, scr):
        def got(app, res):
            try:
                addr = app.wallet.validate_address(res.address)
            except Exception as e:
                scr.error = str(e)[:40]
                return
            app.pop()                    # the typing screen
            ask_amount(app, addr, res.sats, res.label)
        app.push(ScanScreen("SCAN ADDRESS", got, accept=("address",),
                            hint="POINT AT AN ADDRESS QR"))

    def ask_amount(app, addr, initial=None, label=""):
        units = ["SATS", "BTC", "MAX"]
        state = {"u": 0}

        def info(text):
            u = units[state["u"]]
            if u == "MAX":
                return f"Send everything ({fmt_sats(avail)} sats) minus fee"
            return f"In {u}. Available {fmt_sats(avail)} sats"

        def cycle(app, scr):
            state["u"] = (state["u"] + 1) % 3
            scr.prompt = f"Amount ({units[state['u']]}):"
            scr.empty_ok = units[state["u"]] == "MAX"

        def submit(app, text):
            u = units[state["u"]]
            if u == "MAX":
                sats = None
            else:
                sats = parse_amount(text, u)
                if sats is None:
                    return "Not a number" if u == "SATS" or "." not in text or \
                        len(text.split(".", 1)[1]) <= 8 else "BTC has only 8 decimals"
                if sats <= 0:
                    return "Must be more than zero"
                if sats > avail:
                    return "More than you have"
            app.pop()
            pick_fee(app, addr, sats, label)
            return None

        app.push(TextInputScreen("AMOUNT", "Amount (SATS):", submit, allowed="0123456789.",
                                 max_len=16, info=info, tab=("UNITS", cycle),
                                 initial=str(initial) if initial else ""))

    def pick_fee(app, addr, sats, label=""):
        est = app.fees()

        def choose(rate):
            return lambda app: (app.pop(), build(app, addr, sats, rate, label))

        def custom(app):
            def ok(app, text):
                if not text.isdigit() or not 1 <= int(text) <= 500:
                    return "1 to 500 sat/vB"
                app.pop()
                app.pop()
                build(app, addr, sats, int(text), label)
                return None
            app.push(TextInputScreen("FEE RATE", "sat/vB:", ok, allowed="0123456789", max_len=3))

        items = [(f"{name.upper()}", choose(rate), f"{rate} sat/vB", "gold")
                 for name, rate in est.items()]
        items.append(("CUSTOM", custom, "", None))
        info = ["~10 min / ~30 min / ~1 hr"] if app.wallet.connected else \
            ["Offline: rates are guesses."]
        app.push(MenuScreen("FEE", items, info=info, selected=1))

    def build(app, addr, sats, rate, label=""):
        def work():
            built = app.wallet.build(addr, sats, rate, coins)
            built["label"] = label
            return built

        app.job("BUILDING TX...", work, sign_and_send)

    if prefill is not None:
        try:
            addr = app.wallet.validate_address(prefill.address)
        except Exception as e:
            app.push(MessageScreen("CAN'T SEND", [str(e)], border="red"))
            return
        ask_amount(app, addr, prefill.sats, prefill.label)
        return
    app.push(TextInputScreen("SEND TO", "Bitcoin address:", got_addr, max_len=90,
                             info=lambda s: f"{len(s)} chars" if s else "Type it, or TAB to scan a QR",
                             tab=("SCAN", scan)))


def sign_and_send(app, built):
    """The signer shows what the transaction does and signs on YES; then
    this app finishes it and broadcasts (Online) or queues it (Vault)."""
    replaces = built.get("replaces")

    def signed_by_signer(app, r):
        app.job("SENDING..." if app.online else "SAVING...",
                lambda: work(r["psbt"], r["summary"]), done)

    def work(signed, summary):
        if app.wallet.multisig:
            have, need = app.wallet.signatures(signed)
            if have < need:                         # cosigners still to sign
                app.wallet.state["pending_psbt"] = signed
                app.wallet._save()
                return "partial", signed
        tx = app.wallet.finalize(signed)
        if built.get("label"):
            app.wallet.set_label("tx", tx.txid, built["label"])
            for o in summary["outputs"]:
                if o["kind"] == "send":
                    app.wallet.set_label("addr", o["address"], built["label"])
        if app.online and app.wallet.connected:
            return "sent", app.wallet.broadcast(tx, replaces=replaces)
        app.save_outbox(tx, replaces=replaces)
        return "saved", str(tx.compute_txid())

    def done(app, r):
        kind, txid = r
        app.home()
        app.refresh_balance()
        if kind == "partial":
            have, need = app.wallet.signatures(txid)
            app.push(AnimatedQRScreen("NEEDS SIGNATURES", psbt_frames(app, base64.b64decode(txid)),
                                      [f"Signed {have} of {need}.", "Scan this with a",
                                       "cosigner, then press", "Q here to scan its", "signature back."]))
        elif kind == "sent":
            app.push(MessageScreen("SENT!", ["Broadcast to the network.", "",
                                             "TXID", txid[:32], txid[32:]], big="QUEST DONE"))
        else:
            raw = open(os.path.join(app.outbox_dir, f"{txid}.hex")).read().strip()
            app.push(AnimatedQRScreen("SIGNED TX", bbqr_encode(bytes.fromhex(raw), "T"),
                                      ["Saved to the outbox:", "it broadcasts when you", "go Online.",
                                       "", "Or scan this with", "any wallet to send it", "from there."]))

    app.prompt("sign_prompt", signed_by_signer, on_cancel=lambda app: app.home(),
               psbt=built["psbt"])


# ======================================================= history, coins
def history_screen(app, selected=0):
    txs = app.wallet.history()
    items = []
    for tx in txs:
        label = app.wallet.tx_label(tx.txid)
        status = "PENDING" if not tx.confirmed else (
            time.strftime("%m/%d", time.localtime(tx.timestamp)) if tx.timestamp else "OK")
        items.append((fmt_sats(tx.delta, True), lambda app, tx=tx: tx_detail(app, tx),
                      label[:12] if label else status,
                      "cyan" if not tx.confirmed else ("light" if label else "grey")))
    if items:
        items.append(("SAVE ALL TO CARD", export_history, "CSV", "grey"))
    scr = MenuScreen("HISTORY", items, selected=min(selected, max(0, len(items) - 1)))
    if not items:
        scr.info = ["No transactions yet."]
    app.push(scr)


def tx_detail(app, tx):
    w = app.wallet
    tip = w.tip_height
    conf = (f"{tip - tx.height + 1} CONF" if tip else "CONFIRMED") if tx.confirmed else "PENDING"
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(tx.timestamp)) if tx.timestamp else ""
    label = w.tx_label(tx.txid)
    info = [f"{fmt_sats(tx.delta, True)} sats  {conf}",
            f"{when}" + (f"  FEE {fmt_sats(tx.fee)}" if tx.fee else ""),
            f'"{label}"' if label else "No label"]

    def reopen(app):
        app.pop()                 # this detail
        app.pop()                 # history (rebuilt with the new label)
        history_screen(app)

    def edit_label(app):
        def ok(app, text):
            w.set_label("tx", tx.txid, text)
            app.pop()
            reopen(app)
            return None
        app.push(TextInputScreen("LABEL", "Label for this transaction:", ok, max_len=60,
                                 initial=w.label("tx", tx.txid), empty_ok=True))

    items = [("LABEL", edit_label), ("SHOW TXID", lambda app: show_txid(app, tx.txid))]
    rbf = w.replaceable(tx.txid) if tx.delta < 0 else None
    if rbf:
        items.insert(0, ("CANCEL TX", lambda app: replace_flow(app, tx, rbf, cancel=True),
                         "", "red"))
        items.insert(0, ("BUMP FEE", lambda app: replace_flow(app, tx, rbf, cancel=False),
                         f"{rbf['fee_rate']:.1f}/vB", "gold"))
    app.push(MenuScreen("TRANSACTION", items, info=info))


def show_txid(app, txid: str):
    """The transaction as a QR: first a link that opens it on mempool.space
    (or your explorer) when a phone scans it, TAB for the bare TXID."""
    from core.history_export import tx_url
    url = tx_url(app.settings.network, txid, app.settings.explorer)
    words = [" ".join(groups(txid[i:i + 12])) for i in range(0, 64, 12)]
    host = app.settings.explorer.split("//")[-1].strip("/")[:20]
    views = []
    if url:
        views.append(("LINK", "TRACK IT", [url],
                      ["Scan with a phone", f"to open it on", host, "", "TAB: just the TXID"]))
    views.append(("TXID", "TXID", [txid], words + ["", "TAB: the link" if url else ""]))
    app.push(AnimatedQRScreen(views[0][1], views[0][2], views[0][3], views=views))


def share_files(app):
    from .share_flow import share_menu
    share_menu(app)


def paper_wallets(app):
    """TOOLS > PAPER WALLETS: MAKE NEW (Sir Signer makes them and writes the
    files; the root helper moves them to the device's paper folder, which
    only the SHARE FILES and PRINT services can read, not the card, not the
    app) and PRINT (to a printer on your Wi-Fi, ui/print_flow.py)."""
    def moved(app):
        if os.environ.get("CZ_SIM"):
            return "simdata/signer/paper"
        import subprocess
        helper = "/usr/local/sbin/cz-backup"
        if os.path.exists(helper):
            subprocess.run(["sudo", "-n", helper, "paper"], capture_output=True, timeout=60)
        return "this device"

    def done(app, result):
        made = (result or {}).get("made", [])
        where = moved(app)
        if not made:
            return
        lines = [f"{len(made)} paper wallet{'s' if len(made) > 1 else ''} saved to",
                 where + ":", ""]
        lines += [m["address"][:14] + "..." + m["address"][-6:] for m in made[:3]]
        if len(made) > 3:
            lines.append(f"and {len(made) - 3} more")
        lines += ["", "Download: SETUP > SHARE FILES,", "PAPER WALLETS: INCLUDE."]
        app.push(MessageScreen("PAPER WALLETS", lines))

    def make(app):
        app.prompt("paper_prompt", done, on_cancel=lambda app: moved(app))

    def print_one(app):
        from .print_flow import print_menu
        print_menu(app)

    app.push(MenuScreen("PAPER WALLETS", [
        ("MAKE NEW", make),
        ("PRINT", print_one, "WI-FI", "grey"),
    ], info=["A fresh key, not your seed.", "Small amounts only."]))


def export_history(app):
    """Every transaction of this wallet to a CSV on the microSD card."""
    from core import history_export as hx
    txs = app.wallet.history(limit=1_000_000)
    if not txs:
        app.push(MessageScreen("NO HISTORY", ["No transactions to export yet."]))
        return
    tag = f"{app.fingerprint}-{app.wallet_name}"

    def work():
        text = hx.to_csv(txs, app.wallet.tx_label, app.settings.network, app.settings.explorer)
        return hx.save(text, tag)

    def saved(app, name):
        where = ("simdata/app/exports" if os.environ.get("CZ_SIM")
                 else "InsertCoin-exports on the card")
        stamp = name.rsplit("-", 2)
        when = f"{stamp[-2][:4]}-{stamp[-2][4:6]}-{stamp[-2][6:]} {stamp[-1][:2]}:{stamp[-1][2:4]}"
        app.push(MessageScreen("SAVED", [f"{len(txs)} transactions saved to", where,
                                         f"(file dated {when}).", "",
                                         "Each row has the TXID and a",
                                         "link to track it online."]))

    app.push(ConfirmScreen("EXPORT HISTORY?", [
        f"Save all {len(txs)} transactions",
        "(dates, amounts, TXIDs, labels)",
        "as a spreadsheet file (CSV).",
        "No keys, but anyone with the",
        "card can read your history."],
        lambda app: app.job("SAVING...", work, saved)))


def replace_flow(app, tx, rbf, cancel):
    """RBF: pay a higher fee, or redirect the payment back to this wallet."""
    current = rbf["fee_rate"]
    floor = math.floor(current) + 1
    est = app.fees()

    def go(rate):
        app.job("BUILDING...", lambda: app.wallet.bump(tx.txid, rate, cancel=cancel),
                sign_and_send)

    if cancel:
        rate = max(est["fast"], floor + 1)
        app.push(ConfirmScreen("CANCEL TX?", [
            "Replace it with one that pays",
            "everything back to this wallet,",
            f"at {rate} sat/vB. Only works if",
            "it hasn't confirmed yet."], lambda app: go(rate)))
        return

    def pick(rate):
        return lambda app: (app.pop(), go(rate))

    def custom(app):
        def ok(app, text):
            if not text.isdigit() or int(text) < floor:
                return f"At least {floor} sat/vB"
            app.pop()
            app.pop()
            go(int(text))
            return None
        app.push(TextInputScreen("NEW FEE", "sat/vB:", ok, allowed="0123456789", max_len=3))

    options = [("MINIMUM", floor), ("NORMAL", max(est["normal"], floor)),
               ("FAST", max(est["fast"], floor + 1))]
    items, seen = [], set()
    for name, rate in options:
        if rate not in seen:
            seen.add(rate)
            items.append((name, pick(rate), f"{rate} sat/vB", "gold"))
    items.append(("CUSTOM", custom))
    app.push(MenuScreen("BUMP FEE", items, info=[f"Now paying {current:.1f} sat/vB."]))


class CoinsScreen(MenuScreen):
    def __init__(self, app):
        self.coins = app.wallet.coins()
        self.chosen: set[str] = set()
        super().__init__("COINS", [], info=["OK picks coins, S sends them"])
        self._rebuild()

    def _rebuild(self):
        self.items = []
        for c in self.coins:
            mark = "*" if c.outpoint in self.chosen else " "
            tag = "CHANGE" if c.change else ("PENDING" if not c.confirmed else "")
            self.items.append((f"{mark}{fmt_sats(c.sats)}", self._toggle(c.outpoint), tag,
                               "cyan" if tag == "PENDING" else "grey"))
        total = sum(c.sats for c in self.coins if c.outpoint in self.chosen)
        self.title = f"COINS {fmt_sats(total)}" if self.chosen else "COINS"

    def _toggle(self, op):
        def go(app):
            self.chosen ^= {op}
            self._rebuild()
        return go

    def handle(self, ev, app):
        if ev.kind is Kind.HOTKEY and ev.value == "S" and self.chosen:
            send_flow(app, coins=sorted(self.chosen))
        else:
            super().handle(ev, app)


def coins_screen(app):
    app.push(CoinsScreen(app))


# =============================================================== modes
class ModeScreen(Screen):
    def __init__(self):
        self.sel = None

    def handle(self, ev, app):
        if self.sel is None:
            self.sel = 1 if app.online else 0
        if ev.kind is Kind.NAV:
            self.sel ^= 1
        elif ev.kind is Kind.SELECT:
            if self.sel == 1 and not app.online:
                go_online(app)
            elif self.sel == 0 and app.online:
                app.job("RADIOS OFF...", app.go_vault,
                        lambda app, _: (app.pop(), app.push(MessageScreen(
                            "VAULT MODE", ["Wi-Fi, Bluetooth and Ethernet", "are off."]))))
            else:
                app.pop()
        else:
            super().handle(ev, app)

    def render(self, app, frame):
        if self.sel is None:
            self.sel = 1 if app.online else 0
        from .screens import choose_mode
        img = choose_mode(selected=self.sel, frame=frame)
        return img


def go_online(app):
    cap = app.signer.status()["cap"]["cap"]
    lines = ["Wi-Fi and Ethernet turn on.",
             f"Spend cap: {fmt_sats(cap)} sats/day." if cap else
             "Cap is 0: receive only. Set it in SETUP."]

    def yes(app):
        def after(app, _):
            app.pop()   # mode screen
            if not app.network_link():
                wifi_flow(app)
            else:
                app.start_sync()
        app.job("RADIOS ON...", app.go_online, after)

    app.push(ConfirmScreen("GO ONLINE?", lines, yes))


def _wifi_value(app) -> tuple[str, str]:
    """What SETUP › NETWORK shows next to WI-FI."""
    if not app.online:
        return ("OFF", "grey")
    name = app.wifi_name() if hasattr(app, "wifi_name") else None
    return ((name[:14], "cyan") if name else ("NOT JOINED", "grey"))


def wifi_flow(app):
    from core import radios

    def show(app, nets):
        def pick(n):
            def go(app):
                def connect(app, pw):
                    app.pop()
                    join(app, n["ssid"], pw)
                    return None
                if n["secure"]:
                    app.push(TextInputScreen("WI-FI", f"Password for {n['ssid'][:16]}:",
                                             connect, masked=True))
                else:
                    join(app, n["ssid"], None)
            return go
        items = [(n["ssid"][:22], pick(n), f"{n['signal']}%", "cyan") for n in nets]
        scr = MenuScreen("WI-FI", items, info=["Pick a network:"] if items else
                         ["No networks found. Ethernet", "works too: just plug it in."])
        app.push(scr)

    def join(app, ssid, pw):
        app.job("CONNECTING...", lambda: radios.wifi_connect(ssid, pw),
                lambda app, _: (getattr(app, "wifi_joined", lambda s: None)(ssid),
                                app.pop(), app.start_sync(),
                                app.push(MessageScreen("CONNECTED", [f"Joined {ssid}."]))))

    app.job("SCANNING...", radios.wifi_scan, show)


# ============================================================ settings
LOCK_CHOICES = [("2 MIN", 120), ("5 MIN", 300), ("10 MIN", 600), ("15 MIN", 900)]


def lock_label(seconds: int) -> str:
    if not seconds:
        return "OFF"
    m = seconds // 60
    return f"{m} MIN" if m < 120 else f"{m // 60} H" if m % 60 == 0 else f"{m} MIN"


SLEEP_CHOICES = [("2 MIN", 120), ("5 MIN", 300), ("10 MIN", 600), ("15 MIN", 900)]


def sleep_label(seconds: int) -> str:
    return "NEVER" if not seconds else lock_label(seconds)


def screen_sleep_menu(app):
    """SETUP > DISPLAY & SOUND > SCREEN SLEEP: backlight off after this long
    with no key pressed. The next key only wakes it. No PIN: it's a light."""
    def apply(seconds):
        def go(app):
            try:
                app.signer.set_screen_sleep(seconds=seconds)
            except Exception as e:  # noqa: BLE001
                app.push(MessageScreen("SCREEN SLEEP", [str(e)[:120]], border="red"))
                return
            del app.stack[app.stack.index(menu):]
            app._shown()
            app.push(MessageScreen("SCREEN SLEEP", [f"Screen sleep: {sleep_label(seconds)}."] + (
                ["The screen stays on."] if not seconds else
                ["The screen goes dark after", "that long with no key pressed.",
                 "Press any key to wake it."])))
        return go

    def custom(app):
        def ok(app, text):
            if not text.isdigit() or not 1 <= int(text) <= 1440:
                return "Type minutes: 1 to 1440"
            app.pop()
            apply(int(text) * 60)(app)
            return None
        app.push(TextInputScreen("CUSTOM", "Sleep after how many minutes?", ok,
                                 allowed="0123456789", max_len=4))

    cur = app.signer.status().get("screen_sleep", 120)

    def mark(sec):
        return "NOW" if sec == cur else ""
    items = [(label, apply(sec), mark(sec), "cyan") for label, sec in SLEEP_CHOICES]
    is_custom = cur and cur not in [v for _, v in SLEEP_CHOICES]
    items.append(("CUSTOM", custom, sleep_label(cur) if is_custom else "", "cyan"))
    items.append(("NEVER", apply(0), mark(0), "grey"))
    menu = MenuScreen("SCREEN SLEEP", items, info=[
        "Turn the screen off after this", "long with no key pressed.",
        "Any key wakes it."])
    app.push(menu)


def auto_lock_menu(app):
    """SETUP > AUTO LOCK: how long without a key press before the wallet
    locks and the signer forgets the seed. Shorter applies straight away;
    longer, or OFF, asks for the PIN on the signer's screen."""
    def done(app, value):
        app.settings.auto_lock_s = int(value)
        app.settings.save()
        app.last_input = time.monotonic()
        del app.stack[app.stack.index(menu):]
        app._shown()
        app.push(MessageScreen("AUTO LOCK", [f"Auto lock: {lock_label(int(value))}."] + (
            ["The wallet stays unlocked until", "you lock it or it restarts."]
            if not value else ["Locks after that long with", "no key pressed."])))

    def apply(seconds):
        def go(app):
            cur = app.settings.auto_lock_s
            if seconds and (not cur or seconds <= cur):          # shorter: no PIN needed
                try:
                    done(app, app.signer.shorten_auto_lock(seconds=seconds))
                except Exception as e:  # noqa: BLE001
                    app.push(MessageScreen("AUTO LOCK", [str(e)[:120]], border="red"))
                return
            app.prompt("auto_lock_prompt", done, seconds=seconds)
        return go

    def custom(app):
        def ok(app, text):
            if not text.isdigit() or not 1 <= int(text) <= 1440:
                return "Type minutes: 1 to 1440"
            app.pop()
            apply(int(text) * 60)(app)
            return None
        app.push(TextInputScreen("CUSTOM", "Lock after how many minutes?", ok,
                                 allowed="0123456789", max_len=4))

    cur = app.settings.auto_lock_s

    def mark(sec):
        return "NOW" if sec == cur else ""
    items = [(label, apply(sec), mark(sec), "green") for label, sec in LOCK_CHOICES]
    is_custom = cur and cur not in [v for _, v in LOCK_CHOICES]
    items.append(("CUSTOM", custom, lock_label(cur) if is_custom else "", "green"))
    items.append(("OFF", apply(0), mark(0), "red"))
    menu = MenuScreen("AUTO LOCK", items, info=[
        "Lock after this long with no key", "pressed. Longer or OFF asks", "for your PIN."])
    app.push(menu)


def sound_label(settings) -> str:
    return "ON" if settings.sound else "OFF"


SETUP_GROUPS = [
    ("SECURITY", ["CHANGE PIN", "AUTO LOCK", "LOGIN COUNTDOWN", "WIPE PIN", "DURESS PIN",
                  "SPENDING POLICY", "ONLINE CAP", "HARDWARE", "LOCK NOW"]),
    ("BACKUP", ["SHOW SEED WORDS", "SEEDQR BACKUP", "SPLIT BACKUP", "CARD BACKUP"]),
    ("NETWORK", ["WI-FI", "SERVER", "TOR", "EXPLORER"]),
    ("FILES", ["SHARE FILES", "EXPORT HISTORY", "EXPORT LABELS", "EXPORT WATCH-ONLY"]),
    ("TOOLS", ["PAPER WALLETS", "CHILD SEEDS", "WALLETS"]),
    ("DISPLAY & SOUND", ["APPEARANCE", "SCREEN SLEEP", "SOUND", "QR FORMAT"]),
]
SETUP_DIRECT = ["UPDATE", "ABOUT", "WIPE DEVICE"]     # WIPE last, away from everything else


def _sec():
    from . import security_flow
    return security_flow


def setup_group_of(label: str) -> str | None:
    return next((g for g, labels in SETUP_GROUPS if label in labels), None)


def settings_screen(app, at: str | None = None):
    """SETUP: six groups (SECURITY, BACKUP, NETWORK, FILES, TOOLS, DISPLAY &
    SOUND) plus UPDATE, ABOUT and WIPE DEVICE. at: an item to come back to,
    in a group or at the top; its group opens with the cursor on it."""
    cap = app.signer.status()["cap"]["cap"]

    def back_to(label):
        def back(app, _=None):
            app.home()
            settings_screen(app, at=label)
        return back

    def set_cap(app):
        def ok(app, text):
            app.pop()
            app.prompt("cap_prompt", back_to("ONLINE CAP"), cap_sats=int(text))
            return None
        app.push(TextInputScreen("ONLINE CAP", "Max sats per day while online:", ok,
                                 allowed="0123456789", max_len=10, initial=str(cap),
                                 info=lambda s: f"= {fmt_btc(int(s or 0))} BTC"))

    def server(app):
        def ok(app, text):
            app.settings.electrum_url = text.strip()
            app.settings.save()
            app.pop()                                   # back to NETWORK, refreshed
            return None
        app.push(TextInputScreen("ELECTRUM", "Server (empty = default):", ok, max_len=90,
                                 initial=app.settings.electrum_url, empty_ok=True,
                                 info=lambda s: "ssl://host:50002 or tcp://...onion:50001"))

    def explorer(app):
        from core.history_export import DEFAULT_EXPLORER

        def ok(app, text):
            app.settings.explorer = text.strip() or DEFAULT_EXPLORER
            app.settings.save()
            app.pop()
            return None
        app.push(TextInputScreen("EXPLORER", "Links to transactions use:", ok, max_len=90,
                                 initial=app.settings.explorer, empty_ok=True,
                                 info=lambda s: "empty = mempool.space"))

    # settings changed in place: LEFT/RIGHT (or OK) flip them, the cursor stays
    def tor(app, step):
        app.settings.use_tor = not app.settings.use_tor
        app.settings.save()

    def qr_format(app, step):
        app.settings.qr_format = "bbqr" if app.settings.qr_format == "ur" else "ur"
        app.settings.save()

    def sound_toggle(app, step):
        """One switch for all sound: the title fanfare and the menu boop.
        Saved, so it stays that way after a restart."""
        from . import sound
        app.settings.sound = not app.settings.sound
        app.settings.save()
        if app.settings.sound:
            sound.play("ui_move")                  # a boop to say it's back on
    sound_toggle.plays_sound = True            # no menu click on top of it

    def auto_lock(app):
        auto_lock_menu(app)

    def ask(method, then=None):
        return lambda app: app.prompt(method, then or (lambda app, _: None))

    def appearance(app):
        from .appearance import appearance_menu
        appearance_menu(app)

    def about(app):
        app.push(MessageScreen("ABOUT", [f"Insert Coin {VERSION}",
                                         f"Network {app.settings.network}",
                                         f"Fingerprint {app.fingerprint}",
                                         f"Server {app.settings.server[:28]}"]))

    def all_items() -> dict:
        s = app.settings
        st = app.signer.status()
        host = s.server.split("//")[-1].split(":")[0][:14]
        rows = [
            ("ONLINE CAP", set_cap, fmt_sats(st["cap"]["cap"]), "gold"),
            ("LOGIN COUNTDOWN", lambda app: _sec().countdown_menu(app),
             _sec()._mins(st.get("countdown_s", 0)), "gold" if st.get("countdown_s") else "grey"),
            ("WIPE PIN", lambda app: _sec().wipe_pin_menu(app),
             "ON" if st.get("wipe_pin") else "OFF", "red" if st.get("wipe_pin") else "grey"),
            ("SPENDING POLICY", lambda app: _sec().policy_menu(app),
             "LOCKED" if st["policy"]["locked"] else "ON" if st["policy"]["active"] else "OFF",
             "red" if st["policy"]["locked"] else "green" if st["policy"]["active"] else "grey"),
            ("SERVER", server, host, "grey"),
            ("TOR", None, "ON" if s.use_tor else "OFF", "green" if s.use_tor else "red", tor),
            ("WI-FI", lambda app: wifi_flow(app) if app.online else app.push(MessageScreen(
                "VAULT MODE", ["Go Online first (MODE)."])),
             *_wifi_value(app)),
            ("SHOW SEED WORDS", ask("words_prompt")),
            ("SEEDQR BACKUP", ask("seedqr_prompt")),
            ("SPLIT BACKUP", split_backup),
            ("CARD BACKUP", card_backup_menu),
            ("CHILD SEEDS", ask("child_seed_prompt")),
            ("DURESS PIN", ask("duress_prompt", back_to("DURESS PIN")),
             "ON" if st.get("duress") else "OFF", "gold" if st.get("duress") else "grey"),
            ("CHANGE PIN", ask("change_pin_prompt")),
            ("HARDWARE", hardware_menu),
            ("UPDATE", update_screen, VERSION, "grey"),
            ("AUTO LOCK", auto_lock, lock_label(s.auto_lock_s), "grey" if s.auto_lock_s else "red"),
            ("LOCK NOW", lambda app: app.lock(full=True)),
            ("WALLETS", wallets_menu, app.wallet_name[:12], "gold"),
            ("EXPORT WATCH-ONLY", export_watch_only),
            ("QR FORMAT", None, s.qr_format.upper(), "cyan", qr_format),
            ("APPEARANCE", appearance, "", "grey"),
            ("SCREEN SLEEP", screen_sleep_menu, sleep_label(st.get("screen_sleep", 120)),
             "cyan" if st.get("screen_sleep", 120) else "grey"),
            ("SOUND", None, sound_label(s), "green" if s.sound else "grey", sound_toggle),
            ("PAPER WALLETS", paper_wallets),
            ("SHARE FILES", share_files, "WI-FI", "grey"),
            ("EXPLORER", explorer, s.explorer.split("//")[-1].strip("/")[:14], "grey"),
            ("EXPORT HISTORY", export_history, "CSV", "grey"),
            ("EXPORT LABELS", export_labels),
            ("WIPE DEVICE", ask("wipe_prompt", lambda app, _: app.factory_reset()), "", "red"),
            ("ABOUT", about),
        ]
        return {r[0]: r for r in rows}

    def open_group(name, labels, at_item=None):
        def items():
            every = all_items()
            return [every[label] for label in labels]
        sub = MenuScreen(name, items)
        if at_item in labels:
            sub.sel = labels.index(at_item)
        app.push(sub)

    def items():
        every = all_items()
        top = [(name, (lambda n, ls: lambda app: open_group(n, ls))(name, labels), "", "light")
               for name, labels in SETUP_GROUPS]
        return top + [every[label] for label in SETUP_DIRECT]

    menu = MenuScreen("SETUP", items)
    group = setup_group_of(at) if at else None
    target = group or at
    if target:
        menu.sel = next((i for i, it in enumerate(menu.items) if it[0] == target), 0)
    app.push(menu)
    if group:
        open_group(group, dict(SETUP_GROUPS)[group], at)


# ================================================================ QR
def scan_anything(app):
    """Q on the home screen: whatever the QR is, do the sensible thing."""
    def got(app, res):
        if res.kind == "address":
            send_flow(app, prefill=res)
        elif res.kind == "psbt":
            if not finish_pending(app, res.data):
                sign_external(app, res.data)
        elif res.kind == "tx":
            broadcast_scanned(app, res.data)
        else:
            app.push(MessageScreen("SCANNED TEXT", [res.text[:200]]))
    app.push(ScanScreen("SCAN", got, hint="ADDRESS, PSBT OR SIGNED TX"))


def sign_external(app, psbt_bytes: bytes):
    """Air-gapped signing: a PSBT made by Sparrow (or any coordinator that
    imported this wallet's watch-only export) comes in by QR, goes back out
    signed as an animated BBQr."""
    b64 = base64.b64encode(psbt_bytes).decode()

    def signed(app, out_b64):
        frames = psbt_frames(app, base64.b64decode(out_b64))
        app.home()
        app.push(AnimatedQRScreen("SIGNED PSBT", frames,
                                  ["Scan this with the", "app that made the", "PSBT, then broadcast",
                                   "from there."]))

    app.prompt("sign_prompt", lambda app, r: signed(app, r["psbt"]), psbt=b64)


def psbt_frames(app, psbt: bytes) -> list[str]:
    if app.settings.qr_format == "ur":
        from core.ur import ur_encode
        return ur_encode(psbt, "crypto-psbt")
    return bbqr_encode(psbt, "P")


def broadcast_scanned(app, raw: bytes):
    from embit.transaction import Transaction
    from core.wallet import SignedTx
    try:
        txid = Transaction.parse(raw).txid().hex()
    except Exception:
        app.push(MessageScreen("NOT A TX", ["That transaction couldn't be read."], border="red"))
        return
    if not (app.online and app.wallet):
        app.push(MessageScreen("VAULT MODE", ["Go Online (MODE) to broadcast", "this transaction."]))
        return
    app.push(ConfirmScreen("BROADCAST?", ["Send this signed transaction", "to the network?",
                                          "", "TXID " + txid[:20] + "..."],
                           lambda app: app.job("BROADCASTING...",
                                               lambda: app.wallet.broadcast(SignedTx(txid, raw)),
                                               lambda app, t: app.push(MessageScreen(
                                                   "SENT!", ["TXID", t[:32], t[32:]])))))


def watch_only_texts(info: dict) -> tuple[str, str]:
    """(descriptor with receive+change, key with origin) for Sparrow & co."""
    recv = info["descriptor"]                          # wpkh([fp/84'/c'/0']xpub/0/*) or tr(...)
    desc = recv.replace("/0/*)", "/<0;1>/*)")
    key = recv[recv.index("(") + 1:recv.index("/0/*)")]   # [fp/84'/c'/0']xpub
    return desc, key


def export_watch_only(app):
    if app.wallet.kind != "single":
        app.push(MessageScreen("SINGLE-SIG ONLY", ["For a multisig, use", "WALLETS > the wallet >",
                                                   "SHOW DESCRIPTOR."]))
        return
    desc, key = watch_only_texts(app.account or app.public)
    note = ["Public keys only:", "can watch, can't spend.", "Import in Sparrow as an",
            "airgapped wallet."]
    app.push(AnimatedQRScreen("DESCRIPTOR", [desc], note, views=[
        ("DESCRIPTOR", "DESCRIPTOR", [desc], note),
        ("XPUB", "XPUB", [key], ["Account key with its", "origin, for apps that", "want an xpub."]),
    ]))


def export_labels(app):
    jsonl = app.wallet.export_labels()
    if not jsonl:
        app.push(MessageScreen("NO LABELS", ["Nothing labeled yet."]))
        return
    app.push(AnimatedQRScreen("LABELS", bbqr_encode(jsonl.encode(), "U"),
                              ["BIP329 labels.", "Scan into a wallet", "that imports BIP329",
                               f"({len(jsonl.splitlines())} labels)."]))


def duress_flow(app):
    app.prompt("duress_prompt", lambda app, _: (app.home(), settings_screen(app, at="DURESS PIN")))
