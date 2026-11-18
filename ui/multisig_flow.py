"""
Multisig screens: switch wallets, create a k-of-n wallet by scanning
cosigner keys, import a descriptor, show this device's key, and finish a
partly signed transaction by scanning a cosigner's signature back.
"""
from __future__ import annotations

from core.multisig import PolicyError, build_descriptor, parse_key

from .qrscreens import AnimatedQRScreen, ScanScreen
from .widgets import ConfirmScreen, MenuScreen, MessageScreen, TextInputScreen


def wallets_menu(app):
    entries = app.signer.list_multisig()
    current = app.wallet_id

    def open_single(app):
        app.switch_wallet(None)

    def wallet_menu(e):
        def go(app):
            def open_it(app):
                app.switch_wallet(e)

            def show(app):
                show_descriptor(app, e)

            def remove(app):
                def yes(app):
                    if app.wallet_id == e["id"]:
                        app.switch_wallet(None)
                    app.signer.remove_multisig(wallet_id=e["id"])
                    app.home()
                    app.push(MessageScreen("REMOVED", [f"{e['name']} is off this device.",
                                                       "The coins are untouched: any",
                                                       "k cosigners can still spend."]))
                app.push(ConfirmScreen("REMOVE?", [f"Forget {e['name']}?",
                                                   "Keep the descriptor backup:",
                                                   "you need it to recover."], yes, danger=True))
            items = [("OPEN", open_it), ("SHOW DESCRIPTOR", show)]
            if e.get("kind") == "inherit":
                from .inherit_flow import refresh_timer, summary_line
                if e["role"] == "owner":
                    def refresh(app):
                        if app.wallet_id != e["id"]:
                            open_it(app)
                        else:
                            app.home()
                        refresh_timer(app)
                    items.insert(1, ("REFRESH TIMER", refresh))
                info = [summary_line(e)]
            else:
                info = [f"{e['k']} of {e['n']}: " + " ".join(e["cosigners"])[:30]]
            items.append(("REMOVE", remove, "", "red"))
            app.push(MenuScreen(e["name"].upper(), items, info=info))
        return go

    def tagged(tag, now):
        return (f"{tag} · IN USE", "green") if now else (tag, "gold")

    items = [("MAIN", open_single, *tagged("SEGWIT", current is None))]
    rules = app.signer.account_rules()
    for a in app.settings.accounts:
        tag = "VAULT" if rules.get(a["id"]) == "vault" else \
            ("TAPROOT" if a["id"].startswith("tr:") else "SEGWIT")
        items.append((a["name"][:16].upper(), account_menu(a), *tagged(tag, current == a["id"])))
    for e in entries:
        tag = (e["role"].upper() if e.get("kind") == "inherit" else f"{e['k']}/{e['n']}")
        items.append((e["name"][:16], wallet_menu(e), *tagged(tag, current == e["id"])))
    from .inherit_flow import new_inheritance
    items += [("NEW ACCOUNT", new_account),
              ("NEW MULTISIG", new_multisig), ("NEW INHERITANCE", new_inheritance),
              ("IMPORT DESCRIPTOR", import_descriptor), ("MY COSIGNER KEY", my_key)]
    app.push(MenuScreen("WALLETS", items))


def account_menu(a):
    """One extra single-sig account: open it, or hide it from this list."""
    def go(app):
        from core.accounts import label

        def open_it(app):
            app.switch_wallet({"account": a["id"], "name": a["name"]})

        def hide(app):
            def yes(app):
                app.settings.accounts = [x for x in app.settings.accounts if x["id"] != a["id"]]
                app.settings.save()
                if app.wallet_id == a["id"]:
                    app.switch_wallet(None)
                app.home()
                app.push(MessageScreen("HIDDEN", [f"{a['name']} is off the list.",
                                                  "Its coins are untouched: add the",
                                                  "same account again to see them."]))
            app.push(ConfirmScreen("HIDE?", [f"Hide {a['name']}?", "The coins stay where they are,",
                                             "and your seed still has them."], yes))
        def toggle(app):
            rule = "normal" if vault else "vault"
            def done(app, rules):
                if app.wallet_id == a["id"]:
                    app.vault_only = rules.get(a["id"]) == "vault"
                app.pop()
                go(app)
            app.prompt("account_rule_prompt", done, account=a["id"], rule=rule, name=a["name"])

        vault = app.signer.account_rules().get(a["id"]) == "vault"
        kind, n = a["id"].split(":")
        app.push(MenuScreen(a["name"].upper()[:16], [
            ("OPEN", open_it),
            ("VAULT ONLY", toggle, "ON" if vault else "OFF", "gold" if vault else "grey"),
            ("HIDE", hide, "", "red")],
            info=[f"{label(a['id'])}: account {n}",
                  "m/86'/.../" + n + "'" if kind == "tr" else "m/84'/.../" + n + "'"]))
    return go


