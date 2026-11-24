"""
Multisig wallets: wsh(sortedmulti(k, ...)) with BIP48 keys, the kind Sparrow,
Coldcard, SeedSigner, Keystone and Nunchuk all share; and Taproot multisig,
tr(NUMS, sortedmulti_a(k, ...)): one script leaf, no key path (the internal
key is BIP341's provably unspendable point), keys at m/48'/coin'/0'/3'.

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


# BIP341's "nothing up my sleeve" point: no one knows its private key, so a
# Taproot output with it as the internal key can only be spent by a script.
NUMS = "50929b74c1a04954b78b4b6035e97a5e078a5a0f28ec96d547bfee9ace803ac0"


def multisig_path(network: str, account: int = 0, taproot: bool = False) -> str:
    return f"m/48h/{NETWORKS[network][2]}h/{account}h/{3 if taproot else 2}h"


def my_keys(root: bip32.HDKey, network: str) -> tuple[str, str]:
    """This device's multisig keys: (SegWit /2h, Taproot /3h)."""
    return cosigner_key(root, network), cosigner_key(root, network, taproot=True)


def cosigner_key(root: bip32.HDKey, network: str, taproot: bool = False) -> str:
    """This device's key for multisig, with its origin: [fp/48h/c'h/0h/2h]xpub
    (3h for Taproot multisig)."""
    emb = EMBIT_NETS[NETWORKS[network][1]]
    path = multisig_path(network, taproot=taproot)
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


def build_descriptor(k: int, keys: list[str], taproot: bool = False) -> str:
    if not 1 <= k <= len(keys) <= (20 if taproot else 15):
        raise PolicyError(f"need 1 <= k <= n <= {20 if taproot else 15}")
    body = ",".join(key + "/<0;1>/*" for key in keys)
    if taproot:
        return f"tr({NUMS},sortedmulti_a({k},{body}))"
    return f"wsh(sortedmulti({k},{body}))"


@dataclass
class Policy:
    descriptor: str            # normalized, no checksum, <0;1> branches
    k: int
    n: int
    cosigners: list[str]       # fingerprints, in descriptor order (inherit: owner, heir)
    name: str = ""
    kind: str = "multisig"     # "multisig" | "inherit" | "miniscript"
    delay: int = 0             # inherit: blocks before the heir can spend
    ws_len: int = 0            # inherit: witness script length
    taproot: bool = False      # tr(NUMS, sortedmulti_a(...))

    @property
    def id(self) -> str:
        return hashlib.sha256(self.descriptor.encode()).hexdigest()[:8]

    @property
    def label(self) -> str:
        if self.name:
            return self.name
        if self.kind == "inherit":
            return "Inheritance"
        if self.kind == "miniscript":
            return "Miniscript"
        return f"{self.k}-of-{self.n}" + (" Taproot" if self.taproot else "")

    def input_vbytes(self) -> float:
        if self.kind == "inherit":
            from .inherit import input_vbytes as iv
            return iv(self.ws_len)
        if self.kind == "miniscript":
            return miniscript_vbytes(self.desc())
        if self.taproot:
            return tr_input_vbytes(self.k, self.n)
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


def _mine(keys: list[str], my_key) -> bool:
    mine = [my_key] if isinstance(my_key, str) else list(my_key)
    return sum(keys.count(k) for k in mine) == 1


def parse_policy(text: str, network: str, my_key=None, name: str = "") -> Policy:
    """Check a multisig descriptor: native SegWit or Taproot sorted multisig,
    every key an xpub with origin, distinct signers, right network, and (if
    given) includes this device's key (one of my_keys()) exactly once."""
    t = normalize_descriptor(text)
    taproot = t.startswith("tr(")
    if taproot:
        m = re.match(r"^tr\(([0-9a-fA-F]{64}),sortedmulti_a\((\d+),(.+)\)\)$", t)
        if not m:
            raise PolicyError("Taproot multisig: tr(NUMS,sortedmulti_a(k,...)) only")
        if m.group(1).lower() != NUMS:
            raise PolicyError("its key path must be unspendable (the NUMS point)")
        k, raw = int(m.group(2)), m.group(3)
    else:
        m = re.match(r"^wsh\((sorted)?multi\((\d+),(.+)\)\)$", t)
        if not m:
            raise PolicyError("only wsh(sortedmulti(...)) and tr(NUMS,sortedmulti_a(...))")
        if not m.group(1):
            raise PolicyError("use sortedmulti (unsorted multi isn't supported)")
        k, raw = int(m.group(2)), m.group(3)
    keys, fps = [], []
    for rk in raw.split(","):
        if not rk.endswith("/<0;1>/*"):
            raise PolicyError("each key needs /<0;1>/* (or /0/*)")
        keys.append(parse_key(rk[:-len("/<0;1>/*")], network))
        fps.append(keys[-1][1:9])
    if len(set(fps)) != len(fps):
        raise PolicyError("the same signer appears twice")
    desc = build_descriptor(k, keys, taproot)
    try:
        d = Descriptor.from_string(desc)
        d.branch(0).derive(0).script_pubkey()
    except Exception as e:
        raise PolicyError(f"descriptor doesn't work: {e}")
    if my_key is not None and not _mine(keys, my_key):
        raise PolicyError("this device's key isn't in that wallet")
    return Policy(desc, k, len(keys), fps, name, taproot=taproot)


