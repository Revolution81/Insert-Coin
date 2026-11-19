"""
Encrypted seed storage (owned by the signer process only), with an
optional duress PIN.

  key  = Argon2id(PIN, salt, 64 MiB, 3 passes)        ~1-2 s on the CM0
  file = header + two slots, each AES-256-GCM(key_i, padded payload)

Two slots, always. One holds the real seed. The other holds either the
decoy wallet (opened by the duress PIN) or random bytes. Both slots are the
same size and look like noise, so the file never reveals whether a duress
PIN exists. Unlocking runs Argon2 once and tries the key on both slots.

A duress unlock can also silently overwrite the real slot ("erase real").

Wipe PIN: the header always carries a 32-byte tag "w". With a wipe PIN set
it's HMAC(Argon2 key of the wipe PIN); without one it's random bytes, so the
file never reveals whether a wipe PIN exists. Unlocking computes Argon2 once
(as always) and, if no slot opens, checks the tag: a match erases the vault.

The failed-try counter is bumped *before* each attempt and reset only after
a success, so cutting power mid-attempt doesn't give a free guess. When it
hits the limit the vault is wiped.

Honest limits: the counter only stops guessing on the device. Someone who
copies the microSD card can try PINs offline against the file, slowed only
by Argon2. Use a long PIN or an unstored BIP39 passphrase for real
protection of a stolen card.
"""
from __future__ import annotations

import base64
import json
import os
import secrets

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = 2
ARGON_T = int(os.environ.get("CZ_ARGON_T", 3))
ARGON_M = int(os.environ.get("CZ_ARGON_M_KIB", 64 * 1024))
ARGON_P = 4
DEFAULT_MAX_TRIES = 10
PAYLOAD_SIZE = 512             # every slot's plaintext is padded to this
SLOT_SIZE = PAYLOAD_SIZE + 16  # plus the GCM tag



def _not_reserved(pin: str) -> None:
    """The unlock screen reads this one as the Konami code (ui/egg.py), so it
    can never be a PIN of any kind."""
    if pin.strip().lower() == "ffxxzczcba":
        raise VaultError("That PIN is taken by a secret. Pick another")

class VaultError(Exception):
    pass


class WrongPin(VaultError):
    def __init__(self, tries_left: int):
        super().__init__(f"wrong PIN, {tries_left} tries left")
        self.tries_left = tries_left


class Wiped(VaultError):
    pass


class WipePinUsed(Wiped):
    """The wipe PIN was entered: the vault is gone, start fresh."""


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _unb64(s: str) -> bytes:
    return base64.b64decode(s)


def _atomic_write(path: str, data: bytes) -> None:
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def _random_slot() -> dict:
    return {"nonce": _b64(secrets.token_bytes(12)), "ct": _b64(secrets.token_bytes(SLOT_SIZE))}


def _pack(payload: dict) -> bytes:
    raw = json.dumps(payload).encode()
    if len(raw) > PAYLOAD_SIZE - 2:
        raise VaultError("secret too long")
    return len(raw).to_bytes(2, "big") + raw + secrets.token_bytes(PAYLOAD_SIZE - 2 - len(raw))


def _unpack(blob: bytes) -> dict:
    n = int.from_bytes(blob[:2], "big")
    return json.loads(blob[2:2 + n])


class Unlocked:
    """What a successful unlock found."""

    def __init__(self, payload: dict, slot: int):
        self.mnemonic = payload["m"]
        self.passphrase = payload.get("pp")          # None = ask every time
        self.decoy = payload.get("kind") == "decoy"
        self.duress_set = bool(payload.get("duress"))
        self.wipe_set = bool(payload.get("wipe"))
        self.slot = slot

    def __iter__(self):                              # (mnemonic, passphrase), as before
        return iter((self.mnemonic, self.passphrase))


class ChipMissing(VaultError):
    pass


