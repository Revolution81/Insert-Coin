"""SETUP > UPDATE: install a signed release from microSD or USB."""
from __future__ import annotations

import os

from core import update

from .widgets import ConfirmScreen, MenuScreen, MessageScreen


def update_screen(app):
    def scan():
        keys = update.load_keys()
        out = []
        for tar, sig in update.find():
            try:
                out.append((tar, update.verify(tar, sig, keys), None))
            except update.UpdateError as e:
                out.append((tar, None, str(e)))
        return keys, out

    def show(app, result):
        keys, found = result
        if not keys:
            app.push(MessageScreen("NO RELEASE KEY", [
                "No trusted release key is set", "up, so no update can be",
                "checked. Add one to", "/etc/czwallet/release_keys.txt"], border="red"))
            return
        if not found:
            app.push(MessageScreen("NO UPDATE FOUND", [
                "Put czwallet-X.Y.Z.tar.gz and", "its .sig file in a folder",
                "named czwallet-update on the", "microSD boot partition, or", "on a USB stick."]))
            return
        items = []
        for tar, info, err in found:
            name = os.path.basename(tar)[len("czwallet-"):-len(".tar.gz")]
            if info:
                items.append((f"VERSION {name}", _confirm(tar, info), "SIGNED", "green"))
            else:
                items.append((f"VERSION {name}", lambda app, e=err: app.push(
                    MessageScreen("REFUSED", [e], border="red")), "REFUSED", "red"))
        app.push(MenuScreen("UPDATES", items, info=[f"Installed: {update.VERSION}"]))

    app.job("LOOKING...", scan, show)


def _confirm(tar, info):
    def go(app):
        def yes(app):
            app.job("INSTALLING...", lambda: update.run_helper(tar),
                    lambda app, msg: app.push(MessageScreen("UPDATED", [
                        f"Now on {info['version']}.", "Restarting in a moment.",
                        "Your seed and wallets are", "untouched."], big="LEVEL UP")),
                    lambda app, err: app.push(MessageScreen("NOT INSTALLED", [str(err)[:120]],
                                                            border="red")))
        app.push(ConfirmScreen(f"UPDATE TO {info['version']}?", [
            f"From {update.VERSION}.", f"Signed by key {info['key']}",
            f"SHA256 {info['sha256'][:16]}", f"       {info['sha256'][16:32]}...",
            "Check the hash matches the", "published release."], yes))
    return go
