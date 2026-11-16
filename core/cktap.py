"""
Coinkite tap cards over NFC: SATSCARD (bearer bitcoin, sealed slots) and
TAPSIGNER (a key on a card, used here as a multisig cosigner).

Protocol per coinkite-tap-proto docs/protocol.md: ISO 7816 APDUs over
ISO-DEP. SELECT the applet, then every command is one APDU
(CLA 00, INS CB) carrying a CBOR map {"cmd": name, ...}; replies are CBOR.

Commands that need the card's CVC (the code printed on the back) prove it
without sending it in clear:
    ephemeral key e, card pubkey C
    session_key = SHA256(compressed point e*C)
    xcvc        = CVC XOR (session_key XOR SHA256(card_nonce || cmd))[:len(CVC)]
Secrets coming back (an unsealed private key) or going out (a digest to
sign) are XORed with session_key.

Everything a card says is checked where the protocol allows it: read and
check signatures against the card's keys, the certificate chain up to
Coinkite's factory root, an unsealed key against the slot's public key,
every TAPSIGNER signature against the key the PSBT expects.

The simulated cards at the bottom implement the card side of the same
formulas, for the simulator and tests. They prove this code agrees with
itself and with the document, not with real cards: confirm against a real
SATSCARD and TAPSIGNER (tools/hwtest.py nfc) before trusting coins to it.
"""
from __future__ import annotations

import hashlib
import os

from embit import bip32, ec
from embit.hashes import hash160
from embit.script import p2wpkh
from embit.util import secp256k1

from . import cbor

AID = bytes.fromhex("f0436f696e6b697465434152447631")          # b"\xf0CoinkiteCARDv1"
FACTORY_ROOT = bytes.fromhex("03028a0e89e70d0ec0d932053a89ab1da7d9182bdc6d2f03e706ee99517d05d9e1")
H = 0x80000000


class CardError(Exception):
    def __init__(self, msg: str, code: int = 0):
        super().__init__(msg)
        self.code = code


ERRORS = {205: "unlucky number, try again", 400: "bad arguments", 401: "wrong CVC",
          403: "not allowed now", 404: "unknown command", 406: "card setup needed",
          417: "bad nonce", 425: "needs a backup first", 429: "too many tries: wait"}


# ------------------------------------------------------------- crypto bits
def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


