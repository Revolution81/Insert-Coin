"""
The signer: the only code that ever holds private keys.

It runs as its own Linux user with no network access (see signer_service.py
and the systemd unit). It does not trust the wallet service or the UI:

- It reads every PSBT itself and builds the summary the confirm screen shows.
- It checks that each input is really its own key, and that "change" outputs
  really go back to its own change addresses (by re-deriving the key), so a
  compromised wallet service can't hide a payment as change.
- While any network interface is up, it enforces a daily spend cap that can
  only be changed with the PIN. That's what makes Online mode safer: even
  fully compromised network code can't drain more than the cap per day.
- It forgets the seed after an idle timeout.
"""
from __future__ import annotations

import datetime as dt
import glob
import hashlib
import hmac
import json
import os
import time

from embit import bip32
from embit.networks import NETWORKS as EMBIT_NETS
from embit.psbt import PSBT
from embit.script import p2tr, p2wpkh

from . import accounts as accts
from . import seed as seedlib
from . import multisig as ms
from .config import NETWORKS
from . import policy as spolicy
from .vault import Vault, VaultError, Wiped, WipePinUsed, _atomic_write

H = 0x80000000
WARN_FEE_RATE = 200
HARD_FEE_RATE = 5000         # never, whatever the policy says (it defaults to 1000)
ARGON_T_BYPASS = int(os.environ.get("CZ_ARGON_T", 3))
ARGON_M_BYPASS = int(os.environ.get("CZ_ARGON_M_KIB", 64 * 1024))
FAR_INDEX = 10_000
MAX_INPUTS = MAX_OUTPUTS = 500   # plenty for real payments, even big batches           # change/self beyond this: warn, a wallet won't look that far


CAP_DAY_S = 20 * 3600


def _boot_id() -> str:
    """Changes on every boot (Linux); one per process elsewhere."""
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()
    except OSError:
        return _PROCESS_BOOT


def _boot_clock() -> float:
    """Seconds since boot, counting suspend; not set by any clock or time server."""
    try:
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    except (AttributeError, OSError):
        return time.monotonic()


_PROCESS_BOOT = os.urandom(8).hex()


class SignerError(Exception):
    pass


class Locked(SignerError):
    pass


def network_is_up() -> bool:
    """True if the device could be online: Wi-Fi radio unblocked, or any
    non-loopback interface up. Errs on the side of "online" so the spend
    cap applies whenever in doubt. Read straight from sysfs."""
    force = os.environ.get("CZ_FORCE_ONLINE")
    if force is not None:
        return force == "1"
    for d in glob.glob("/sys/class/rfkill/rfkill*"):
        try:
            with open(f"{d}/type") as f:
                kind = f.read().strip()
            with open(f"{d}/soft") as f:
                soft = f.read().strip()
            with open(f"{d}/hard") as f:
                hard = f.read().strip()
        except OSError:
            continue
        if kind == "wlan" and soft == "0" and hard == "0":
            return True
    for p in glob.glob("/sys/class/net/*/operstate"):
        name = p.split("/")[-2]
        if name == "lo":
            continue
        try:
            with open(p) as f:
                if f.read().strip() in ("up", "unknown", "dormant"):
                    return True
        except OSError:
            continue
    return False


def estimate_vsize(n_in: int, n_out: int) -> float:
    """P2WPKH inputs and outputs (all this wallet makes)."""
    return 10.5 + 68.0 * n_in + 31.0 * n_out


SEEDVAULT_MAX = 20