def _next_number(app, kind: str) -> int:
    from core.accounts import MAX_ACCOUNT
    used = {int(a["id"].split(":")[1]) for a in app.settings.accounts
            if a["id"].startswith(kind + ":")}
    if kind == "wpkh":
        used.add(0)                                  # the main wallet
    return next(i for i in range(MAX_ACCOUNT + 1) if i not in used)


def _add_account(app, kind: str, name: str):
    from core.accounts import make_id
    acct = make_id(kind, _next_number(app, kind))
    app.settings.accounts.append({"id": acct, "name": name})
    app.settings.save()
    app.home()
    app.switch_wallet({"account": acct, "name": name})
    return acct


def new_account(app):
    """Another single-sig wallet from the same seed: native SegWit (BIP84)
    or Taproot (BIP86), the next free account number of that type.

    SAVINGS is a Taproot account made vault-only by the signer (it won't
    sign from it while any radio is on); SPENDING is an everyday one."""
    def preset(name, vault):
        def go(app):
            if any(a["name"].lower() == name.lower() for a in app.settings.accounts):
                app.push(MessageScreen("ALREADY THERE", [f"You have a {name} account.",
                                                         "Use CUSTOM to add another."]))
                return
            if not vault:
                _add_account(app, "tr", name)
                return
            from core.accounts import make_id
            acct = make_id("tr", _next_number(app, "tr"))

            def done(app, _rules):
                _add_account(app, "tr", name)
                app.push(MessageScreen("SAVINGS READY", [
                    "This account is VAULT ONLY:", "the signer won't send from it",
                    "while any radio is on.", "",
                    "Receive here any time. To move", "coins out, switch to Vault mode."],
                    big="LOCKED"))
            app.prompt("account_rule_prompt", done, account=acct, rule="vault", name=name)
        return go

    def custom(kind):
        def go(app):
            n = _next_number(app, kind)
            default = ("Taproot" if kind == "tr" else "Segwit") + (f" {n}" if n else "")

            def named(app, name):
                _add_account(app, kind, name.strip() or default)
                return None
            app.pop()
            app.push(TextInputScreen("NAME IT", f"Account {n} name:", named, max_len=16,
                                     initial=default))
        return go

    app.push(MenuScreen("NEW ACCOUNT", [
        ("SAVINGS", preset("Savings", True), "VAULT ONLY", "gold"),
        ("SPENDING", preset("Spending", False), "EVERYDAY", "grey"),
        ("CUSTOM TAPROOT", custom("tr"), "bc1p...", "grey"),
        ("CUSTOM SEGWIT", custom("wpkh"), "bc1q...", "grey")],
        info=["A separate wallet from your", "same seed and backup. Other",
              "wallets find it too (BIP86/84)."]))


def my_key(app):
    key = app.public["multisig_key"]
    app.push(AnimatedQRScreen("COSIGNER KEY", [key], [
        "This device's key", "for multisig (BIP48).", f"Fingerprint {key[1:9]}", "",
        "Scan it into the", "wallet you set up", "in Sparrow."]))


def show_descriptor(app, e):
    from core.qr import bbqr_encode
    # One QR while it's small enough for 2-pixel modules, else animated BBQr
    frames = [e["descriptor"]] if len(e["descriptor"]) <= 250 else \
        bbqr_encode(e["descriptor"].encode(), "U")
    if e.get("kind") == "inherit":
        lines = [f"{e['name']}.", "Give a copy to the", "heir, with how to", "use it (e.g. Liana).",
                 "", "BACK IT UP: both", "people need it."]
    else:
        lines = [f"{e['name']}: {e['k']} of {e['n']}.", "Load it into Sparrow",
                 "and every cosigner.", "", "BACK IT UP: without", "it, a recovery needs",
                 "every xpub again."]
    app.push(AnimatedQRScreen("DESCRIPTOR", frames, lines))