def tr_input_vbytes(k: int, n: int) -> float:
    """tr(NUMS, sortedmulti_a) script-path input: 41 bytes + witness/4.
    Witness: k signatures (65), n-k empty pushes, the leaf script
    (n keys of 34 bytes, k and OP_NUMEQUAL), a 33-byte control block."""
    witness = 1 + k * 65 + (n - k) * 1 + (3 + 34 * n + 2) + (1 + 33)
    return 41 + witness / 4


def tap_leaves(derived: Descriptor) -> list[tuple[bytes, int, bytes]]:
    """(leaf script, leaf version, control block) of every leaf of a derived
    tr(...) descriptor: what a PSBT's taproot_scripts may hold for it."""
    from embit.descriptor.taptree import _tweak_helper
    internal = derived.key.get_public_key()
    leaves, h = _tweak_helper(derived.taptree)
    parity = derived.key.get_public_key().taproot_tweak(h).sec()[0] & 1
    out = []
    for leaf, path in leaves:
        ser = leaf.serialize()                   # version byte + compact-size script
        from embit.script import Script
        script = Script.parse(ser[1:]).data
        out.append((script, leaf.version, bytes([leaf.version | parity]) + internal.xonly() + path))
    assert internal is not None
    return out


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


def parse_any(text: str, network: str, my_key=None, name: str = "", root=None) -> Policy:
    """A multisig, an inheritance or any other miniscript descriptor,
    whichever it is (the most specific reading wins)."""
    t = normalize_descriptor(text)
    if t.startswith("wsh(or_d("):
        from .inherit import _RE, parse
        if _RE.match(t):
            return parse(t, network, my_key, name)
    if re.match(r"^wsh\((sorted)?multi\(", t) or re.match(r"^tr\([0-9a-fA-F]{64},sortedmulti_a\(", t):
        return parse_policy(t, network, my_key, name)
    return parse_miniscript(t, network, root, name)


MAX_MINISCRIPT_KEYS = 20


def parse_miniscript(text: str, network: str, root=None, name: str = "") -> Policy:
    """Any miniscript wallet: wsh(<miniscript>) or tr(<key>,<tree of
    miniscript leaves>) (Liana, Coldcard, Bitcoin Core). Every key an xpub
    with its origin and /<0;1>/*, each key once, right network, no private
    keys; and (if root is given) this device's key in it, checked by
    deriving it. The device signs for it; another wallet (the one that
    made it) puts the final transaction together."""
    t = normalize_descriptor(text)
    if not (t.startswith("wsh(") or t.startswith("tr(")):
        raise PolicyError("only wsh(...) and tr(...) wallets")
    try:
        d = Descriptor.from_string(t)
        d.branch(0).derive(0).script_pubkey()
        d.branch(1).derive(0).script_pubkey()
    except Exception as e:
        raise PolicyError(f"not a miniscript this wallet understands: {str(e)[:60]}")
    keys = list(d.keys)
    internal_nums = d.taproot and not d.key.is_extended
    if internal_nums:
        if d.key.to_string().lower() != NUMS:
            raise PolicyError("a Taproot internal key must be an xpub or the NUMS point")
        keys = list(d.taptree.keys)
    if not keys or len(keys) > MAX_MINISCRIPT_KEYS:
        raise PolicyError(f"1 to {MAX_MINISCRIPT_KEYS} keys")
    seen, fps = set(), []
    for k in keys:
        s = str(k)
        if k.is_private:
            raise PolicyError("it holds a PRIVATE key: never share those")
        if not k.is_extended or k.origin is None or not s.endswith("/<0;1>/*"):
            raise PolicyError("each key: [fingerprint/path]xpub/<0;1>/*")
        parse_key(s[:-len("/<0;1>/*")], network)
        if s in seen:
            raise PolicyError("the same key appears twice")
        seen.add(s)
        fps.append(k.origin.fingerprint.hex())
    if root is not None and not my_origins(t, root):
        raise PolicyError("this device's key isn't in that wallet")
    return Policy(t, 0, len(keys), fps, name, kind="miniscript", taproot=d.taproot)


def my_origins(descriptor: str, root) -> list[list[int]]:
    """The origin paths at which this device's key appears in a wallet
    (checked by deriving, not just by fingerprint)."""
    out = []
    d = Descriptor.from_string(descriptor)
    for k in d.keys:
        if not k.is_extended or k.origin is None or k.origin.fingerprint != root.my_fingerprint:
            continue
        path = list(k.origin.derivation)
        if root.derive(path).to_public().key.sec() == k.key.key.sec():
            out.append(path)
    return out


def miniscript_vbytes(desc: Descriptor) -> float:
    """A generous input size for a miniscript wallet (fees never fall short):
    the whole script (or the biggest leaf, its control block and the tree's
    depth) plus a signature for every key."""
    n = max(1, len(desc.keys))
    if desc.taproot and desc.taptree:
        leaves = tap_leaves(desc.branch(0).derive(0))
        biggest = max(len(sc) + len(cb) for sc, _v, cb in leaves)
        return 41 + (biggest + 66 * n + 8) / 4
    if desc.taproot:
        return 57.5
    return 41 + (len(desc.branch(0).derive(0).witness_script().data) + 74 * n + 8) / 4