class Vault:
    def __init__(self, directory: str, chip_factory=None):
        """chip_factory() -> (ATECC or None, status) for secure-chip binding."""
        self.dir = directory
        self.path = os.path.join(directory, "seed.vault")
        self.tries_path = os.path.join(directory, "tries.json")
        self.chip_factory = chip_factory

    # ------------------------------------------------------------ state
    def exists(self) -> bool:
        return os.path.exists(self.path)

    def _read(self) -> dict:
        with open(self.path) as f:
            return json.load(f)

    def info(self) -> dict:
        """Plaintext header: no secrets, and nothing about a duress PIN."""
        raw = self._read()
        return {"network": raw["network"], "max_tries": raw["max_tries"],
                "tries_left": raw["max_tries"] - self._tries(),
                "passphrase_stored": raw["pp_stored"],
                "secure_chip": bool(raw.get("se"))}

    def _tries(self) -> int:
        try:
            with open(self.tries_path) as f:
                return int(json.load(f)["failed"])
        except FileNotFoundError:
            return 0

    def _set_tries(self, n: int) -> None:
        _atomic_write(self.tries_path, json.dumps({"failed": n}).encode())

    # ------------------------------------------------------------ crypto
    def _key(self, pin: str, body: dict) -> bytes:
        k = body["kdf"]
        secret = hash_secret_raw(pin.encode(), _unb64(k["salt"]), time_cost=k["t"],
                                 memory_cost=k["m"], parallelism=k["p"], hash_len=32,
                                 type=Type.ID)
        se = body.get("se")
        if not se:
            return secret
        # Bound to the secure chip: the chip must compute ECDH on a point
        # derived from this PIN. No chip, no key; every guess needs the chip.
        from .hw.atecc import mix, pin_point
        chip = self._chip(se)
        return mix(secret, chip.ecdh(se["slot"], pin_point(secret)))

    def _chip(self, se: dict):
        import hashlib
        chip, status = self.chip_factory() if self.chip_factory else (None, {})
        if chip is None:
            raise ChipMissing("the secure chip isn't connected")
        if hashlib.sha256(chip.serial()).hexdigest()[:16] != se["serial"]:
            raise ChipMissing("that's a different secure chip")
        return chip

    @staticmethod
    def _aad(body: dict, slot: int) -> bytes:
        head = {x: body[x] for x in ("v", "network", "max_tries", "pp_stored")}
        head["salt"] = body["kdf"]["salt"]
        if body.get("se"):
            head["se"] = body["se"]
        head["slot"] = slot
        return json.dumps(head, sort_keys=True).encode()

    def _seal(self, key: bytes, body: dict, slot: int, payload: dict) -> dict:
        nonce = secrets.token_bytes(12)
        ct = AESGCM(key).encrypt(nonce, _pack(payload), self._aad(body, slot))
        return {"nonce": _b64(nonce), "ct": _b64(ct)}

    def _open(self, key: bytes, body: dict, slot: int) -> dict | None:
        s = body["slots"][slot]
        try:
            return _unpack(AESGCM(key).decrypt(_unb64(s["nonce"]), _unb64(s["ct"]),
                                               self._aad(body, slot)))
        except Exception:
            return None

    @staticmethod
    def _wipe_tag(key: bytes) -> bytes:
        import hashlib
        import hmac
        return hmac.new(key, b"insert-coin-wipe-pin-v1", hashlib.sha256).digest()

    def _is_wipe(self, key: bytes, body: dict) -> bool:
        import hmac
        w = body.get("w")
        return bool(w) and hmac.compare_digest(self._wipe_tag(key), _unb64(w))

    def _try_key(self, key: bytes, body: dict) -> tuple[dict, int] | None:
        found = None
        for slot in (0, 1):            # always try both: same time either way
            p = self._open(key, body, slot)
            if p is not None and found is None:
                found = (p, slot)
        return found

    # ----------------------------------------------------------- create
    def create(self, pin: str, mnemonic: str, network: str, passphrase: str | None = None,
               max_tries: int = DEFAULT_MAX_TRIES) -> None:
        if len(pin) < 4:
            raise VaultError("PIN must be at least 4 characters")
        _not_reserved(pin)
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        body = {"v": VERSION, "network": network, "max_tries": max_tries,
                "pp_stored": passphrase is not None,
                "kdf": {"alg": "argon2id", "t": ARGON_T, "m": ARGON_M, "p": ARGON_P,
                        "salt": _b64(secrets.token_bytes(16))},
                "slots": [_random_slot(), _random_slot()],
                "w": _b64(secrets.token_bytes(32))}  # wipe PIN tag: noise until one is set
        slot = secrets.randbelow(2)                   # real seed's slot is random
        key = self._key(pin, body)
        body["slots"][slot] = self._seal(key, body, slot,
                                         {"m": mnemonic, "pp": passphrase, "kind": "real"})
        _atomic_write(self.path, json.dumps(body).encode())
        self._set_tries(0)

    # ----------------------------------------------------------- unlock
    def unlock(self, pin: str) -> Unlocked:
        if not self.exists():
            raise Wiped("no seed on this device")
        body = self._read()
        if body.get("se"):
            self._chip(body["se"])             # missing chip isn't a wrong PIN
        failed = self._tries()
        if failed >= body["max_tries"]:
            self.wipe()
            raise Wiped("too many wrong PINs; seed erased")
        self._set_tries(failed + 1)            # count first, see module doc
        key = self._key(pin, body)
        hit = self._try_key(key, body)
        if hit is None and self._is_wipe(key, body):
            self.wipe()
            raise WipePinUsed("wiped")
        if hit is None:
            left = body["max_tries"] - (failed + 1)
            if left <= 0:
                self.wipe()
                raise Wiped("too many wrong PINs; seed erased")
            raise WrongPin(left)
        self._set_tries(0)
        payload, slot = hit
        if payload.get("kind") == "decoy" and payload.get("erase_real"):
            # Duress with erase: destroy the real seed, quietly.
            body["slots"][1 - slot] = _random_slot()
            payload["erase_real"] = False
            body["slots"][slot] = self._seal(self._key(pin, body), body, slot, payload)
            _atomic_write(self.path, json.dumps(body).encode())
        return Unlocked(payload, slot)

    def check_pin(self, pin: str) -> Unlocked:
        """Like unlock (counts tries); used to confirm sensitive actions."""
        return self.unlock(pin)

    # ------------------------------------------------------------ duress
    def set_duress(self, pin: str, duress_pin: str, decoy_mnemonic: str,
                   erase_real: bool = False) -> None:
        _not_reserved(duress_pin)
        if duress_pin == pin or len(duress_pin) < 4:
            raise VaultError("duress PIN must differ from your PIN (4+ characters)")
        u = self.unlock(pin)
        body = self._read()
        if u.decoy:
            # In the decoy wallet: look like it worked, never touch the other slot.
            self._reseal(pin, body, u, duress=True)
            return
        other = 1 - u.slot
        dkey = self._key(duress_pin, body)
        if self._is_wipe(dkey, body):
            raise VaultError("that PIN is in use")
        body["slots"][other] = self._seal(dkey, body, other, {
            "m": decoy_mnemonic, "pp": "", "kind": "decoy", "erase_real": erase_real})
        key = self._key(pin, body)
        real = {"m": u.mnemonic, "pp": u.passphrase, "kind": "real", "duress": True}
        if u.wipe_set:
            real["wipe"] = True
        body["slots"][u.slot] = self._seal(key, body, u.slot, real)
        _atomic_write(self.path, json.dumps(body).encode())

    def _reseal(self, pin, body, u, duress, wipe=None):
        payload = {"m": u.mnemonic, "pp": u.passphrase, "kind": "decoy" if u.decoy else "real"}
        if duress:
            payload["duress"] = True
        if u.wipe_set if wipe is None else wipe:
            payload["wipe"] = True
        body["slots"][u.slot] = self._seal(self._key(pin, body), body, u.slot, payload)
        _atomic_write(self.path, json.dumps(body).encode())

    def clear_duress(self, pin: str) -> None:
        u = self.unlock(pin)
        body = self._read()
        if u.decoy:
            self._reseal(pin, body, u, duress=False)
            return
        body["slots"][1 - u.slot] = _random_slot()
        self._reseal(pin, body, u, duress=False)

    # ---------------------------------------------------------- wipe PIN
    def set_wipe_pin(self, pin: str, wipe_pin: str) -> None:
        """A PIN that erases everything when entered at unlock."""
        _not_reserved(wipe_pin)
        if len(wipe_pin) < 4 or wipe_pin == pin:
            raise VaultError("wipe PIN must differ from your PIN (4+ characters)")
        u = self.unlock(pin)
        body = self._read()
        wkey = self._key(wipe_pin, body)
        if self._try_key(wkey, body) is not None:
            raise VaultError("that PIN is in use")
        body["w"] = _b64(self._wipe_tag(wkey))
        self._reseal(pin, body, u, duress=u.duress_set, wipe=True)

    def clear_wipe_pin(self, pin: str) -> None:
        u = self.unlock(pin)
        body = self._read()
        body["w"] = _b64(secrets.token_bytes(32))
        self._reseal(pin, body, u, duress=u.duress_set, wipe=False)

    def pin_in_use(self, candidate: str) -> bool:
        """Whether a PIN opens a slot or is the wipe PIN. Doesn't count a try
        (only the signer calls it, for a PIN you're choosing, after your PIN)."""
        body = self._read()
        key = self._key(candidate, body)
        return self._try_key(key, body) is not None or self._is_wipe(key, body)

    # -------------------------------------------------------- change pin
    def change_pin(self, old_pin: str, new_pin: str) -> None:
        _not_reserved(new_pin)
        if len(new_pin) < 4:
            raise VaultError("PIN must be at least 4 characters")
        u = self.unlock(old_pin)
        body = self._read()
        key = self._key(new_pin, body)
        if self._open(key, body, 1 - u.slot) is not None or self._is_wipe(key, body):
            raise VaultError("that PIN is already in use")
        payload = {"m": u.mnemonic, "pp": u.passphrase,
                   "kind": "decoy" if u.decoy else "real"}
        if u.duress_set:
            payload["duress"] = True
        if u.wipe_set:
            payload["wipe"] = True
        body["slots"][u.slot] = self._seal(key, body, u.slot, payload)
        _atomic_write(self.path, json.dumps(body).encode())

    # ------------------------------------------------------- secure chip
    def bind_chip(self, pin: str, slot: int) -> None:
        """Re-encrypt the seed so it needs the chip from now on. The other
        slot (duress decoy) can't be re-wrapped without its PIN, so it's
        cleared: set the duress PIN again afterwards."""
        import hashlib
        u = self.unlock(pin)
        if u.decoy:
            return                                     # pretend; never touch the real seed
        chip, _ = self.chip_factory() if self.chip_factory else (None, {})
        if chip is None:
            raise ChipMissing("the secure chip isn't connected")
        body = self._read()
        body["se"] = {"slot": slot, "serial": hashlib.sha256(chip.serial()).hexdigest()[:16]}
        body["slots"] = [_random_slot(), _random_slot()]
        body["w"] = _b64(secrets.token_bytes(32))       # set the wipe PIN again afterwards
        body["slots"][u.slot] = self._seal(self._key(pin, body), body, u.slot, {
            "m": u.mnemonic, "pp": u.passphrase, "kind": "real"})
        _atomic_write(self.path, json.dumps(body).encode())

    def unbind_chip(self, pin: str) -> None:
        u = self.unlock(pin)
        if u.decoy:
            return
        body = self._read()
        body.pop("se", None)
        body["slots"] = [_random_slot(), _random_slot()]
        body["w"] = _b64(secrets.token_bytes(32))
        body["slots"][u.slot] = self._seal(self._key(pin, body), body, u.slot, {
            "m": u.mnemonic, "pp": u.passphrase, "kind": "real"})
        _atomic_write(self.path, json.dumps(body).encode())

    # -------------------------------------------------------------- wipe
    def wipe(self) -> None:
        """Overwrite then delete. (SD cards remap blocks, so overwriting is
        best effort; the file is useless without the PIN anyway.)"""
        for p in (self.path, self.tries_path):
            if os.path.exists(p):
                size = os.path.getsize(p)
                with open(p, "r+b") as f:
                    f.write(secrets.token_bytes(max(size, 1)))
                    f.flush()
                    os.fsync(f.fileno())
                os.remove(p)