def _confirm_and_register(app, descriptor, default_name):
    """Name it here; the signer then shows what it understood (signers,
    threshold or heir delay) on its own screen and saves it on SAVE."""
    def named(app, name):
        app.pop()
        app.prompt("register_prompt",
                   lambda app, e: (app.home(), app.switch_wallet(e), show_descriptor(app, e)),
                   descriptor=descriptor, name=name)
        return None

    def checked(app, _info):
        app.push(TextInputScreen("NAME IT", "Wallet name:", named, max_len=16,
                                 initial=default_name))
    # a quick check first, so a bad descriptor fails before you name it
    app.job("CHECKING...", lambda: app.signer.check_multisig(descriptor=descriptor), checked)


def new_multisig(app):
    def start(k, n):
        def go(app):
            keys = [app.public["multisig_key"]]

            def next_key(app):
                if len(keys) == n:
                    _confirm_and_register(app, build_descriptor(k, keys), f"{k}-of-{n}")
                    return

                def got(app, res):
                    try:
                        key = parse_key(res.text or res.address, app.settings.network)
                    except PolicyError as e:
                        app.push(MessageScreen("NOT A KEY", [str(e)], border="red",
                                               then=next_key))
                        return
                    if key[1:9] in (x[1:9] for x in keys):
                        app.push(MessageScreen("ALREADY HAVE IT", [f"{key[1:9]} is already in."],
                                               then=next_key))
                        return
                    keys.append(key)
                    app.push(MessageScreen(f"COSIGNER {len(keys)}/{n}",
                                           [f"Fingerprint {key[1:9]}", "Check it matches the",
                                            "other device."], then=next_key))
                def tap(app):
                    from .nfc_flow import get_key, tap_card

                    def on_card(app, s):
                        def have(app, key):
                            s.close()
                            got(app, type("Res", (), {"text": key, "address": ""})())
                        get_key(app, s, have)
                    tap_card(app, want="tapsigner", then=on_card)
                app.push(ScanScreen(f"KEY {len(keys) + 1} OF {n}", got, accept=("text", "address"),
                                    hint="SCAN A COSIGNER'S XPUB QR", tab=("TAP CARD", tap)))
            next_key(app)
        return go

    app.push(MenuScreen("NEW MULTISIG", [
        ("2 OF 3", start(2, 3)), ("2 OF 2", start(2, 2)), ("3 OF 5", start(3, 5))],
        info=["This device is one key.", "Scan the others next."]))


def import_descriptor(app):
    def got(app, res):
        _confirm_and_register(app, res.text, "Imported")
    app.push(ScanScreen("DESCRIPTOR", got, accept=("text",),
                        hint="SCAN THE WALLET DESCRIPTOR"))


def finish_pending(app, scanned_psbt: bytes) -> bool:
    """If this PSBT is the pending multisig one, merge its signatures and,
    once there are enough, broadcast (or queue). Returns False if unrelated."""
    import base64
    w = app.wallet
    pending = w.state.get("pending_psbt") if w else None
    if not pending:
        return False
    incoming = base64.b64encode(scanned_psbt).decode()
    try:
        merged = w.combine(pending, incoming)
    except Exception:
        return False
    have, need = w.signatures(merged)
    w.state["pending_psbt"] = merged
    w._save()
    if have < need:
        from .flow import psbt_frames
        app.push(AnimatedQRScreen("NEEDS SIGNATURES", psbt_frames(app, base64.b64decode(merged)),
                                  [f"Signed {have} of {need}.", "Scan with another",
                                   "cosigner, then Q to", "scan it back."]))
        return True

    def work():
        tx = w.finalize(merged)
        w.state.pop("pending_psbt", None)
        if app.online and w.connected:
            return "sent", w.broadcast(tx)
        app.save_outbox(tx)
        return "saved", tx.txid

    def done(app, r):
        kind, txid = r
        app.home()
        app.refresh_balance()
        app.push(MessageScreen("SENT!" if kind == "sent" else "FULLY SIGNED",
                               (["Broadcast to the network."] if kind == "sent" else
                                ["Saved to the outbox: it", "broadcasts when you go Online."])
                               + ["", "TXID", txid[:32], txid[32:]], big="QUEST DONE"))
    app.job("FINISHING...", work, done)
    return True
