"""
Multisig wallets: wsh(sortedmulti(k, ...)) with BIP48 keys, the kind Sparrow,
Coldcard, SeedSigner, Keystone and Nunchuk all share.

This device contributes one key:  [fp/48'/coin'/0'/2']xpub
A wallet is described by one output descriptor, e.g.

  wsh(sortedmulti(2,[aaaa0001/48h/0h/0h/2h]xpub.../<0;1>/*,
                    [bbbb0002/48h/0h/0h/2h]xpub.../<0;1>/*,
                    [cccc0003/48h/0h/0h/2h]xpub.../<0;1>/*))
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from embit import bip32
from embit.descriptor import Descriptor
from embit.networks import NETWORKS as EMBIT_NETS

from .config import NETWORKS

H = 0x80000000


class PolicyError(Exception):
    pass


def multisig_path(network: str, account: int = 0) -> str:
    return f"m/48h/{NETWORKS[network][2]}h/{account}h/2h"


def cosigner_key(root: bip32.HDKey, network: str) -> str:
    """This device's key for multisig, with its origin: [fp/48h/c'h/0h/2h]xpub."""
    emb = EMBIT_NETS[NETWORKS[network][1]]
    path = multisig_path(network)
    xpub = root.derive(path).to_public().to_string(version=emb["xpub"])
    return f"[{root.my_fingerprint.hex()}/{path[2:]}]{xpub}"


_KEY_RE = re.compile(r"^\[([0-9a-fA-F]{8})((?:/\d+['hH]?)+)\]([1-9A-HJ-NP-Za-km-z]{100,120})$")


def parse_key(text: str, network: str) -> str:
    """Normalize a scanned cosigner key to [fp/path]xpub (network's xpub
    version, h for hardened). Accepts Zpub/Vpub-style keys too."""
    t = text.strip()
    m = _KEY_RE.match(t.split("/<")[0].split("/0/*")[0])
    if not m:
        raise PolicyError("not a cosigner key ([fingerprint/path]xpub)")
    fp, path, xkey = m.groups()
    path = re.sub(r"['H]", "h", path)
    try:
        k = bip32.HDKey.from_string(xkey)
    except Exception:
        raise PolicyError("the xpub is damaged")
    if k.is_private:
        raise PolicyError("that's a PRIVATE key: never share it")
    emb = EMBIT_NETS[NETWORKS[network][1]]
    want_main = network == "bitcoin"
    is_main = xkey[0] in "xyzYZ"
    if is_main != want_main:
        raise PolicyError("key is for a different network")
    return f"[{fp.lower()}{path}]{k.to_string(version=emb['xpub'])}"


def build_descriptor(k: int, keys: list[str]) -> str:
    if not 1 <= k <= len(keys) <= 15:
        raise PolicyError("need 1 <= k <= n <= 15")
    return f"wsh(sortedmulti({k},{','.join(key + '/<0;1>/*' for key in keys)}))"


@dataclass
class Policy:
    descriptor: str            # normalized, no checksum, <0;1> branches
    k: int
    n: int
    cosigners: list[str]       # fingerprints, in descriptor order (inherit: owner, heir)
    name: str = ""
    kind: str = "multisig"     # "multisig" | "inherit"
    delay: int = 0             # inherit: blocks before the heir can spend
    ws_len: int = 0            # inherit: witness script length

    @property
    def id(self) -> str:
        return hashlib.sha256(self.descriptor.encode()).hexdigest()[:8]

    @property
    def label(self) -> str:
        if self.name:
            return self.name
        return "Inheritance" if self.kind == "inherit" else f"{self.k}-of-{self.n}"

    def input_vbytes(self) -> float:
        if self.kind == "inherit":
            from .inherit import input_vbytes as iv
            return iv(self.ws_len)
        return input_vbytes(self.k, self.n)

    def desc(self) -> Descriptor:
        cached = getattr(self, "_desc", None)
        if cached is None:
            cached = Descriptor.from_string(self.descriptor)
            object.__setattr__(self, "_desc", cached)
        return cached

    def derive(self, branch: int, index: int) -> Descriptor:
        return self.desc().branch(branch).derive(index)

    def to_dict(self) -> dict:
        return {"descriptor": self.descriptor, "name": self.name}


def normalize_descriptor(text: str) -> str:
    t = "".join(text.split())                  # QR text may wrap
    t = t.split("#")[0]                         # drop checksum
    # hardened marks: ' or H -> h, only inside [origin] brackets (xpubs contain H)
    t = re.sub(r"\[[^\]]*\]", lambda m: m.group(0).replace("'", "h").replace("H", "h"), t)
    # Sparrow sometimes exports one branch per key (/0/*): use both branches
    t = re.sub(r"(\]?[1-9A-HJ-NP-Za-km-z]{100,120})/0/\*", r"\1/<0;1>/*", t)
    return t


def parse_policy(text: str, network: str, my_key: str | None = None, name: str = "") -> Policy:
    """Check a multisig descriptor: native SegWit sorted multisig, every key
    an xpub with origin, distinct signers, right network, and (if given)
    includes this device's key exactly once."""
    t = normalize_descriptor(text)
    m = re.match(r"^wsh\((sorted)?multi\((\d+),(.+)\)\)$", t)
    if not m:
        raise PolicyError("only wsh(sortedmulti(...)) wallets are supported")
    if not m.group(1):
        raise PolicyError("use sortedmulti (unsorted multi isn't supported)")
    k = int(m.group(2))
    raw_keys = m.group(3).split(",")
    keys, fps = [], []
    for rk in raw_keys:
        if not rk.endswith("/<0;1>/*"):
            raise PolicyError("each key needs /<0;1>/* (or /0/*)")
        keys.append(parse_key(rk[:-len("/<0;1>/*")], network))
        fps.append(keys[-1][1:9])
    if len(set(fps)) != len(fps):
        raise PolicyError("the same signer appears twice")
    desc = build_descriptor(k, keys)
    try:
        d = Descriptor.from_string(desc)
        d.branch(0).derive(0).script_pubkey()
    except Exception as e:
        raise PolicyError(f"descriptor doesn't work: {e}")
    if my_key is not None and keys.count(my_key) != 1:
        raise PolicyError("this device's key isn't in that wallet")
    return Policy(desc, k, len(keys), fps, name)


def witness_k_n(witness_script: bytes) -> tuple[int, int] | None:
    """(k, n) of a standard multisig witness script, else None."""
    if len(witness_script) < 37 or witness_script[-1] != 0xAE:
        return None
    k, n = witness_script[0] - 0x50, witness_script[-2] - 0x50
    if not (1 <= k <= n <= 16) or len(witness_script) != 3 + 34 * n:
        return None
    return k, n


def input_vbytes(k: int, n: int) -> float:
    """P2WSH k-of-n input: 41 non-witness bytes + witness/4."""
    witness = 1 + 1 + k * 73 + 1 + (3 + 34 * n)
    return 41 + witness / 4


def parse_any(text: str, network: str, my_key: str | None = None, name: str = "") -> Policy:
    """A multisig or an inheritance descriptor, whichever it is."""
    t = normalize_descriptor(text)
    if t.startswith("wsh(or_d("):
        from .inherit import parse
        return parse(t, network, my_key, name)
    return parse_policy(t, network, my_key, name)
