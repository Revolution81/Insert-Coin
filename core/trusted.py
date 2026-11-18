"""
What the app may ask the signer for.

Two kinds of request:

SAFE     answered straight away, need no PIN, reveal no secret
         (status, public keys, reading a PSBT, locking...).

PROMPTS  the signer takes over the screen and keyboard (ui/console.py) and
         asks you itself: PIN entry, confirming a payment, showing seed
         words, changing settings that need the PIN. The app only learns
         the outcome: a signed PSBT, public keys, "cancelled".

There is no request that takes a PIN or returns seed words. A compromised
app can ask for a prompt, but you see it on the signer's screen (gold
shield in the corner, your security words on PIN screens) and it only does
what that screen says.
"""
from __future__ import annotations

import os
import secrets
import threading

from . import seed as seedlib
from .signer import Signer
from .vault import _atomic_write

SAFE = {"status", "public_info", "account_info", "account_rules", "backup_tag", "make_backup",
        "lock", "describe", "list_multisig",
        "check_multisig", "remove_multisig", "chip_status", "touch", "shorten_auto_lock",
        "policy_info", "set_screen_sleep"}

PROMPTS = {"unlock_prompt", "create_prompt", "restore_prompt", "import_prompt",
           "shares_prompt", "sign_prompt", "words_prompt", "seedqr_prompt", "split_prompt",
           "cap_prompt", "change_pin_prompt", "wipe_prompt", "duress_prompt",
           "bind_chip_prompt", "unbind_chip_prompt", "quick_unlock_prompt",
           "register_prompt", "address_prompt", "message_prompt",
           "child_seed_prompt", "account_rule_prompt", "restore_backup_prompt",
           "paper_prompt", "auto_lock_prompt", "wipe_pin_prompt", "countdown_prompt",
           "policy_prompt", "bypass_prompt", "totp_prompt"}


class Guarded:
    """The Signer behind one lock: requests arrive on several threads (the
    app's connections and the console's jobs)."""

    def __init__(self, signer: Signer):
        self._s = signer
        self._lock = threading.RLock()

    def __getattr__(self, name):
        attr = getattr(self._s, name)
        if not callable(attr):
            return attr

        def call(*a, **kw):
            with self._lock:
                return attr(*a, **kw)
        return call


def security_words(directory: str) -> tuple[str, str]:
    """Two random words for this device, made once and kept in the signer's
    folder (the app can't read it). They're not secret from someone holding
    the device, only from the app process."""
    path = os.path.join(directory, "security_words")
    try:
        with open(path) as f:
            a, b = f.read().split()[:2]
            return a, b
    except (FileNotFoundError, ValueError):
        pass
    os.makedirs(directory, exist_ok=True)
    words = seedlib.WORDLIST
    a, b = secrets.choice(words), secrets.choice(words)
    _atomic_write(path, f"{a} {b}\n".encode())
    return a, b


class TrustedSigner:
    def __init__(self, signer: Signer, console=None, sync_jobs: bool = False):
        from ui.console import Console
        self.signer = Guarded(signer)
        self.console = console or Console(self.signer, security_words(signer.dir),
                                          sync_jobs=sync_jobs,
                                          idle_s=max(60, signer.auto_lock_s))

    # ------------------------------------------------------------ safe
    def __getattr__(self, name):
        if name in SAFE:
            return getattr(self.signer, name)
        raise AttributeError(name)

    # ----------------------------------------------------------- prompts
    def _ask(self, flow, **params):
        from ui import secure_flows as f
        fn = getattr(f, flow)
        return self.console.session(lambda c: _start(c, fn, params))

    def unlock_prompt(self):
        return self._ask("unlock")

    def create_prompt(self, network: str, camera: str = ""):
        return self._ask("create", network=network, camera=camera)

    def restore_prompt(self, network: str, words: int = 12):
        return self._ask("restore", network=network, words=words)

    def import_prompt(self, network: str, mnemonic: str):
        return self._ask("import_seed", network=network, mnemonic=mnemonic)

    def shares_prompt(self, network: str):
        return self._ask("shares", network=network)

    def sign_prompt(self, psbt: str):
        return self._ask("sign", psbt=psbt)

    def words_prompt(self):
        return self._ask("show_words")

    def seedqr_prompt(self):
        return self._ask("seedqr")

    def split_prompt(self):
        return self._ask("split")

    def cap_prompt(self, cap_sats: int):
        return self._ask("set_cap", cap_sats=cap_sats)

    def change_pin_prompt(self):
        return self._ask("change_pin")

    def wipe_prompt(self):
        return self._ask("wipe")

    def duress_prompt(self):
        return self._ask("duress")

    def bind_chip_prompt(self):
        return self._ask("bind_chip")

    def unbind_chip_prompt(self):
        return self._ask("unbind_chip")

    def quick_unlock_prompt(self, hours: int):
        return self._ask("quick_unlock", hours=hours)

    def register_prompt(self, descriptor: str, name: str = ""):
        return self._ask("register", descriptor=descriptor, name=name)

    def address_prompt(self, wallet_id: str | None = None, index: int = 0):
        return self._ask("show_address", wallet_id=wallet_id, index=index)

    def account_rule_prompt(self, account: str, rule: str, name: str = ""):
        return self._ask("account_rule", account=account, rule=rule, name=name)

    def restore_backup_prompt(self, blob: str):
        return self._ask("restore_backup", blob=blob)

    def child_seed_prompt(self):
        return self._ask("child_seed")

    def paper_prompt(self):
        return self._ask("paper")

    def auto_lock_prompt(self, seconds: int):
        return self._ask("auto_lock", seconds=seconds)

    def wipe_pin_prompt(self, action: str):
        return self._ask("wipe_pin", action=action)

    def countdown_prompt(self, seconds: int):
        return self._ask("countdown", seconds=seconds)

    def policy_prompt(self, field: str, value):
        return self._ask("policy", field=field, value=value)

    def bypass_prompt(self, action: str):
        return self._ask("bypass", action=action)

    def totp_prompt(self, action: str):
        return self._ask("totp", action=action)

    def message_prompt(self, index: int, message: str, fmt: str = "electrum",
                       account: str | None = None):
        return self._ask("sign_message", index=index, message=message, fmt=fmt,
                         account=account)


def _start(c, fn, params):
    """Run a flow's first step on the console; an error there ends the prompt."""
    try:
        fn(c, **params)
    except Exception as e:  # noqa: BLE001
        c.fail(e)