def sha256(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()


_P = 2 ** 256 - 2 ** 32 - 977
_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


def _decompress(pub33: bytes) -> tuple[int, int]:
    if len(pub33) != 33 or pub33[0] not in (2, 3):
        raise CardError("bad public key")
    x = int.from_bytes(pub33[1:], "big")
    y = pow((x * x * x + 7) % _P, (_P + 1) // 4, _P)
    if (y * y - x * x * x - 7) % _P:
        raise CardError("bad public key")
    if y & 1 != pub33[0] & 1:
        y = _P - y
    return x, y


def _mul(point: tuple[int, int], k: int) -> tuple[int, int]:
    """Scalar multiplication on secp256k1 (affine double-and-add; the
    scalar here is a throwaway ephemeral key, so timing doesn't matter)."""
    result = None
    addend = point
    while k:
        if k & 1:
            result = _add(result, addend)
        addend = _add(addend, addend)
        k >>= 1
    return result


def _add(a, b):
    if a is None:
        return b
    if b is None:
        return a
    (x1, y1), (x2, y2) = a, b
    if x1 == x2 and (y1 + y2) % _P == 0:
        return None
    if a == b:
        m = 3 * x1 * x1 * pow(2 * y1, -1, _P)
    else:
        m = (y2 - y1) * pow(x2 - x1, -1, _P)
    x3 = (m * m - x1 - x2) % _P
    return x3, (m * (x1 - x3) - y1) % _P


def ecdh(pub33: bytes, priv32: bytes) -> bytes:
    """SHA256 of the compressed shared point (libsecp256k1's default ECDH,
    which the cards use). Pure Python, so it works with either embit backend."""
    k = int.from_bytes(priv32, "big")
    if not 0 < k < _N:
        raise CardError("bad key")
    x, y = _mul(_decompress(pub33), k)
    return sha256(bytes([2 + (y & 1)]) + x.to_bytes(32, "big"))


def calc_xcvc(cvc: bytes, session_key: bytes, card_nonce: bytes, cmd: str) -> bytes:
    mask = _xor(session_key, sha256(card_nonce + cmd.encode()))[:len(cvc)]
    return _xor(cvc, mask)


def verify64(pub33: bytes, digest: bytes, sig64: bytes) -> bool:
    try:
        sig = secp256k1.ecdsa_signature_parse_compact(sig64)
        sig = secp256k1.ecdsa_signature_normalize(sig)
        return bool(secp256k1.ecdsa_verify(sig, digest, secp256k1.ec_pubkey_parse(pub33)))
    except Exception:  # noqa: BLE001
        return False


def recover65(digest: bytes, sig65: bytes) -> bytes:
    """Public key from a 65-byte recoverable signature (BIP137-style header)."""
    h = sig65[0]
    rec = h - 39 if 39 <= h <= 42 else h - 27 if 27 <= h <= 30 else -1
    if rec < 0:
        raise CardError("bad certificate signature")
    rs = secp256k1.ecdsa_recoverable_signature_parse_compact(sig65[1:], rec)
    return secp256k1.ec_pubkey_serialize(secp256k1.ecdsa_recover(rs, digest))


def der_sig(sig64: bytes) -> bytes:
    sig = secp256k1.ecdsa_signature_normalize(secp256k1.ecdsa_signature_parse_compact(sig64))
    return secp256k1.ecdsa_signature_serialize_der(sig)


# ------------------------------------------------------------------ client
class Card:
    """One tap card in the field. transmit(apdu) -> (data, sw)."""

    def __init__(self, transmit):
        self.transmit = transmit
        self.status: dict = {}
        self.card_nonce = b""
        self.pubkey = b""

    # ---------------------------------------------------------- transport
    def select(self) -> dict:
        data, sw = self.transmit(bytes([0x00, 0xA4, 0x04, 0x00, len(AID)]) + AID)
        if sw != 0x9000:
            raise CardError("not a Coinkite card")
        return self._took(data)

    def _took(self, data: bytes) -> dict:
        try:
            r = cbor.decode(data)
        except cbor.CBORError as e:
            raise CardError(f"garbled reply: {e}")
        if not isinstance(r, dict):
            raise CardError("garbled reply")
        if "error" in r:
            code = r.get("code", 0)
            raise CardError(ERRORS.get(code, str(r["error"])[:40]), code)
        if isinstance(r.get("card_nonce"), bytes) and len(r["card_nonce"]) == 16:
            self.card_nonce = r["card_nonce"]
        if "pubkey" in r and "proto" in r:                   # status
            self.status = r
            self.pubkey = r["pubkey"]
        return r

    def send(self, cmd: str, **args) -> dict:
        body = cbor.encode({"cmd": cmd, **args})
        if len(body) > 255:
            raise CardError("command too long")
        data, sw = self.transmit(bytes([0x00, 0xCB, 0x00, 0x00, len(body)]) + body)
        if sw != 0x9000 and not data:
            raise CardError(f"card said {sw:04x}")
        return self._took(data)

    def _auth(self, cmd: str, cvc: str) -> tuple[bytes, dict]:
        raw = cvc.encode()
        if not 6 <= len(raw) <= 32:
            raise CardError("the code on the card is 6 digits")
        e = os.urandom(32)
        session = ecdh(self.pubkey, e)
        epub = ec.PrivateKey(e).get_public_key().sec()
        return session, {"epubkey": epub, "xcvc": calc_xcvc(raw, session, self.card_nonce, cmd)}

    # ---------------------------------------------------------- general
    @property
    def kind(self) -> str:
        if self.status.get("tapsigner"):
            return "tapsigner"
        if self.status.get("satschip"):
            return "satschip"
        return "satscard"

    @property
    def testnet(self) -> bool:
        return bool(self.status.get("testnet"))

    def refresh(self) -> dict:
        return self.send("status")

    def wait(self) -> dict:
        return self.send("wait")

    def genuine(self, root: bytes = FACTORY_ROOT) -> bool:
        """The card holds the private key of a pubkey Coinkite's factory key
        certified (check + certs, per the protocol document)."""
        nonce = os.urandom(16)
        before = self.card_nonce
        chk = self.send("check", nonce=nonce)
        base = b"OPENDIME" + before + nonce
        candidates = [sha256(base)]
        if self.kind == "satscard" and self.slot_pubkey:
            candidates.append(sha256(base + self.slot_pubkey))
        if not any(verify64(self.pubkey, d, chk.get("auth_sig", b"")) for d in candidates):
            return False
        chain = self.send("certs").get("cert_chain", [])
        pub = self.pubkey
        for sig in chain[:8]:
            pub = recover65(sha256(pub), sig)
        return bool(chain) and pub == root

    # ---------------------------------------------------------- SATSCARD
    slot_pubkey: bytes = b""

    def slot(self) -> tuple[int, int]:
        slots = self.status.get("slots", [0, 0])
        if not (isinstance(slots, list) and len(slots) == 2
                and all(isinstance(x, int) and 0 <= x <= 255 for x in slots)):
            raise CardError("garbled reply")
        return slots[0], slots[1]

    def read_slot(self) -> bytes:
        """The current slot's public key, proven by the card's signature."""
        nonce = os.urandom(16)
        before = self.card_nonce
        r = self.send("read", nonce=nonce)
        cur, _ = self.slot()
        digest = sha256(b"OPENDIME" + before + nonce + bytes([cur]))
        if not verify64(r["pubkey"], digest, r.get("sig", b"")):
            raise CardError("the card's signature doesn't check out")
        self.slot_pubkey = r["pubkey"]
        return r["pubkey"]

    def address(self, embit_net: dict) -> str:
        """The current slot's deposit address, checked against the address
        the card itself reports (it shows the first and last 12 characters)."""
        pub = self.slot_pubkey or self.read_slot()
        addr = p2wpkh(ec.PublicKey.parse(pub)).address(embit_net)
        shown = self.status.get("addr", "")
        if shown and "___" in shown:
            head, tail = shown.split("___", 1)
            if not (addr.startswith(head) and addr.endswith(tail)):
                raise CardError("address doesn't match what the card reports")
        return addr

    def unseal(self, cvc: str) -> ec.PrivateKey:
        """Permanently opens the current slot and returns its private key."""
        cur, _ = self.slot()
        session, auth = self._auth("unseal", cvc)
        r = self.send("unseal", slot=cur, **auth)
        priv = _xor(r["privkey"], session)
        key = ec.PrivateKey(priv)
        if key.get_public_key().sec() != r.get("pubkey"):
            raise CardError("unsealed key doesn't match the slot")
        if self.slot_pubkey and self.slot_pubkey != r["pubkey"]:
            raise CardError("unsealed key doesn't match the slot")
        self.refresh()
        return key

    def new_slot(self, cvc: str) -> int:
        """Pick the next slot's key (needs the previous slot unsealed)."""
        cur, total = self.slot()
        session, auth = self._auth("new", cvc)
        r = self.send("new", slot=cur, chain_code=os.urandom(32), **auth)
        self.refresh()
        self.slot_pubkey = b""
        return r.get("slot", cur)

    def slot_used(self) -> str:
        """'sealed', 'unsealed' or 'unused' for the current slot."""
        cur, _ = self.slot()
        r = self.send("dump", slot=cur)
        if r.get("used") is False:
            return "unused"
        return "sealed" if r.get("sealed") else "unsealed"

    # --------------------------------------------------------- TAPSIGNER
    def derive(self, cvc: str, path: list[int]) -> bytes:
        nonce = os.urandom(16)
        session, auth = self._auth("derive", cvc)
        r = self.send("derive", path=list(path), nonce=nonce, **auth)
        self.refresh()
        return r.get("pubkey") or r["master_pubkey"]

    def xpub(self, cvc: str, master: bool = False) -> bip32.HDKey:
        session, auth = self._auth("xpub", cvc)
        r = self.send("xpub", master=master, **auth)
        try:
            return bip32.HDKey.parse(r["xpub"])
        except Exception:  # noqa: BLE001
            raise CardError("the card's xpub is damaged")

    def cosigner_key(self, cvc: str, network: str) -> str:
        """[xfp/48h/coin h/0h/2h]xpub: the card's multisig key, BIP48."""
        from embit.networks import NETWORKS as EMBIT_NETS
        from .config import NETWORKS
        coin = NETWORKS[network][2]
        path = [48 + H, coin + H, 0 + H, 2 + H]
        self.derive(cvc, path)
        master = self.xpub(cvc, master=True)
        acct = self.xpub(cvc, master=False)
        xfp = hash160(master.key.sec())[:4].hex()
        version = EMBIT_NETS[NETWORKS[network][1]]["xpub"]
        return f"[{xfp}/48h/{coin}h/0h/2h]{acct.to_string(version=version)}"

    def sign_digest(self, cvc: str, digest: bytes, subpath: list[int]) -> tuple[bytes, bytes]:
        """(64-byte signature, pubkey) for digest at the card's path/subpath."""
        for _ in range(5):
            session, auth = self._auth("sign", cvc)
            try:
                r = self.send("sign", subpath=list(subpath), digest=_xor(digest, session), **auth)
            except CardError as e:
                if e.code == 205:
                    continue                       # the protocol's "try again"
                raise
            if not verify64(r["pubkey"], digest, r["sig"]):
                raise CardError("the card's signature doesn't check out")
            return r["sig"], r["pubkey"]
        raise CardError("the card kept asking to retry")


# ======================================================= simulated cards
class SimCard:
    """The card side of the protocol, for the simulator and tests."""

    VERSION = "1.0.3"

    def __init__(self, cvc: str = "123456", testnet: bool = True, root_priv: bytes | None = None):
        self.cvc = cvc.encode()
        self.priv = os.urandom(32)
        self.pub = ec.PrivateKey(self.priv).get_public_key().sec()
        self.nonce = os.urandom(16)
        self.testnet = testnet
        self.selected = False
        self.fails = 0
        self.auth_delay = 0
        # certificate chain: root -> batch -> this card
        self.root_priv = root_priv or os.urandom(32)
        self.root_pub = ec.PrivateKey(self.root_priv).get_public_key().sec()
        batch = os.urandom(32)
        self.chain = [self._cert(batch, self.pub), self._cert(self.root_priv,
                      ec.PrivateKey(batch).get_public_key().sec())]

    @staticmethod
    def _cert(signer_priv: bytes, subject_pub: bytes) -> bytes:
        rs = secp256k1.ecdsa_sign_recoverable(sha256(subject_pub), signer_priv)
        raw, rec = secp256k1.ecdsa_recoverable_signature_serialize_compact(rs)
        return bytes([39 + rec]) + raw

    def _sign(self, priv: bytes, digest: bytes) -> bytes:
        sig = secp256k1.ecdsa_sign(digest, priv)
        return secp256k1.ecdsa_signature_serialize_compact(sig)

    # transport ----------------------------------------------------------
    def transmit(self, apdu: bytes) -> tuple[bytes, int]:
        if apdu[:4] == bytes([0x00, 0xA4, 0x04, 0x00]):
            if apdu[5:5 + apdu[4]] != AID:
                return b"", 0x6A82
            self.selected = True
            return cbor.encode(self.status()), 0x9000
        if apdu[:4] != bytes([0x00, 0xCB, 0x00, 0x00]):
            return b"", 0x6D00
        if not self.selected:
            return b"", 0x6D00
        req = cbor.decode(apdu[5:5 + apdu[4]])
        fn = getattr(self, "c_" + str(req.get("cmd")), None)
        if fn is None:
            return cbor.encode({"error": "unknown", "code": 404}), 0x9000
        try:
            out = fn(req)
        except _Err as e:
            return cbor.encode({"error": e.args[0], "code": e.code}), 0x9000
        self.nonce = os.urandom(16)
        out["card_nonce"] = self.nonce
        return cbor.encode(out), 0x9000

    def _check_auth(self, req, cmd) -> bytes:
        if self.auth_delay:
            raise _Err("wait", 429)
        try:
            session = ecdh(req["epubkey"], self.priv)
        except Exception:  # noqa: BLE001
            raise _Err("bad epubkey", 400)
        if calc_xcvc(self.cvc, session, self.nonce, cmd) != req.get("xcvc"):
            self.fails += 1
            if self.fails >= 3:
                self.auth_delay = 15
            raise _Err("bad auth", 401)
        self.fails = 0
        return session

    def status(self) -> dict:
        s = {"proto": 1, "ver": self.VERSION, "birth": 700000, "pubkey": self.pub,
             "card_nonce": self.nonce}
        if self.testnet:
            s["testnet"] = True
        if self.auth_delay:
            s["auth_delay"] = self.auth_delay
        return s

    def c_status(self, req):
        return self.status()

    def c_wait(self, req):
        self.auth_delay = max(0, self.auth_delay - 1)
        return {"success": True, "auth_delay": self.auth_delay}

    def c_check(self, req):
        msg = b"OPENDIME" + self.nonce + req["nonce"] + self._check_extra()
        return {"auth_sig": self._sign(self.priv, sha256(msg))}

    def _check_extra(self) -> bytes:
        return b""

    def c_certs(self, req):
        return {"cert_chain": self.chain}


class _Err(Exception):
    def __init__(self, msg, code):
        super().__init__(msg)
        self.code = code


class SimSatscard(SimCard):
    def __init__(self, slots: int = 10, **kw):
        super().__init__(**kw)
        self.slots = [{"state": "unused"} for _ in range(slots)]
        self.cur = 0
        self._setup(os.urandom(32))

    def _setup(self, chain_code: bytes):
        master = os.urandom(32)
        k = bip32.HDKey(ec.PrivateKey(master), chain_code).derive([0])
        self.slots[self.cur] = {"state": "sealed", "master": master, "cc": chain_code,
                                "priv": k.key.secret, "pub": k.key.get_public_key().sec()}

    def _addr(self, pub: bytes) -> str:
        from embit.networks import NETWORKS
        return p2wpkh(ec.PublicKey.parse(pub)).address(NETWORKS["test" if self.testnet
                                                                else "main"])

    def status(self):
        s = super().status()
        s["slots"] = [self.cur, len(self.slots)]
        sl = self.slots[self.cur]
        if sl["state"] == "sealed":
            a = self._addr(sl["pub"])
            s["addr"] = a[:12] + "___" + a[-12:]
        return s

    def _check_extra(self) -> bytes:
        sl = self.slots[self.cur]
        return sl["pub"] if sl["state"] == "sealed" else b""

    def c_read(self, req):
        sl = self.slots[self.cur]
        if sl["state"] != "sealed":
            raise _Err("not sealed", 406)
        msg = b"OPENDIME" + self.nonce + req["nonce"] + bytes([self.cur])
        return {"sig": self._sign(sl["priv"], sha256(msg)), "pubkey": sl["pub"]}

    def c_unseal(self, req):
        session = self._check_auth(req, "unseal")
        if req.get("slot") != self.cur or self.slots[self.cur]["state"] != "sealed":
            raise _Err("bad slot", 400)
        sl = self.slots[self.cur]
        sl["state"] = "unsealed"
        return {"slot": self.cur, "privkey": _xor(sl["priv"], session), "pubkey": sl["pub"],
                "master_pk": sl["master"], "chain_code": sl["cc"]}

    def c_dump(self, req):
        sl = self.slots[req.get("slot", self.cur)]
        if sl["state"] == "unused":
            return {"slot": req.get("slot"), "used": False}
        return {"slot": req.get("slot"), "sealed": sl["state"] == "sealed"}

    def c_new(self, req):
        self._check_auth(req, "new")
        if self.slots[self.cur]["state"] != "unsealed":
            raise _Err("current slot still sealed", 403)
        if self.cur + 1 >= len(self.slots):
            raise _Err("no slots left", 406)
        self.cur += 1
        self._setup(req.get("chain_code") or os.urandom(32))
        return {"slot": self.cur}


class SimTapsigner(SimCard):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.root = bip32.HDKey.from_seed(os.urandom(32))
        self.path = [84 + H, 0 + H, 0 + H]
        self.signed = 0

    def status(self):
        s = super().status()
        s.update({"tapsigner": True, "path": list(self.path), "num_backups": 1})
        return s

    def c_derive(self, req):
        self._check_auth(req, "derive")
        path = req.get("path", self.path)
        if any(not (p & H) for p in path) or len(path) > 8:
            raise _Err("hardened only", 400)
        self.path = list(path)
        k = self.root.derive(self.path)
        msg = b"OPENDIME" + self.nonce + req["nonce"] + k.chain_code
        return {"sig": self._sign(k.key.secret, sha256(msg)), "chain_code": k.chain_code,
                "master_pubkey": self.root.key.get_public_key().sec(),
                "pubkey": k.key.get_public_key().sec()}

    def c_xpub(self, req):
        self._check_auth(req, "xpub")
        k = self.root if req.get("master") else self.root.derive(self.path)
        return {"xpub": k.to_public().serialize()}

    def c_sign(self, req):
        session = self._check_auth(req, "sign")
        sub = req.get("subpath", [])
        if len(sub) > 2 or any(p & H for p in sub):
            raise _Err("bad subpath", 400)
        k = self.root.derive(self.path + list(sub))
        digest = _xor(req["digest"], session)
        self.signed += 1
        return {"slot": 0, "sig": self._sign(k.key.secret, digest),
                "pubkey": k.key.get_public_key().sec()}


# =========================================================== TAPSIGNER + PSBT
def multisig_path(network: str) -> list[int]:
    from .config import NETWORKS
    return [48 + H, NETWORKS[network][2] + H, 0 + H, 2 + H]


def sign_psbt(card: Card, cvc: str, psbt_b64: str, network: str) -> tuple[str, int]:
    """Add the TAPSIGNER's signatures to a multisig PSBT: every input with a
    key of this card (its fingerprint, BIP48 path) gets one, checked against
    the key the PSBT names. Returns (psbt, signatures added)."""
    from embit.psbt import PSBT
    path = multisig_path(network)
    if list(card.status.get("path", [])) != path:
        card.derive(cvc, path)
    xfp = hash160(card.xpub(cvc, master=True).key.sec())[:4]
    psbt = PSBT.from_string(psbt_b64)
    added = 0
    for i, inp in enumerate(psbt.inputs):
        for pub, der in inp.bip32_derivations.items():
            d = list(der.derivation)
            if der.fingerprint != xfp or d[:4] != path or len(d) != 6 or pub in inp.partial_sigs:
                continue
            if inp.witness_script is None:
                raise CardError("input isn't a multisig input")
            digest = psbt.sighash(i)
            sig, got = card.sign_digest(cvc, digest, d[4:])
            if got != pub.sec():
                raise CardError("the card signed with a different key than the PSBT names")
            inp.partial_sigs[pub] = der_sig(sig) + b"\x01"
            added += 1
    if not added:
        raise CardError("this card isn't a signer of that transaction")
    return psbt.to_string(), added