class Signer:
    def __init__(self, directory: str, auto_lock_s: int = 120):
        self.dir = directory
        from .hw.atecc import open_chip
        self.vault = Vault(directory, chip_factory=open_chip)
        self.auto_lock_s = auto_lock_s
        self._root: bip32.HDKey | None = None
        self._network: str | None = None
        self._last_use = 0.0
        self._pending: dict[str, dict] = {}     # digest -> summary
        self._duress = False
        self._registry: list[ms.Policy] = []
        self._wipe_set = False
        self._login: dict | None = None         # a login waiting out its countdown
        self._main: bip32.HDKey | None = None   # the main seed's key while a temporary one is in use
        self._temp: dict | None = None          # {"secret", "passphrase", "label"} of that one
        self.cap_path = os.path.join(directory, "cap.json")
        self.policy_path = os.path.join(directory, "policy.json")

    # ---------------------------------------------------------- lifecycle
    def status(self) -> dict:
        self._expire()
        st = {"has_seed": self.vault.exists(), "unlocked": self._root is not None,
              "online": network_is_up(), "cap": self._cap_state()}
        if st["has_seed"]:
            st.update(self.vault.info())
        if st["unlocked"]:
            st["duress"] = self._duress
            st["wipe_pin"] = self._wipe_set
            if self._temp is not None:
                st["temporary"] = {"label": self._temp["label"],
                                   "fingerprint": seedlib.fingerprint(self._root)}
        st["quick_hours"] = self._quick_hours()
        st["lock_after"] = self.lock_after()
        st["screen_sleep"] = self.screen_sleep()
        st["countdown_s"] = self.countdown_s()
        st["policy"] = spolicy.public(spolicy.load(self.policy_path))
        return st

    def setup(self, pin: str, mnemonic: str, network: str, passphrase: str = "",
              store_passphrase: bool = False, max_tries: int = 10) -> dict:
        if self.vault.exists():
            raise SignerError("a seed already exists; wipe first")
        if network not in NETWORKS:
            raise SignerError("unknown network")
        if seedlib.is_xprv(mnemonic):              # an imported master key, no words
            try:
                mnemonic = seedlib.check_xprv(mnemonic, network)
            except ValueError as e:
                raise SignerError(str(e))
            passphrase, store_passphrase = "", True
        else:
            mnemonic = seedlib.normalize(mnemonic)
            if not seedlib.is_valid(mnemonic):
                raise SignerError("invalid mnemonic")
        self.vault.create(pin, mnemonic, network,
                          passphrase if store_passphrase else None, max_tries)
        return self._open(self.vault.unlock(pin), passphrase)

    def unlock(self, pin: str, passphrase: str | None = None) -> dict:
        """Check the PIN. With a login countdown set, the wallet doesn't open
        yet: {"countdown": seconds} comes back, and only finish_login (called
        on the signer's own screen once the time is up) opens it."""
        try:
            u = self.vault.unlock(pin)
        except WipePinUsed:
            self.wipe_all()
            raise
        except Wiped:
            self.wipe_all()                  # too many wrong PINs: everything goes
            raise
        wait = self.countdown_s()
        if wait:
            self.lock()
            self._login = {"u": u, "pp": passphrase, "ready": _boot_clock() + wait,
                           "boot": _boot_id()}
            return {"countdown": wait}
        return self._open(u, passphrase)

    def login_wait(self) -> int:
        """Seconds left on the login countdown (0 = ready), or -1 if none."""
        if not self._login:
            return -1
        if self._login["boot"] != _boot_id():
            self._login = None
            return -1
        return max(0, int(self._login["ready"] - _boot_clock() + 0.999))

    def finish_login(self) -> dict:
        left = self.login_wait()
        if left < 0:
            raise SignerError("no login is waiting")
        if left > 0:
            raise SignerError(f"countdown: {left} s left")
        login, self._login = self._login, None
        return self._open(login["u"], login["pp"])

    def cancel_login(self) -> None:
        self._login = None

    def _open(self, u, passphrase) -> dict:
        pp = u.passphrase if u.passphrase is not None else (passphrase or "")
        network = self.vault.info()["network"]
        self._root = seedlib.root_key(u.mnemonic, pp, network)
        self._main, self._temp = None, None
        self._seed_tag = hashlib.sha256(u.mnemonic.encode()).digest()
        self._network = network
        self._duress = u.duress_set
        self._wipe_set = u.wipe_set
        self._load_registry()
        self._load_rules()
        self._touch()
        return self.public_info()

    def lock(self) -> None:
        self._root = None
        self._main = None
        self._temp = None
        self._duress = False
        self._wipe_set = False
        self._registry = []
        self._rules = {}
        self._pending.clear()

    # -------------------------------------------------------- secure chip
    def chip_status(self) -> dict:
        """What's plugged in, and whether the vault uses it. No PIN needed:
        the UI shows this in the status bar."""
        from .hw.atecc import open_chip
        _, st = open_chip()
        st = dict(st)
        st["in_use"] = bool(self.vault.exists() and self.vault.info().get("secure_chip"))
        return st

    def chip_entropy(self) -> bytes:
        """Random bytes from the secure chip, if one is connected (else b"")."""
        from .hw.atecc import chip_entropy
        return chip_entropy()

    def bind_chip(self, pin: str) -> dict:
        from .hw.atecc import open_chip
        _, st = open_chip()
        if st.get("state") != "ready":
            raise SignerError("no usable secure chip found")
        self.vault.bind_chip(pin, st["slot"])
        self._duress = False
        return self.chip_status()

    def unbind_chip(self, pin: str) -> dict:
        self.vault.unbind_chip(pin)
        self._duress = False
        return self.chip_status()

    # ------------------------------------------------------------- duress
    def set_duress(self, pin: str, duress_pin: str, erase_real: bool = False) -> None:
        """The duress PIN opens a fresh decoy wallet (put a little in it so
        it looks real). With erase_real, using it also destroys this seed."""
        self.vault.set_duress(pin, duress_pin, seedlib.new_mnemonic(12), erase_real)
        self._duress = True

    def clear_duress(self, pin: str) -> None:
        self.vault.clear_duress(pin)
        self._duress = False

    def wipe(self, pin: str) -> None:
        self.vault.unlock(pin)          # proves the PIN (and counts a try)
        self.wipe_all()

    def wipe_all(self) -> None:
        """Back to a first boot: the seed and every file the signer keeps
        (spending policy, cap, multisig wallets, account rules, timers)."""
        self.lock()
        self._login = None
        self.vault.wipe()
        for path in glob.glob(os.path.join(self.dir, "*")):
            if os.path.isfile(path) and not os.path.islink(path):
                try:
                    size = os.path.getsize(path)
                    with open(path, "r+b") as f:
                        f.write(os.urandom(max(size, 1)))
                        f.flush()
                        os.fsync(f.fileno())
                except OSError:
                    pass
                try:
                    os.remove(path)
                except OSError:
                    pass
        paper = os.path.join(self.dir, "paper")
        if os.path.isdir(paper):
            for path in glob.glob(os.path.join(paper, "*")):
                try:
                    os.remove(path)
                except OSError:
                    pass

    # ------------------------------------------------------------ wipe PIN
    def _is_bypass(self, candidate: str) -> bool:
        b = spolicy.load(self.policy_path)["bypass"]
        return bool(b) and spolicy.check_bypass(b, candidate)

    def set_wipe_pin(self, pin: str, wipe_pin: str) -> None:
        if self._is_bypass(wipe_pin):
            raise SignerError("that PIN is in use")
        self.vault.set_wipe_pin(pin, wipe_pin)
        if self._root is not None:
            self._wipe_set = True

    def clear_wipe_pin(self, pin: str) -> None:
        self.vault.clear_wipe_pin(pin)
        self._wipe_set = False

    # ----------------------------------------------------- login countdown
    def countdown_s(self) -> int:
        try:
            with open(os.path.join(self.dir, "countdown.json")) as f:
                v = int(json.load(f)["seconds"])
            return v if 0 <= v <= 24 * 3600 else 0
        except (FileNotFoundError, ValueError, KeyError, TypeError):
            return 0

    def set_countdown(self, pin: str, seconds: int) -> int:
        """Any change needs the PIN: shorter would help a thief who knows it,
        longer could lock you out by surprise."""
        if isinstance(seconds, bool) or not isinstance(seconds, int) or \
                not (seconds == 0 or 60 <= seconds <= 24 * 3600):
            raise SignerError("countdown must be 1 minute to 24 hours, or off")
        self.vault.unlock(pin)
        _atomic_write(os.path.join(self.dir, "countdown.json"),
                      json.dumps({"seconds": seconds}).encode())
        return seconds

    def change_pin(self, old_pin: str, new_pin: str) -> None:
        if self._is_bypass(new_pin):
            raise SignerError("that PIN is in use")
        self.vault.change_pin(old_pin, new_pin)

    def public_info(self) -> dict:
        root = self._need_root()
        recv, change = seedlib.public_descriptors(root, self._network)
        info = {"fingerprint": seedlib.fingerprint(root), "network": self._network,
                "descriptor": recv, "change_descriptor": change,
                "multisig_key": ms.cosigner_key(root, self._network)}
        if self._temp is not None:
            info["temporary"] = {"label": self._temp["label"]}
        return info

    # ------------------------------------------------- multisig registry
    # Registered wallets are encrypted with a key derived from the seed, so
    # each seed (real or decoy) sees only its own, and the file reveals nothing.
    def _registry_file(self) -> tuple[str, bytes]:
        key = hmac.new(self._root.secret, b"czwallet-registry-v1", hashlib.sha256).digest()
        name = hashlib.sha256(key).hexdigest()[:16]
        return os.path.join(self.dir, f"ms-{name}.reg"), key

    def _load_registry(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        path, key = self._registry_file()
        self._registry = []
        if os.path.exists(path):
            with open(path, "rb") as f:
                blob = f.read()
            for e in json.loads(AESGCM(key).decrypt(blob[:12], blob[12:], None)):
                self._registry.append(ms.parse_any(e["descriptor"], self._network,
                                                   name=e.get("name", "")))

    def _save_registry(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        path, key = self._registry_file()
        nonce = os.urandom(12)
        data = json.dumps([p.to_dict() for p in self._registry]).encode()
        _atomic_write(path, nonce + AESGCM(key).encrypt(nonce, data, None))

    # ------------------------------------------------------ account rules
    # Per-account rules, e.g. {"tr:1": "vault"}: a vault-only account can't
    # be spent from while any radio is on. Stored like the registry:
    # encrypted with a key from the seed, so each seed sees only its own.
    RULES = ("vault", "normal")

    def _rules_file(self) -> tuple[str, bytes]:
        key = hmac.new(self._root.secret, b"czwallet-rules-v1", hashlib.sha256).digest()
        name = hashlib.sha256(key).hexdigest()[:16]
        return os.path.join(self.dir, f"rules-{name}.bin"), key

    # ------------------------------------------------ notes & passwords
    def _notes_file(self) -> tuple[str, bytes]:
        key = hmac.new(self._root.secret, b"czwallet-notes-v1", hashlib.sha256).digest()
        name = hashlib.sha256(key).hexdigest()[:16]
        return os.path.join(self.dir, f"notes-{name}.bin"), key

    def _load_notes(self) -> list[dict]:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        path, key = self._notes_file()
        if not os.path.exists(path):
            return []
        with open(path, "rb") as f:
            blob = f.read()
        return json.loads(AESGCM(key).decrypt(blob[:12], blob[12:], None))

    def _save_notes(self, entries: list[dict]) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        path, key = self._notes_file()
        nonce = os.urandom(12)
        data = json.dumps(entries).encode()
        _atomic_write(path, nonce + AESGCM(key).encrypt(nonce, data, None))

    def notes(self) -> list[dict]:
        """Every note and password (signer screens only: not for the app)."""
        self._need_root()
        return self._load_notes()

    def note_save(self, entry: dict, index: int | None = None) -> list[dict]:
        from . import notes as nl
        self._need_root()
        try:
            e = nl.clean(entry)
        except nl.NoteError as ex:
            raise SignerError(str(ex))
        entries = self._load_notes()
        if index is None:
            if len(entries) >= nl.MAX_ENTRIES:
                raise SignerError(f"no room: {nl.MAX_ENTRIES} entries at most")
            entries.append(e)
        else:
            if not isinstance(index, int) or not 0 <= index < len(entries):
                raise SignerError("no such entry")
            e["made"] = entries[index].get("made", e["made"])
            entries[index] = e
        self._save_notes(entries)
        return entries

    def note_delete(self, index: int) -> list[dict]:
        self._need_root()
        entries = self._load_notes()
        if not isinstance(index, int) or not 0 <= index < len(entries):
            raise SignerError("no such entry")
        del entries[index]
        self._save_notes(entries)
        return entries

    def bip85_password(self, length: int, index: int, kind: str = "base64") -> str:
        from . import notes as nl
        root = self._need_root()
        try:
            return nl.bip85_password(root, length, index, kind)
        except nl.NoteError as e:
            raise SignerError(str(e))

    def _load_rules(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        path, key = self._rules_file()
        self._rules = {}
        if os.path.exists(path):
            with open(path, "rb") as f:
                blob = f.read()
            self._rules = json.loads(AESGCM(key).decrypt(blob[:12], blob[12:], None))

    def account_rules(self) -> dict:
        """{account id: rule} for accounts with a rule (public: no secrets)."""
        self._need_root()
        return dict(self._rules)

    def set_account_rule(self, pin: str, account: str, rule: str) -> dict:
        """PIN needed: a rule protects coins, so only you can loosen it."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        self._need_root()
        try:
            account = accts.make_id(*accts.parse(account))
        except accts.AccountError as e:
            raise SignerError(str(e))
        if rule == "normal" and self._rules.get(account) == "vault":
            self._hobbled("turning vault-only off")
        if rule not in self.RULES:
            raise SignerError("unknown rule")
        self._check_same_wallet(pin)
        if rule == "normal":
            self._rules.pop(account, None)
        else:
            self._rules[account] = rule
        path, key = self._rules_file()
        nonce = os.urandom(12)
        _atomic_write(path, nonce + AESGCM(key).encrypt(nonce, json.dumps(self._rules).encode(),
                                                        None))
        return dict(self._rules)

    # ------------------------------------------------------- card backup
    def backup_tag(self) -> str:
        """Names this seed's backup files (core/backup.py); reveals nothing."""
        from . import backup
        return backup.keys(self._need_root().secret)[1]

    def make_backup(self, app_data: dict) -> dict:
        """Encrypt the app's part plus the signer's (multisig wallets, account
        rules) with a key from the seed. Holds no secrets: safe to ask for."""
        from . import backup
        root = self._need_root()
        if not isinstance(app_data, dict):
            raise SignerError("bad backup data")
        payload = {"v": 1, "made": dt.date.today().isoformat(), "network": self._network,
                   "app": app_data,
                   "signer": {"multisig": [p.to_dict() for p in self._registry],
                              "rules": dict(self._rules),
                              "notes": self._load_notes()}}
        if self._temp is None:                         # the main seed's backup carries its vault
            payload["signer"]["seedvault"] = self._load_seedvault()
        key, tag = backup.keys(root.secret)
        try:
            blob = backup.seal(key, payload)
        except backup.BackupError as e:
            raise SignerError(str(e))
        return {"blob": backup.b64(blob), "tag": tag}

    def open_backup(self, blob_b64: str) -> dict:
        from . import backup
        root = self._need_root()
        try:
            payload = backup.open_sealed(backup.keys(root.secret)[0], backup.unb64(blob_b64))
        except (backup.BackupError, ValueError) as e:
            raise SignerError(str(e))
        if payload.get("network") != self._network:
            raise SignerError(f"that backup is for {payload.get('network')}, "
                              f"this wallet is on {self._network}")
        return payload

    def apply_backup(self, payload: dict) -> dict:
        """Merge the signer's part: multisig wallets (each re-checked, must
        include this device's key), vault-only rules and notes & passwords.
        Only ever adds; a restore can't loosen a rule or change a note."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        sig = payload.get("signer", {})
        added, skipped = 0, 0
        for e in sig.get("multisig", []):
            try:
                self.register_multisig(e["descriptor"], e.get("name", ""))
                added += 1
            except Exception:  # noqa: BLE001 - one bad entry doesn't stop the rest
                skipped += 1
        for acct, rule in sig.get("rules", {}).items():
            try:
                acct = accts.make_id(*accts.parse(acct))
            except accts.AccountError:
                continue
            if rule == "vault":
                self._rules[acct] = "vault"
        path, key = self._rules_file()
        nonce = os.urandom(12)
        _atomic_write(path, nonce + AESGCM(key).encrypt(nonce, json.dumps(self._rules).encode(),
                                                        None))
        from . import notes as nl
        theirs = sig.get("notes", [])
        notes_added = 0
        if isinstance(theirs, list) and theirs:
            merged, notes_added = nl.merge(self._load_notes(), theirs)
            if notes_added:
                self._save_notes(merged)
        vault_added = 0
        theirs = sig.get("seedvault", [])
        if self._temp is None and isinstance(theirs, list) and theirs:
            mine = self._load_seedvault()
            have = {e["fp"] for e in mine}
            for e in theirs:
                try:
                    e = self._vault_entry(e.get("secret", ""), e.get("passphrase", ""),
                                          e.get("label", ""), e.get("made"))
                except (SignerError, AttributeError):
                    continue
                if e["fp"] not in have and len(mine) < SEEDVAULT_MAX:
                    mine.append(e)
                    have.add(e["fp"])
                    vault_added += 1
            if vault_added:
                self._save_seedvault(mine)
        return {"multisig": added, "skipped": skipped, "rules": dict(self._rules),
                "notes": notes_added, "seedvault": vault_added}

    def _check_same_wallet(self, pin: str):
        u = self.vault.check_pin(pin)
        if hashlib.sha256(u.mnemonic.encode()).digest() != getattr(self, "_seed_tag", b""):
            raise SignerError("wrong PIN for this wallet")

    def _policy_info(self, p: ms.Policy) -> dict:
        info = {"id": p.id, "name": p.label, "k": p.k, "n": p.n, "kind": p.kind,
                "descriptor": p.descriptor, "cosigners": p.cosigners}
        if p.kind == "inherit":
            mine = self._root.my_fingerprint.hex()
            info["delay"] = p.delay
            info["role"] = "owner" if p.cosigners[0] == mine else "heir"
        return info

    def list_multisig(self) -> list[dict]:
        self._need_root()
        return [self._policy_info(p) for p in self._registry]

    def check_multisig(self, descriptor: str) -> dict:
        """Validate without saving (for the confirm screen)."""
        root = self._need_root()
        p = ms.parse_any(descriptor, self._network, ms.cosigner_key(root, self._network))
        return self._policy_info(p)

    def register_multisig(self, descriptor: str, name: str = "") -> dict:
        root = self._need_root()
        p = ms.parse_any(descriptor, self._network, ms.cosigner_key(root, self._network),
                         name=name.strip()[:24])
        self._registry = [q for q in self._registry if q.id != p.id] + [p]
        self._save_registry()
        self._touch()
        return self._policy_info(p)

    def remove_multisig(self, wallet_id: str) -> None:
        self._need_root()
        self._registry = [q for q in self._registry if q.id != wallet_id]
        self._save_registry()

    def check_pin(self, pin: str) -> bool:
        """Proves the PIN (and counts a try) without revealing anything."""
        self.vault.unlock(pin)
        return True

    def reveal_words(self, pin: str) -> str:
        """For the backup screen. Needs the PIN again even when unlocked.
        For a wallet imported from an xprv, that key (there are no words)."""
        self._hobbled("showing the seed")
        mnemonic, _ = self.vault.unlock(pin)
        if self._temp is not None:                    # the PIN is still the device's
            return self._temp["secret"]
        return mnemonic

    # ----------------------------------------- temporary seeds, seed vault
    # TEMPORARY SEED: another seed (or xprv) used until LOCK, without touching
    # the stored one. The PIN stays the device's; the main key waits in
    # memory and comes back with main_seed(). SEED VAULT: extra seeds kept
    # encrypted with a key from the main seed, to switch to in two clicks.
    # Signer screens only (the app never sees a secret), and not while a
    # spending policy is locked: loading the main seed as a "temporary" one
    # would walk around the policy.
    def _check_secret(self, secret: str, passphrase: str) -> tuple[str, str]:
        if not isinstance(secret, str) or not isinstance(passphrase, str) or len(passphrase) > 100:
            raise SignerError("bad seed")
        if seedlib.is_xprv(secret):
            try:
                return seedlib.check_xprv(secret, self._network), ""
            except ValueError as e:
                raise SignerError(str(e))
        m = seedlib.normalize(secret)
        if not seedlib.is_valid(m):
            raise SignerError("not a valid seed")
        return m, passphrase

    def _main_key(self) -> bip32.HDKey:
        self._need_root()
        return self._main if self._main is not None else self._root

    def use_temporary(self, secret: str, passphrase: str = "", label: str = "") -> dict:
        self._need_root()
        self._hobbled("temporary seeds")
        secret, passphrase = self._check_secret(secret, passphrase)
        root = seedlib.root_key(secret, passphrase, self._network)
        main = self._main_key()
        if root.my_fingerprint == main.my_fingerprint:
            return self.main_seed()                    # that's the main seed: just go back
        self._main = main
        self._root = root
        self._temp = {"secret": secret, "passphrase": passphrase,
                      "label": str(label)[:16] or seedlib.fingerprint(root).upper()}
        self._pending.clear()
        self._load_registry()
        self._load_rules()
        self._touch()
        return self.public_info()

    def main_seed(self) -> dict:
        """Back to the stored seed (also what LOCK and the next unlock do)."""
        self._need_root()
        if self._main is not None:
            self._root, self._main, self._temp = self._main, None, None
            self._pending.clear()
            self._load_registry()
            self._load_rules()
        self._touch()
        return self.public_info()

    def _seedvault_file(self) -> tuple[str, bytes]:
        key = hmac.new(self._main_key().secret, b"czwallet-seedvault-v1", hashlib.sha256).digest()
        name = hashlib.sha256(key).hexdigest()[:16]
        return os.path.join(self.dir, f"sv-{name}.bin"), key

    def _load_seedvault(self) -> list[dict]:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        path, key = self._seedvault_file()
        if not os.path.exists(path):
            return []
        with open(path, "rb") as f:
            blob = f.read()
        return json.loads(AESGCM(key).decrypt(blob[:12], blob[12:], None))

    def _save_seedvault(self, entries: list[dict]) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        path, key = self._seedvault_file()
        nonce = os.urandom(12)
        _atomic_write(path, nonce + AESGCM(key).encrypt(nonce, json.dumps(entries).encode(), None))

    def _vault_entry(self, secret, passphrase, label, made=None) -> dict:
        secret, passphrase = self._check_secret(secret, passphrase)
        fp = seedlib.fingerprint(seedlib.root_key(secret, passphrase, self._network))
        return {"secret": secret, "passphrase": passphrase,
                "label": (str(label).strip()[:16] or fp.upper()), "fp": fp,
                "made": made if isinstance(made, int) else int(time.time())}

    def seedvault(self) -> list[dict]:
        """What's in the vault, without the secrets (signer screens only)."""
        self._need_root()
        self._hobbled("the seed vault")
        out = []
        for e in self._load_seedvault():
            kind = "XPRV" if seedlib.is_xprv(e["secret"]) else f"{len(e['secret'].split())}W"
            out.append({"label": e["label"], "fp": e["fp"], "kind": kind,
                        "passphrase": bool(e["passphrase"]), "made": e["made"],
                        "active": self._temp is not None and
                        seedlib.fingerprint(self._root) == e["fp"]})
        return out

    def seedvault_add(self, secret: str, passphrase: str = "", label: str = "") -> list[dict]:
        self._need_root()
        self._hobbled("the seed vault")
        e = self._vault_entry(secret, passphrase, label)
        if e["fp"] == self._main_key().my_fingerprint.hex():
            raise SignerError("that's your main seed")
        entries = self._load_seedvault()
        if any(x["fp"] == e["fp"] for x in entries):
            raise SignerError("already in the vault")
        if len(entries) >= SEEDVAULT_MAX:
            raise SignerError(f"the vault is full ({SEEDVAULT_MAX})")
        entries.append(e)
        self._save_seedvault(entries)
        return self.seedvault()

    def seedvault_use(self, index: int) -> dict:
        self._need_root()
        entries = self._load_seedvault()
        if not isinstance(index, int) or not 0 <= index < len(entries):
            raise SignerError("no such seed")
        e = entries[index]
        return self.use_temporary(e["secret"], e["passphrase"], e["label"])

    def seedvault_rename(self, index: int, label: str) -> list[dict]:
        self._need_root()
        entries = self._load_seedvault()
        if not isinstance(index, int) or not 0 <= index < len(entries) or not str(label).strip():
            raise SignerError("no such seed")
        entries[index]["label"] = str(label).strip()[:16]
        self._save_seedvault(entries)
        if self._temp is not None and seedlib.fingerprint(self._root) == entries[index]["fp"]:
            self._temp["label"] = entries[index]["label"]
        return self.seedvault()

    def seedvault_delete(self, index: int) -> list[dict]:
        self._need_root()
        entries = self._load_seedvault()
        if not isinstance(index, int) or not 0 <= index < len(entries):
            raise SignerError("no such seed")
        del entries[index]
        self._save_seedvault(entries)
        return self.seedvault()

    def temporary_secret(self) -> tuple[str, str] | None:
        """The temporary seed in use (to save it to the vault from its screen)."""
        self._need_root()
        return (self._temp["secret"], self._temp["passphrase"]) if self._temp else None

    # ------------------------------------------------------- key teleport
    # KEY TELEPORT (core/teleport.py, COLDCARD's protocol). The receiver's
    # one-time key waits here, encrypted with the main seed, until a package
    # opens with it (or a new one is asked for). Signer screens only.
    def _kt_file(self) -> tuple[str, bytes]:
        key = hmac.new(self._main_key().secret, b"czwallet-teleport-v1", hashlib.sha256).digest()
        name = hashlib.sha256(key).hexdigest()[:16]
        return os.path.join(self.dir, f"kt-{name}.bin"), key

    def kt_rx_key(self, new: bool = False, make: bool = True) -> bytes | None:
        """The waiting receive key: made (and kept) when there's none or new=True."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from . import teleport as kt
        self._need_root()
        self._hobbled("receiving secrets")
        path, key = self._kt_file()
        if os.path.exists(path) and not new:
            with open(path, "rb") as f:
                blob = f.read()
            return AESGCM(key).decrypt(blob[:12], blob[12:], None)
        if not make:
            return None
        k = kt.new_secret()
        nonce = os.urandom(12)
        _atomic_write(path, nonce + AESGCM(key).encrypt(nonce, k, None))
        return k

    def kt_rx_done(self) -> None:
        """A package opened: that receive key is never used again."""
        self._need_root()
        path, _ = self._kt_file()
        if os.path.exists(path):
            os.remove(path)

    def teleport_secret(self, pin: str) -> tuple[bytes, str]:
        """The seed in use, in COLDCARD's encoding, after the PIN. With a
        passphrase it goes as the master xprv it makes (as COLDCARD does)."""
        from . import teleport as kt
        words = self.reveal_words(pin=pin)            # PIN, policy checks
        root = self._need_root()
        fp = seedlib.fingerprint(root)
        if seedlib.fingerprint(seedlib.root_key(words, "", self._network)) == fp:
            return kt.encode_secret(words), fp
        return b"\x01" + root.chain_code + root.key.secret, fp

    def seedvault_secret(self, index: int) -> tuple[bytes, str, str]:
        """(encoded, fingerprint, label) of a seed vault entry, for teleport."""
        from . import teleport as kt
        self._need_root()
        self._hobbled("the seed vault")
        entries = self._load_seedvault()
        if not isinstance(index, int) or not 0 <= index < len(entries):
            raise SignerError("no such seed")
        e = entries[index]
        if e["passphrase"]:
            r = seedlib.root_key(e["secret"], e["passphrase"], self._network)
            enc = b"\x01" + r.chain_code + r.key.secret
        else:
            enc = kt.encode_secret(e["secret"])
        return enc, e["fp"], e["label"]

    def _ms_keys(self, pol) -> list[tuple[str, str, bip32.HDKey]]:
        """(fingerprint, path, xpub) of every key in a registered wallet."""
        import re
        out = []
        for fp, path, xk in re.findall(r"\[([0-9a-fA-F]{8})((?:/\d+[hH']?)*)\]([1-9A-HJ-NP-Za-km-z]{100,120})",
                                       pol.descriptor):
            out.append((fp.lower(), path, bip32.HDKey.from_string(xk)))
        return out

    def _my_ms_key(self, pol) -> bip32.HDKey | None:
        mine = self._root.my_fingerprint.hex()
        for fp, path, xk in self._ms_keys(pol):
            if fp == mine:
                k = self._root.derive("m" + path)
                if k.to_public().key.sec() == xk.key.sec():
                    return k
        return None

    def kt_ms_candidates(self, nonce: int) -> list[tuple[str, bytes, bytes]]:
        """For an E package: (sender fingerprint, my private key, his pubkey)
        for every other co-signer of every registered wallet."""
        from . import teleport as kt
        self._need_root()
        out = []
        for pol in self._registry:
            me = self._my_ms_key(pol)
            if me is None:
                continue
            mine = kt.ms_child_secret(me, nonce)
            for fp, _, xk in self._ms_keys(pol):
                if fp != self._root.my_fingerprint.hex():
                    out.append((fp, mine, kt.ms_child_pubkey(xk, nonce)))
        return out

    def kt_ms_target(self, wallet_id: str, fingerprint: str, nonce: int) -> tuple[bytes, bytes]:
        """(my private key, his pubkey) to send a PSBT to one co-signer."""
        from . import teleport as kt
        self._need_root()
        pol = next((p for p in self._registry if p.id == wallet_id), None)
        me = pol and self._my_ms_key(pol)
        if not me:
            raise SignerError("not a wallet this device is part of")
        his = [xk for fp, _, xk in self._ms_keys(pol) if fp == fingerprint.lower()]
        if len(his) != 1 or fingerprint.lower() == self._root.my_fingerprint.hex():
            raise SignerError("no such co-signer")
        return kt.ms_child_secret(me, nonce), kt.ms_child_pubkey(his[0], nonce)

    def ms_cosigners(self, wallet_id: str) -> list[str]:
        self._need_root()
        pol = next((p for p in self._registry if p.id == wallet_id), None)
        if pol is None:
            raise SignerError("unknown wallet")
        return [fp for fp, _, _ in self._ms_keys(pol)]

    # ------------------------------------------------------ spending policy
    def _policy_day(self, rules: dict) -> dict:
        """Today's total for PER DAY: the same day rules as the online cap
        (a clock change alone can't start a new day within one boot)."""
        st = dict(rules.get("state") or {})
        today = dt.date.today().isoformat()
        boot, up = _boot_id(), _boot_clock()
        st.setdefault("spent", 0)
        st.setdefault("signed", [])
        if st.get("day") != today:
            same_boot = st.get("boot") == boot
            if not same_boot or up - st.get("reset_at", 0) >= CAP_DAY_S or not st.get("day"):
                st.update(day=today, spent=0, signed=[], boot=boot, reset_at=up)
        return st

    def policy_info(self) -> dict:
        return spolicy.public(spolicy.load(self.policy_path))

    def policy_locked(self) -> bool:
        p = spolicy.load(self.policy_path)
        return bool(p["bypass"]) or p["damaged"]

    def _hobbled(self, what: str = "that") -> None:
        if self.policy_locked():
            raise SignerError(f"spending policy is locked: {what} needs it unlocked "
                              "(BYPASS PIN) first")

    def _check_bypass(self, rules: dict, bypass_pin: str | None) -> None:
        """A locked policy changes only with the bypass PIN. Wrong ones count
        as wrong PINs, so they can't be guessed endlessly."""
        if rules.get("damaged"):
            raise SignerError("the spending policy file is damaged: restore from your "
                              "seed words to start over")
        if not rules["bypass"]:
            return
        if not bypass_pin or not spolicy.check_bypass(rules["bypass"], bypass_pin):
            failed = self.vault._tries() + 1
            info = self.vault.info()
            self.vault._set_tries(failed)
            left = info["max_tries"] - failed
            if left <= 0:
                self.wipe_all()
                raise Wiped("too many wrong PINs; seed erased")
            from .vault import WrongPin
            raise WrongPin(left)

    def _check_address(self, addr):
        from embit.script import address_to_scriptpubkey
        if not isinstance(addr, str) or len(addr) > 100:
            raise SignerError("not an address for this network")
        a = addr.strip()
        try:
            spk = address_to_scriptpubkey(a)
            net = EMBIT_NETS[NETWORKS[self._network or self.vault.info()["network"]][1]]
            if spk.address(net) != a:
                raise ValueError
        except Exception:
            raise SignerError("not an address for this network")
        return a

    def set_policy(self, pin: str, field: str, value, bypass_pin: str | None = None) -> dict:
        rules = spolicy.load(self.policy_path)
        self._check_bypass(rules, bypass_pin)
        try:
            new = spolicy.change(rules, field, value, self._check_address)
        except spolicy.PolicyError as e:
            raise SignerError(str(e))
        self.vault.unlock(pin)
        _atomic_write(self.policy_path, json.dumps(new).encode())
        return spolicy.public(new)

    def set_bypass(self, pin: str, new_bypass: str, bypass_pin: str | None = None) -> dict:
        rules = spolicy.load(self.policy_path)
        self._check_bypass(rules, bypass_pin)
        if len(new_bypass) < 4 or new_bypass == pin:
            raise SignerError("bypass PIN must differ from your PIN (4+ characters)")
        self.vault.unlock(pin)
        if self.vault.pin_in_use(new_bypass):
            raise SignerError("that PIN is in use")
        rules["bypass"] = spolicy.hash_bypass(new_bypass, ARGON_T_BYPASS, ARGON_M_BYPASS)
        _atomic_write(self.policy_path, json.dumps(rules).encode())
        return spolicy.public(rules)

    def clear_bypass(self, pin: str, bypass_pin: str) -> dict:
        rules = spolicy.load(self.policy_path)
        if not rules["bypass"]:
            return spolicy.public(rules)
        self._check_bypass(rules, bypass_pin)
        self.vault.unlock(pin)
        rules["bypass"] = None
        _atomic_write(self.policy_path, json.dumps(rules).encode())
        return spolicy.public(rules)

    def set_totp(self, pin: str, secret: str | None, code: str = "",
                 bypass_pin: str | None = None) -> dict:
        """Turn phone 2FA on (secret plus a current code from the phone, so a
        typo in the setup can't lock you out) or off (secret None)."""
        rules = spolicy.load(self.policy_path)
        self._check_bypass(rules, bypass_pin)
        if secret is not None:
            if not isinstance(secret, str) or not 16 <= len(secret) <= 64:
                raise SignerError("bad 2FA secret")
            try:
                step = spolicy.verify_code(secret, code, 0)
            except spolicy.PolicyError as e:
                raise SignerError(str(e))
        self.vault.unlock(pin)
        rules["totp"] = secret
        st = dict(rules.get("state") or {})
        st["last_step"] = step if secret is not None else 0
        rules["state"] = st
        _atomic_write(self.policy_path, json.dumps(rules).encode())
        return spolicy.public(rules)

    # ----------------------------------------------------------- spend cap
    def _cap_state(self) -> dict:
        """Today's cap, spent and signed list. A new day starts the count
        again, but only for a real new day: the date comes from the clock,
        which a spoofed network time server could move, so within one boot a
        reset also needs 20 hours to have passed on the kernel's boot clock
        (which no time server can touch). After a reboot the date decides."""
        try:
            with open(self.cap_path) as f:
                c = json.load(f)
        except FileNotFoundError:
            c = {"cap": 0, "day": "", "spent": 0}
        today = dt.date.today().isoformat()
        boot, up = _boot_id(), _boot_clock()
        if c["day"] != today:
            same_boot = c.get("boot") == boot
            if not same_boot or up - c.get("reset_at", 0) >= CAP_DAY_S or not c["day"]:
                c = {**c, "day": today, "spent": 0, "signed": [], "boot": boot, "reset_at": up}
        c.setdefault("signed", [])
        return c

    @staticmethod
    def _cost(cap: dict, outpoints: set[str], cost: int) -> tuple[int, list]:
        """What this signature adds to today's total. A replacement (RBF)
        spends the same coins as something already signed today, and at most
        one of the two can ever confirm, so the earlier one's cost is credited."""
        replaced = [e for e in cap["signed"] if outpoints & set(e["in"])]
        credit = sum(e["cost"] for e in replaced)
        return max(0, cost - credit), replaced

    def set_cap(self, pin: str, cap_sats: int) -> dict:
        """0 = no online spending at all (Online mode can only receive)."""
        c = self._cap_state()
        if int(cap_sats) > c["cap"]:
            self._hobbled("raising the online cap")
        self.vault.unlock(pin)
        c["cap"] = max(0, int(cap_sats))
        _atomic_write(self.cap_path, json.dumps(c).encode())
        return c

    # ------------------------------------------------------------- PSBTs
    def describe(self, psbt: str) -> dict:
        """Parse and check a PSBT; returns what the confirm screen shows.
        Raises SignerError if anything about it is wrong."""
        root = self._need_root()
        try:
            psbt = PSBT.from_string(psbt)
        except Exception as e:
            raise SignerError(f"unreadable PSBT: {e}")
        if len(psbt.inputs) > MAX_INPUTS or len(psbt.outputs) > MAX_OUTPUTS:
            # a giant PSBT would tie the signer up for minutes on the CM0
            raise SignerError(f"too big: at most {MAX_INPUTS} inputs and "
                              f"{MAX_OUTPUTS} outputs")
        emb = EMBIT_NETS[NETWORKS[self._network][1]]
        coin = NETWORKS[self._network][2]

        def own_branch(scope, script):
            """Single-sig: (account id, branch) if this script is one of our
            own account keys' (BIP84 wpkh or BIP86 key-path Taproot), the
            whole path re-derived here; else (None, None)."""
            def path_ok(path, purpose):
                return (len(path) == 5 and path[0] == purpose + H and path[1] == coin + H
                        and H <= path[2] <= H + accts.MAX_ACCOUNT and path[3] in (0, 1)
                        and path[4] < H)
            for pub, der in scope.bip32_derivations.items():
                path = list(der.derivation)
                if der.fingerprint != root.my_fingerprint or not path_ok(path, 84):
                    continue
                key = root.derive(path).get_public_key()
                if key.sec() == pub.sec() and p2wpkh(key).data == script.data:
                    return accts.make_id("wpkh", path[2] - H), path[3]
            for pub, (leaves, der) in scope.taproot_bip32_derivations.items():
                path = list(der.derivation)
                if leaves or der.fingerprint != root.my_fingerprint or not path_ok(path, 86):
                    continue
                key = root.derive(path).get_public_key()
                if key.xonly() == pub.xonly() and p2tr(key).data == script.data:
                    return accts.make_id("tr", path[2] - H), path[3]
            return None, None

        ms_acct = [48 + H, coin + H, 0 + H, 2 + H]

        def multisig_branch(derivs, script):
            """(policy, branch) if script belongs to a registered multisig
            wallet: our key's path picks the index, the whole policy is
            re-derived and must give exactly this script."""
            for pub, der in derivs.items():
                if der.fingerprint != root.my_fingerprint:
                    continue
                path = list(der.derivation)
                if path[:4] != ms_acct or len(path) != 6 or path[4] not in (0, 1):
                    continue
                if root.derive(path).get_public_key().sec() != pub.sec():
                    continue
                for pol in self._registry:
                    if pol.derive(path[4], path[5]).script_pubkey().data == script.data:
                        return pol, path[4]
            return None, None

        total_in, wallet, vbytes, sigs_present = 0, None, 10.5, 0
        for i, inp in enumerate(psbt.inputs):
            utxo = self._spent_coin(psbt, i)
            if inp.sighash_type is not None and inp.sighash_type not in (0, 1):
                raise SignerError(f"input {i} asks for an unusual signature type; refusing")
            acct, _ = own_branch(inp, utxo.script_pubkey)
            if acct is not None:
                if inp.taproot_merkle_root or inp.taproot_scripts or inp.redeem_script \
                        or inp.witness_script:
                    raise SignerError(f"input {i} has scripts this wallet didn't make")
                this, vb = acct, accts.KINDS[accts.parse(acct)[0]]["input_vb"]
            else:
                pol, _ = multisig_branch(inp.bip32_derivations, utxo.script_pubkey)
                if pol is None:
                    raise SignerError(f"input {i} is not from this wallet")
                this, vb = pol, pol.input_vbytes()
                sigs_present = max(sigs_present, len(inp.partial_sigs))
            if wallet is None:
                wallet = this
            elif wallet != this:
                raise SignerError("inputs come from different wallets")
            total_in += utxo.value
            vbytes += vb

        outputs, total_out, spend, far = [], 0, 0, []
        for i, out in enumerate(psbt.outputs):
            vout = psbt.tx.vout[i]
            if isinstance(wallet, str):
                # change only counts as change inside the same account
                acct, branch = own_branch(out, vout.script_pubkey)
                if acct != wallet:
                    branch = None
            else:
                # change only counts as change inside the same multisig wallet
                pol, branch = multisig_branch(out.bip32_derivations, vout.script_pubkey)
                if pol is not wallet:
                    branch = None
            kind = {1: "change", 0: "self"}.get(branch, "send")
            if kind != "send":
                idx = self._out_index(out)
                if idx is not None and idx > FAR_INDEX:
                    far.append(f"{'Change' if kind == 'change' else 'Self'} goes to address "
                               f"#{idx}: your wallet may not show it")
            try:
                addr = vout.script_pubkey.address(emb)
            except Exception:
                addr = "(non-standard)"
            outputs.append({"address": addr, "sats": vout.value, "kind": kind})
            total_out += vout.value
            vbytes += 9 + len(vout.script_pubkey.data)
            if kind == "send":
                spend += vout.value

        fee = total_in - total_out
        if fee <= 0:
            raise SignerError("fee is zero or negative")
        fee_rate = fee / vbytes
        if fee_rate > HARD_FEE_RATE:
            raise SignerError(f"fee rate {fee_rate:.0f} sat/vB is absurd; refusing")

        warnings = list(far)
        if fee_rate > WARN_FEE_RATE:
            warnings.append(f"High fee: {fee_rate:.0f} sat/vB")
        if spend and fee > spend * 0.1:
            warnings.append("Fee is over 10% of the amount sent")

        online = network_is_up()
        cap = self._cap_state()
        outpoints = {f"{i.txid.hex()}:{i.vout}" for i in psbt.tx.vin}
        added, replaced = self._cost(cap, outpoints, spend + fee)
        vault_only = isinstance(wallet, str) and self._rules.get(wallet) == "vault"
        blocked = "VAULT-ONLY ACCOUNT. TURN RADIOS OFF (VAULT MODE)." \
            if vault_only and online else ""
        rules = spolicy.load(self.policy_path)
        pstate = self._policy_day(rules)
        outpoints = {f"{i.txid.hex()}:{i.vout}" for i in psbt.tx.vin}
        sends = [(o["address"], o["sats"]) for o in outputs if o["kind"] == "send"]
        p_added, p_replaced = self._cost(pstate, outpoints, spend + fee)
        why, needs_code = spolicy.check(rules, sends, spend, fee, fee_rate, time.time(),
                                        pstate["spent"], pstate.get("last_sign"),
                                        day_cost=p_added, replaces=bool(p_replaced))
        if why and not blocked:
            blocked = why[0]
        cap_ok = not blocked and ((not online) or added <= cap["cap"] - cap["spent"])
        digest = hashlib.sha256(psbt.tx.serialize()).hexdigest()
        whole = hashlib.sha256(psbt.serialize()).hexdigest()
        summary = {"digest": digest, "outputs": outputs, "inputs": len(psbt.inputs),
                   "total_in": total_in, "spend": spend, "fee": fee,
                   "fee_rate": round(fee_rate, 1), "warnings": warnings,
                   "online": online, "cap": cap, "cap_ok": cap_ok, "cap_cost": added,
                   "replaces_signed": bool(replaced) or bool(p_replaced),
                   "vault_only": vault_only, "blocked": blocked,
                   "policy": why, "needs_code": needs_code,
                   "wallet": ("Single-sig" if wallet == accts.MAIN else accts.label(wallet))
                   if isinstance(wallet, str) else wallet.label,
                   "wallet_id": (None if wallet == accts.MAIN else wallet)
                   if isinstance(wallet, str) else wallet.id,
                   "sigs_needed": 1 if isinstance(wallet, str) else wallet.k,
                   "sigs_present": sigs_present}
        self._pending = {digest: dict(summary, psbt_hash=whole)}
        self._touch()
        return summary

    def sign(self, psbt: str, digest: str, code: str | None = None) -> str:
        """Sign only the exact transaction the user just confirmed. code: the
        phone 2FA code, when the spending policy asks for one."""
        root = self._need_root()
        summary = self._pending.get(digest)
        if summary is None:
            raise SignerError("confirm the transaction first")
        try:
            psbt = PSBT.from_string(psbt)
        except Exception as e:
            raise SignerError(f"unreadable PSBT: {e}")
        # The whole PSBT, not only the transaction: the coin amounts and key
        # paths in it are what the confirm screen and the spend cap were based on.
        if hashlib.sha256(psbt.tx.serialize()).hexdigest() != digest or \
                hashlib.sha256(psbt.serialize()).hexdigest() != summary.get("psbt_hash"):
            raise SignerError("transaction changed after confirmation")
        online = network_is_up()
        if summary.get("vault_only") and online:
            raise SignerError("this account is vault-only: turn the radios off to sign")
        cap = self._cap_state()
        outpoints = {f"{i.txid.hex()}:{i.vout}" for i in psbt.tx.vin}
        cost = summary["spend"] + summary["fee"]
        added, replaced = self._cost(cap, outpoints, cost)
        if online and added > cap["cap"] - cap["spent"]:
            left = max(0, cap["cap"] - cap["spent"])
            raise SignerError(f"over today's online cap ({left} sats left); "
                              "switch to Vault mode to send more")
        rules = spolicy.load(self.policy_path)
        pstate = self._policy_day(rules)
        sends = [(o["address"], o["sats"]) for o in summary["outputs"] if o["kind"] == "send"]
        ins = {f"{i.txid.hex()}:{i.vout}" for i in psbt.tx.vin}
        p_added, p_replaced = self._cost(pstate, ins, summary["spend"] + summary["fee"])
        why, needs_code = spolicy.check(rules, sends, summary["spend"], summary["fee"],
                                        summary["fee_rate"], time.time(), pstate["spent"],
                                        pstate.get("last_sign"), day_cost=p_added,
                                        replaces=bool(p_replaced))
        if why:
            raise SignerError("spending policy: " + why[0].lower())
        step = None
        if needs_code:
            if not code:
                raise SignerError("2FA code needed")
            try:
                step = spolicy.verify_code(rules["totp"], code, pstate.get("last_step", 0))
            except spolicy.PolicyError as e:
                raise SignerError(str(e))
        n = psbt.sign_with(root)
        if n < len(psbt.inputs):
            raise SignerError("could not sign every input")
        if summary["spend"] or step is not None:
            if summary["spend"]:
                pstate["spent"] += p_added
                pstate["signed"] = [e for e in pstate["signed"] if e not in p_replaced]
                pstate["signed"].append({"in": sorted(ins),
                                         "cost": summary["spend"] + summary["fee"]})
                if not p_replaced:
                    pstate["last_sign"] = time.time()
            if step is not None:
                pstate["last_step"] = step
            rules["state"] = pstate
            _atomic_write(self.policy_path, json.dumps(rules).encode())
        if online:
            cap["spent"] += added
            cap["signed"] = [e for e in cap["signed"] if e not in replaced]
            cap["signed"].append({"in": sorted(outpoints), "cost": cost})
            _atomic_write(self.cap_path, json.dumps(cap).encode())
        self._pending.clear()
        self._touch()
        return psbt.to_string()

    @staticmethod
    def _spent_coin(psbt, i: int):
        """The coin input i spends, checked. SegWit v0 signatures commit only
        to their own input's amount, so a PSBT that lies about amounts can
        hide a huge fee across two signings (the 2020 "SegWit fee attack").
        So for v0 inputs the full previous transaction must be there, hash to
        the txid being spent, and agree with any witness_utxo. Taproot
        signatures commit to every input's amount, so witness_utxo is enough."""
        inp, txin = psbt.inputs[i], psbt.tx.vin[i]
        prev = inp.non_witness_utxo
        if prev is not None:
            if prev.txid() != txin.txid:
                raise SignerError(f"input {i}: its previous transaction doesn't match")
            if txin.vout >= len(prev.vout):
                raise SignerError(f"input {i} spends an output that doesn't exist")
            coin = prev.vout[txin.vout]
            w = inp.witness_utxo
            if w is not None and (w.value != coin.value or w.script_pubkey.data != coin.script_pubkey.data):
                raise SignerError(f"input {i}: the coin's amount doesn't match its transaction")
            return coin
        coin = inp.witness_utxo
        if coin is None:
            raise SignerError(f"input {i} is missing the coin it spends")
        if coin.script_pubkey.script_type() != "p2tr":
            raise SignerError(f"input {i} is missing its previous transaction, needed to "
                              "check the fee")
        return coin

    @staticmethod
    def _out_index(out):
        for der in out.bip32_derivations.values():
            if der.derivation:
                return der.derivation[-1]
        for _, (_, der) in out.taproot_bip32_derivations.items():
            if der.derivation:
                return der.derivation[-1]
        return None

    # ---------------------------------------------------------- internals
    def _touch(self):
        self._last_use = time.monotonic()

    def _expire(self):
        after = self.lock_after()
        if after == 0:                       # AUTO LOCK OFF: until LOCK NOW or a restart
            return
        limit = max(after, self._quick_hours() * 3600)
        if self._root is not None and time.monotonic() - self._last_use > limit:
            self.lock()

    # ----------------------------------------------------------- auto lock
    # SETUP > AUTO LOCK. Kept here, not in the app: it decides how long the
    # seed stays in memory, so only the signer enforces it. Longer (or OFF)
    # needs the PIN on the signer's screen; shorter is always allowed.
    LOCK_MIN_S, LOCK_MAX_S = 60, 24 * 3600

    def lock_after(self) -> int:
        """Seconds without use before the seed is forgotten; 0 = never."""
        try:
            with open(os.path.join(self.dir, "autolock.json")) as f:
                v = int(json.load(f)["seconds"])
            if v == 0 or self.LOCK_MIN_S <= v <= self.LOCK_MAX_S:
                return v
        except (FileNotFoundError, ValueError, KeyError, TypeError):
            pass
        return self.auto_lock_s

    def _check_lock_after(self, seconds) -> int:
        if isinstance(seconds, bool) or not isinstance(seconds, int) or \
                not (seconds == 0 or self.LOCK_MIN_S <= seconds <= self.LOCK_MAX_S):
            raise SignerError("auto lock must be 1 minute to 24 hours, or off")
        return seconds

    def _save_lock_after(self, seconds: int) -> int:
        _atomic_write(os.path.join(self.dir, "autolock.json"),
                      json.dumps({"seconds": seconds}).encode())
        return seconds

    # SETUP > SCREEN SLEEP: the backlight goes off after this long with no
    # key pressed. Kept by the signer because it owns the screen; it's only
    # a light, so changing it needs no PIN.
    SLEEP_MIN_S, SLEEP_MAX_S, SLEEP_DEFAULT_S = 60, 24 * 3600, 120

    def screen_sleep(self) -> int:
        try:
            with open(os.path.join(self.dir, "screen.json")) as f:
                v = int(json.load(f)["sleep"])
            if v == 0 or self.SLEEP_MIN_S <= v <= self.SLEEP_MAX_S:
                return v
        except (FileNotFoundError, ValueError, KeyError, TypeError):
            pass
        return self.SLEEP_DEFAULT_S

    def set_screen_sleep(self, seconds: int) -> int:
        if isinstance(seconds, bool) or not isinstance(seconds, int) or \
                not (seconds == 0 or self.SLEEP_MIN_S <= seconds <= self.SLEEP_MAX_S):
            raise SignerError("screen sleep must be 1 minute to 24 hours, or never")
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        _atomic_write(os.path.join(self.dir, "screen.json"),
                      json.dumps({"sleep": seconds}).encode())
        return seconds

    def set_auto_lock(self, pin: str, seconds: int) -> int:
        """Any value, OFF included. PIN needed."""
        seconds = self._check_lock_after(seconds)
        self.vault.unlock(pin)
        return self._save_lock_after(seconds)

    def shorten_auto_lock(self, seconds: int) -> int:
        """No PIN: a shorter timeout only makes things safer."""
        seconds = self._check_lock_after(seconds)
        now = self.lock_after()
        if seconds == 0 or (now != 0 and seconds > now):
            raise SignerError("a longer auto lock needs the PIN")
        return self._save_lock_after(seconds)

    # -------------------------------------------------------- quick unlock
    def _quick_hours(self) -> int:
        try:
            with open(os.path.join(self.dir, "quick.json")) as f:
                return int(json.load(f)["hours"])
        except (FileNotFoundError, ValueError, KeyError):
            return 0

    def set_quick_unlock(self, pin: str, hours: int) -> int:
        """Keep the seed unlocked for up to `hours` of inactivity so the
        screen can be reopened with a fingerprint. 0 turns it off. PIN needed."""
        self.vault.unlock(pin)
        hours = max(0, min(24, int(hours)))
        _atomic_write(os.path.join(self.dir, "quick.json"), json.dumps({"hours": hours}).encode())
        return hours

    def keepalive(self) -> None:
        """A key was pressed on the device: not idle. (Too late if the time
        was already up: the seed is forgotten first, never revived.)"""
        self._expire()
        if self._root is not None:
            self._touch()

    def address(self, wallet_id: str | None = None, index: int = 0, change: bool = False) -> str:
        """An address of one of this signer's wallets, derived here from the
        keys, so the signer's screen can show it without trusting the app."""
        self._need_root()                      # locked: refuses here
        index, branch = int(index), 1 if change else 0
        if not 0 <= index < 2 ** 31:
            raise SignerError("bad address index")
        emb = EMBIT_NETS[NETWORKS[self._network][1]]
        if wallet_id is None or ":" in str(wallet_id):          # a single-sig account
            key = self._account_key(wallet_id, branch, index)
            kind, _ = accts.parse(wallet_id)
            script = p2tr(key) if kind == "tr" else p2wpkh(key)
            return script.address(network=emb)
        for p in self._registry:
            if p.id == wallet_id:
                return p.derive(branch, index).address(network=emb)
        raise SignerError("no such wallet on this device")

    def _account_key(self, account, branch: int, index: int, private: bool = False):
        try:
            kind, n = accts.parse(account)
        except accts.AccountError as e:
            raise SignerError(str(e))
        path = seedlib.account_path(self._network, n, kind) + f"/{branch}/{index}"
        k = self._need_root().derive(path)
        return k.key if private else k.get_public_key()

    def account_info(self, account: str) -> dict:
        """Public descriptors of a single-sig account (core/accounts.py), for
        the app to watch. Public keys only."""
        try:
            kind, n = accts.parse(account)
        except accts.AccountError as e:
            raise SignerError(str(e))
        root = self._need_root()
        recv, change = seedlib.public_descriptors(root, self._network, n, kind)
        return {"id": accts.make_id(kind, n), "kind": kind, "number": n,
                "descriptor": recv, "change_descriptor": change,
                "fingerprint": seedlib.fingerprint(root)}

    def child_seed(self, pin: str, words: int, index: int) -> str:
        self._hobbled("child seeds")
        """A BIP85 child seed. Needs the PIN again: it's a secret that
        controls whatever wallet is made from it."""
        from . import bip85
        root = self._need_root()
        self._check_same_wallet(pin)
        try:
            return bip85.child_mnemonic(root, words, index)
        except ValueError as e:
            raise SignerError(str(e))

    def paper_wallet(self, kind: str = "segwit", rolls: str = "", with_pdf: bool = True) -> dict:
        """A brand-new key, unrelated to the seed, saved as <address>.txt (and
        .pdf) in the signer's own paper/ folder. The app only ever learns the
        address and the file names; cz-backup copies the files to the card."""
        from . import paper
        from .hw.atecc import chip_entropy
        self._need_root()                       # unlocked: the wallet's network
        try:
            secret = paper.dice_key(rolls) if rolls else paper.random_key(chip_entropy())
            w = paper.build(secret, self._network, kind)
            files = paper.save(self.paper_dir, w, with_pdf)
        except paper.PaperError as e:
            raise SignerError(str(e))
        return {"address": w["address"], "files": files, "network": self._network,
                "kind": kind, "dice": bool(rolls)}

    @property
    def paper_dir(self) -> str:
        return os.path.join(self.dir, "paper")

    def sign_message(self, index: int, message: str, fmt: str = "electrum",
                     account: str | None = None) -> dict:
        """Sign a text message with the key of receive address #index of a
        single-sig account. The signer's screen shows the address and the
        whole message before this is called. Taproot: BIP322 only."""
        from . import message as msg
        if not isinstance(index, int) or not 0 <= index < 2 ** 31:
            raise SignerError("bad address index")
        kind, _ = accts.parse(account) if account else ("wpkh", 0)
        if kind == "tr" and fmt != "bip322":
            raise SignerError("Taproot addresses sign with BIP322 only")
        try:
            msg.check_text(message)
            key = self._account_key(account, 0, index, private=True)
            sig = msg.sign(key, message, fmt, taproot=(kind == "tr"))
        except msg.MessageError as e:
            raise SignerError(str(e))
        self._touch()
        return {"address": self.address(wallet_id=account, index=index), "message": message,
                "signature": sig, "format": fmt}

    def touch(self) -> bool:
        """The screen was reopened (e.g. by fingerprint): True if still unlocked."""
        self._expire()
        if self._root is None:
            return False
        self._touch()
        return True

    def _need_root(self) -> bip32.HDKey:
        self._expire()
        if self._root is None:
            raise Locked("signer is locked")
        return self._root


__all__ = ["Signer", "SignerError", "Locked", "VaultError", "network_is_up"]
