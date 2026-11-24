"""
Wallet service: a watch-only wallet in pure Python (embit + a small
Electrum client). It knows public keys only, tracks coins, builds unsigned
PSBTs and broadcasts signed ones, so it's the part allowed on the network.

Pure Python on purpose: BDK's Python package has no ARM64 Linux builds, and
nothing here needs compiling on the CM0.

All state is derived from one thing: the set of transactions that touch our
addresses. Coins, balance and history are recomputed from it, which keeps
the logic small and hard to get subtly wrong.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from embit.descriptor import Descriptor
from embit.networks import NETWORKS as EMBIT_NETS
from embit.psbt import PSBT, DerivationPath
from embit.script import Script, Witness, address_to_scriptpubkey, p2tr, p2wpkh
from embit.transaction import Transaction, TransactionInput, TransactionOutput

from .config import APP_DIR, NETWORKS, Settings
from .electrum import local_host, ElectrumClient, ElectrumError, scripthash

DUST = 546
RBF_SEQUENCE = 0xFFFFFFFD


@dataclass
class Tx:
    txid: str
    delta: int          # sats in (+) or out (-) of this wallet
    fee: int | None
    confirmed: bool
    height: int | None
    timestamp: int | None


@dataclass
class Coin:
    outpoint: str
    sats: int
    confirmed: bool
    address: str
    change: bool


@dataclass
class SignedTx:
    txid: str
    raw: bytes

    # same names the UI used with BDK transactions
    def compute_txid(self) -> str:
        return self.txid

    def serialize(self) -> bytes:
        return self.raw


class WalletError(Exception):
    pass


def vsize(n_in: int, n_out: int) -> float:
    return 10.5 + 68.0 * n_in + 31.0 * n_out


class WalletService:
    def __init__(self, settings: Settings, descriptor: str, change_descriptor: str | None,
                 fingerprint: str, data_dir: str = APP_DIR, in_memory: bool = False,
                 role: str | None = None):
        """Single-sig: receive + change descriptors, BIP84 wpkh(...) or
        BIP86 tr(...) (key-path Taproot).
        Multisig: one wsh(sortedmulti(...<0;1>/*)) descriptor, change_descriptor=None.
        Inheritance: one wsh(or_d(...)) descriptor, change_descriptor=None,
        role "owner" or "heir" (which key is this device's)."""
        self.settings = settings
        self.network = settings.network
        self.net = EMBIT_NETS[NETWORKS[settings.network][1]]
        desc = Descriptor.from_string(descriptor)
        self.kind = "single"
        self.taproot = False
        self.role = role
        self.delay = 0
        if change_descriptor is None and descriptor.startswith("wsh(or_d("):
            from .inherit import _RE, input_vbytes as inherit_vb
            self._branches = [desc.branch(0), desc.branch(1)]
            self.multisig = True                 # script-based: same plumbing
            self.kind = "inherit"
            self.delay = int(_RE.match(descriptor).group(3))
            self.k, self.n = 1, 2
            self._in_vb = inherit_vb(len(self._branches[0].derive(0).witness_script().data))
            if role not in ("owner", "heir"):
                raise WalletError("inheritance wallet needs a role")
        elif change_descriptor is None:          # multisig
            from .multisig import input_vbytes, witness_k_n
            self._branches = [desc.branch(0), desc.branch(1)]
            self.multisig = True
            self.kind = "multisig"
            k, n = witness_k_n(self._branches[0].derive(0).witness_script().data)
            self.k, self.n = k, n
            self._in_vb = input_vbytes(k, n)
        else:
            self._key = desc.keys[0]             # account xpub with origin
            Descriptor.from_string(change_descriptor)   # validate
            self.multisig = False
            self.taproot = descriptor.startswith("tr(")
            if not (self.taproot or descriptor.startswith("wpkh(")):
                raise WalletError("only wpkh(...) and tr(...) single-sig descriptors")
            self.k, self.n = 1, 1
            self._in_vb = 57.5 if self.taproot else 68.0
        self.pending_note = ""
        # The history file is encrypted with a key derived from the public
        # descriptor, which only exists after unlocking. A copied SD card shows
        # neither balances, addresses nor how many wallets this device holds.
        tag = hashlib.sha256(b"czwallet-db-v1" + descriptor.encode()).digest()
        self._db_key = hashlib.sha256(b"key" + tag).digest()
        self._path = None if in_memory else os.path.join(data_dir, f"w-{tag[:8].hex()}.db")
        self._lock = threading.RLock()
        self._client: ElectrumClient | None = None
        self.tip_height: int | None = None
        self.last_sync: float | None = None
        self._scripts: dict[str, tuple[int, int]] = {}    # script hex -> (branch, index)
        self._script_cache: dict[tuple[int, int], Script] = {}
        self._derived = [0, 0]
        self._parsed: dict[str, Transaction] = {}
        self.state = {"txs": {}, "current": 0, "times": {}}
        if self._path and os.path.exists(self._path):
            with open(self._path, "rb") as f:
                blob = f.read()
            try:
                self.state.update(json.loads(AESGCM(self._db_key).decrypt(blob[:12], blob[12:], None)))
            except Exception:
                pass                              # unreadable: start over and resync
        self.tip_height = self.state.get("tip")   # last known, for timelocks offline
        self._derive_to(0, 40)
        self._derive_to(1, 40)

    # --------------------------------------------------------------- keys
    def _child(self, branch: int, i: int):
        pub = self._key.key.derive([branch, i]).key
        path = list(self._key.origin.derivation) + [branch, i]
        return pub, path

    def _keys_at(self, branch: int, i: int) -> tuple[list, Script | None]:
        """[(pubkey, fingerprint, path)] for every key of this address, and
        the witness script (multisig only)."""
        if not self.multisig:
            pub, path = self._child(branch, i)
            return [(pub, self._key.origin.fingerprint, path)], None
        d = self._branches[branch].derive(i)
        return ([(k.get_public_key(), k.origin.fingerprint, list(k.origin.derivation))
                 for k in d.keys], d.witness_script())

    def _make_script(self, branch: int, i: int) -> Script:
        if self.multisig:
            return self._branches[branch].derive(i).script_pubkey()
        pub = self._child(branch, i)[0]
        return p2tr(pub) if self.taproot else p2wpkh(pub)

    def _derive_to(self, branch: int, n: int):
        while self._derived[branch] < n:
            i = self._derived[branch]
            sc = self._make_script(branch, i)
            self._scripts[sc.data.hex()] = (branch, i)
            self._script_cache[(branch, i)] = sc
            self._derived[branch] += 1

    def _script(self, branch: int, i: int) -> Script:
        self._derive_to(branch, i + 1)
        return self._script_cache[(branch, i)]

    def _vsize(self, n_in: int, n_out: int) -> float:
        # outputs counted at 43 vB (P2WSH/P2TR size) so the fee never falls short
        return 10.5 + self._in_vb * n_in + 43.0 * n_out

    def _addr(self, script: Script) -> str:
        return script.address(self.net)

    # ------------------------------------------------------------ storage
    def _save(self):
        if not self._path:
            return
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        nonce = os.urandom(12)
        blob = nonce + AESGCM(self._db_key).encrypt(nonce, json.dumps(self.state).encode(), None)
        tmp = self._path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(blob)
        os.replace(tmp, self._path)

    def _tx(self, txid: str) -> Transaction:
        if txid not in self._parsed:
            self._parsed[txid] = Transaction.from_string(self.state["txs"][txid]["hex"])
        return self._parsed[txid]

    def _used(self) -> set[str]:
        used = set()
        for txid in self.state["txs"]:
            for out in self._tx(txid).vout:
                h = out.script_pubkey.data.hex()
                if h in self._scripts:
                    used.add(h)
        return used

    # ------------------------------------------------------------ network
    def connect(self) -> None:
        proxy = self.settings.tor_proxy if self.settings.use_tor else None
        host = self.settings.server.split("://")[-1].rsplit(":", 1)[0]
        if proxy and local_host(host):
            proxy = None            # your own node at home: Tor can't reach the LAN
        # On the device the app has no IP networking: everything goes through
        # the cznet relay (core/netd.py). Without CZ_NET_SOCK (simulator,
        # tests) it connects directly.
        if os.environ.get("CZ_NET_SOCK"):
            from .netd import RelayClient as Client
        else:
            Client = ElectrumClient
        try:
            c = Client(self.settings.server, socks5=proxy).connect()
            self.tip_height = c.call("blockchain.headers.subscribe")["height"]
        except (OSError, ElectrumError, ValueError) as e:
            self._client = None
            raise WalletError(f"can't reach server: {e}") from e
        self._client = c

    def disconnect(self) -> None:
        if self._client:
            self._client.close()
        self._client = None

    @property
    def connected(self) -> bool:
        return self._client is not None

    def sync(self, full: bool | None = None) -> None:
        if self._client is None:
            self.connect()
        try:
            self._sync()
        except (OSError, ElectrumError) as e:
            self.disconnect()
            raise WalletError(f"sync failed: {e}") from e
        self.last_sync = time.time()

    def _sync(self):
        c = self._client
        gap = self.settings.stop_gap
        found: dict[str, int] = {}
        for branch in (0, 1):
            i, unused = 0, 0
            must_reach = self.state["current"] + 1 if branch == 0 else 0
            while unused < gap or i < must_reach:
                batch = list(range(i, i + 10))
                hashes = [scripthash(self._script(branch, k).data) for k in batch]
                hists = c.call_many([("blockchain.scripthash.get_history", [h]) for h in hashes])
                for hist in hists:
                    if hist:
                        unused = 0
                        for item in hist:
                            found[item["tx_hash"]] = item["height"]
                    else:
                        unused += 1
                i += 10

        with self._lock:
            txs = self.state["txs"]
            missing = [t for t in found if t not in txs]
        raws = c.call_many([("blockchain.transaction.get", [t]) for t in missing]) if missing else []

        with self._lock:
            now = int(time.time())
            for t, raw in zip(missing, raws):
                txs[t] = {"hex": raw, "height": found[t], "seen": now}
            for t, h in found.items():
                txs[t]["height"] = h
                txs[t].pop("local", None)
            # Drop what the server no longer knows (replaced, evicted, reorged),
            # but keep our own not-yet-broadcast transactions unless conflicted.
            server_spent = {(i.txid.hex(), i.vout) for t in found for i in self._tx(t).vin}
            for t in list(txs):
                if t in found:
                    continue
                if txs[t].get("local") and not any(
                        (i.txid.hex(), i.vout) in server_spent for i in self._tx(t).vin):
                    continue
                del txs[t]
                self._parsed.pop(t, None)

        heights = sorted({h for h in found.values() if h > 0 and str(h) not in self.state["times"]})
        if heights:
            headers = c.call_many([("blockchain.block.header", [h]) for h in heights])
            for h, hdr in zip(heights, headers):
                self.state["times"][str(h)] = int.from_bytes(bytes.fromhex(hdr)[68:72], "little")
        self.tip_height = c.call("blockchain.headers.subscribe")["height"]
        with self._lock:
            self.state["tip"] = self.tip_height
            self._save()

    def fee_estimates(self) -> dict[str, int]:
        """sat/vB for fast (next block), normal (~30 min), slow (~1 h)."""
        out = {"fast": 10, "normal": 5, "slow": 2}
        if self._client is None:
            return out
        try:
            res = self._client.call_many([("blockchain.estimatefee", [n]) for n in (1, 3, 6)])
        except (OSError, ElectrumError):
            return out
        for name, btc_kb in zip(out, res):
            if btc_kb and btc_kb > 0:
                out[name] = max(1, math.ceil(btc_kb * 1e8 / 1000))
        return out

    # -------------------------------------------------------------- reads
    def _utxos(self) -> list[dict]:
        with self._lock:
            txs = self.state["txs"]
            spent = {(i.txid.hex(), i.vout) for t in txs for i in self._tx(t).vin}
            out = []
            for t, rec in txs.items():
                for n, o in enumerate(self._tx(t).vout):
                    key = o.script_pubkey.data.hex()
                    if key in self._scripts and (t, n) not in spent:
                        branch, idx = self._scripts[key]
                        out.append({"txid": t, "vout": n, "value": o.value,
                                    "script": o.script_pubkey, "branch": branch, "index": idx,
                                    "height": rec["height"]})
            return out

    # ------------------------------------------------- someone else's coins
    def foreign_utxos(self, script: Script) -> list[dict]:
        """Unspent outputs paying a script that isn't ours (a SATSCARD slot),
        from the server's history of it. Needs the network."""
        if self._client is None:
            self.connect()
        sh = scripthash(script.data)
        try:
            hist = self._client.call("blockchain.scripthash.get_history", sh)
            txs = {h["tx_hash"]: (Transaction.from_string(
                self._client.call("blockchain.transaction.get", h["tx_hash"])), h["height"])
                for h in hist[:50]}
        except (OSError, ElectrumError, KeyError, TypeError) as e:
            raise WalletError(f"couldn't look up that address: {e}") from e
        spent = {(i.txid.hex(), i.vout) for tx, _ in txs.values() for i in tx.vin}
        out = []
        for txid, (tx, height) in txs.items():
            if tx.txid().hex() != txid:
                raise WalletError("the server sent a transaction that doesn't match its id")
            for n, o in enumerate(tx.vout):
                if o.script_pubkey.data == script.data and (txid, n) not in spent:
                    out.append({"txid": txid, "vout": n, "value": o.value, "height": height})
        return out

    def sweep(self, key, utxos: list[dict], fee_rate: int) -> tuple[SignedTx, int, str]:
        """Spend every coin of a single key (P2WPKH) to our next receive
        address. Returns (signed tx, fee, address)."""
        from embit.script import p2pkh
        pub = key.get_public_key()
        src = p2wpkh(pub)
        total = sum(u["value"] for u in utxos)
        fee = math.ceil(fee_rate * (10.5 + 68.0 * len(utxos) + 43.0))
        if total - fee < DUST:
            raise WalletError("not enough in it to pay the fee")
        _, addr = self.receive_address()
        tx = Transaction(version=2,
                         vin=[TransactionInput(bytes.fromhex(u["txid"]), u["vout"],
                                               sequence=RBF_SEQUENCE) for u in utxos],
                         vout=[TransactionOutput(total - fee, address_to_scriptpubkey(addr))])
        code = p2pkh(pub)
        for i, u in enumerate(utxos):
            h = tx.sighash_segwit(i, code, u["value"])
            tx.vin[i].witness = Witness([key.sign(h).serialize() + b"\x01", pub.sec()])
        del src
        return SignedTx(tx.txid().hex(), tx.serialize()), fee, addr

    # ---------------------------------------------------- inheritance timer
    def blocks_until_unlock(self, height: int) -> int | None:
        """For a coin confirmed at `height`: blocks until the heir can spend
        it (0 = now). None if unconfirmed or the chain height is unknown."""
        if height <= 0 or not self.tip_height:
            return None
        confs = self.tip_height - height + 1
        return max(0, self.delay - confs)

    def _spendable(self, utxos: list[dict]) -> list[dict]:
        if self.kind != "inherit" or self.role != "heir":
            return utxos
        return [u for u in utxos if self.blocks_until_unlock(u["height"]) == 0]

    def timer(self) -> dict:
        """Inheritance status: {unlocked sats, locked sats, next_unlock blocks,
        soonest: blocks until the first coin unlocks for the heir}."""
        u = self._utxos()
        left = [(self.blocks_until_unlock(x["height"]), x["value"]) for x in u]
        unlocked = sum(v for b, v in left if b == 0)
        waiting = [b for b, _ in left if b]
        return {"unlocked": unlocked, "locked": sum(v for _, v in left) - unlocked,
                "soonest": min(waiting) if waiting else None,
                "unknown": any(b is None for b, _ in left)}

    def balance(self) -> dict[str, int]:
        u = self._utxos()
        conf = sum(x["value"] for x in u if x["height"] > 0)
        pend = sum(x["value"] for x in u if x["height"] <= 0)
        spend = sum(x["value"] for x in self._spendable(u))
        return {"confirmed": conf, "pending": pend, "spendable": spend,
                "total": conf + pend}

    def receive_address(self) -> tuple[int, str]:
        """Keeps showing the same address until it receives coins."""
        with self._lock:
            used = self._used()
            i = self.state["current"]
            while self._script(0, i).data.hex() in used:
                i += 1
            if i != self.state["current"]:
                self.state["current"] = i
                self._save()
            return i, self._addr(self._script(0, i))

    def new_receive_address(self) -> tuple[int, str]:
        with self._lock:
            used = self._used()
            i = self.state["current"] + 1
            while self._script(0, i).data.hex() in used:
                i += 1
            if i - self._first_unused(0, used) >= self.settings.stop_gap:
                raise WalletError("too many unused addresses; use one first")
            self.state["current"] = i
            self._save()
            return i, self._addr(self._script(0, i))

    def addresses(self, branch: int, start: int = 0, count: int | None = None) -> list[dict]:
        """ADDRESS EXPLORER: addresses of one branch (0 receive, 1 change)
        with what's known about each. By default, up to the last used one
        plus the gap limit, which is as far as a restore would look."""
        if branch not in (0, 1):
            raise WalletError("branch is 0 (receive) or 1 (change)")
        with self._lock:
            used = self._used()
            held: dict[str, int] = {}
            for u in self._utxos():
                k = u["script"].data.hex()
                held[k] = held.get(k, 0) + u["value"]
            if count is None:
                last = max([i for (b, i) in self._scripts.values()
                            if b == branch and self._script(b, i).data.hex() in used] or [-1])
                if branch == 0:
                    last = max(last, self.state.get("current", 0))
                count = max(0, last + 1 + self.settings.stop_gap - start)
            count = min(count, 1000)
            out = []
            for i in range(start, start + count):
                sc = self._script(branch, i)
                h = sc.data.hex()
                addr = self._addr(sc)
                out.append({"index": i, "address": addr, "used": h in used,
                            "sats": held.get(h, 0), "label": self.label("addr", addr)})
            return out

    def _first_unused(self, branch: int, used: set[str]) -> int:
        i = 0
        while self._script(branch, i).data.hex() in used:
            i += 1
        return i

    def history(self, limit: int = 50) -> list[Tx]:
        out = []
        with self._lock:
            txs = self.state["txs"]
            for t, rec in txs.items():
                tx = self._tx(t)
                received = sum(o.value for o in tx.vout
                               if o.script_pubkey.data.hex() in self._scripts)
                sent, ins_known, all_ours = 0, 0, True
                for i in tx.vin:
                    prev_id = i.txid.hex()
                    if prev_id in txs:
                        prev = self._tx(prev_id).vout[i.vout]
                        ins_known += prev.value
                        if prev.script_pubkey.data.hex() in self._scripts:
                            sent += prev.value
                        else:
                            all_ours = False
                    else:
                        all_ours = False
                fee = ins_known - sum(o.value for o in tx.vout) if all_ours else None
                h = rec["height"]
                ts = self.state["times"].get(str(h)) if h > 0 else rec.get("seen")
                out.append(Tx(t, received - sent, fee, h > 0, h if h > 0 else None, ts))
        out.sort(key=lambda x: (x.confirmed, -(x.height or 0), -(x.timestamp or 0)))
        return out[:limit]

    def coins(self) -> list[Coin]:
        out = [Coin(f"{u['txid']}:{u['vout']}", u["value"], u["height"] > 0,
                    self._addr(u["script"]), u["branch"] == 1) for u in self._utxos()]
        out.sort(key=lambda c: -c.sats)
        return out

    def validate_address(self, text: str) -> str:
        """Returns the cleaned address or raises WalletError."""
        text = text.strip()
        if text.lower().startswith("bitcoin:"):
            text = text[8:].split("?")[0]
        try:
            sc = address_to_scriptpubkey(text)
            ok = sc.address(self.net).lower() == text.lower()
        except Exception:
            ok = False
        if not ok:
            raise WalletError(why_not_bitcoin(text, self.settings.network))
        return text.lower() if text.lower().startswith(self.net["bech32"]) else text

    # ------------------------------------------------------------- spend
    def build(self, address: str, sats: int | None, fee_rate: int,
              coins: list[str] | None = None, fee_from_amount: bool = False) -> dict:
        """sats=None sends everything (minus fee). fee_from_amount: the fee
        comes out of `sats`, so the recipient gets sats - fee and exactly
        `sats` leaves your wallet. Returns {psbt, fee, amount}."""
        dest = address_to_scriptpubkey(self.validate_address(address))
        rate = int(fee_rate)
        if rate < 1:
            raise WalletError("fee rate must be at least 1 sat/vB")
        utxos = self._utxos()
        if coins:
            utxos = [u for u in utxos if f"{u['txid']}:{u['vout']}" in coins]
            if len(utxos) != len(coins):
                raise WalletError("some chosen coins are already spent")
        if self.kind == "inherit" and self.role == "heir":
            if not self.tip_height:
                raise WalletError("go Online once so the device knows the block height")
            ready = self._spendable(utxos)
            if coins and len(ready) != len(utxos):
                raise WalletError("some chosen coins are still time-locked")
            if not ready and utxos:
                t = self.timer()
                raise WalletError("nothing unlocked yet" + (
                    f": next in ~{t['soonest']} blocks" if t["soonest"] else ""))
            utxos = ready
        if not utxos:
            raise WalletError("not enough funds for amount + fee")
        utxos.sort(key=lambda u: (u["height"] <= 0, -u["value"]))   # confirmed, biggest first

        fee_for = lambda n_in, n_out: math.ceil(rate * self._vsize(n_in, n_out))
        outputs: list[tuple[Script, int, tuple | None]] = []
        if sats is None:
            chosen = utxos
            total = sum(u["value"] for u in chosen)
            fee = fee_for(len(chosen), 1)
            if total - fee < DUST:
                raise WalletError("not enough funds for amount + fee")
            outputs.append((dest, total - fee, None))
        elif fee_from_amount:
            if coins:
                chosen, total = utxos, sum(u["value"] for u in utxos)
            else:
                chosen, total = [], 0
                for u in utxos:
                    chosen.append(u)
                    total += u["value"]
                    if total >= sats:
                        break
            if total < sats:
                raise WalletError("not enough funds for that amount")
            change = total - sats
            if change >= DUST:
                fee = fee_for(len(chosen), 2)
                ci = self._first_unused(1, self._used())
                pay = sats - fee
                outputs.append((dest, pay, None))
                outputs.append((self._script(1, ci), change, (1, ci)))
            else:
                pay = sats - fee_for(len(chosen), 1)
                fee = total - pay                    # tiny remainder goes to the fee
                outputs.append((dest, pay, None))
            if pay < DUST:
                raise WalletError("the fee would eat the whole amount")
        else:
            if sats < 330:
                raise WalletError("amount below the dust limit (330 sats)")
            if coins:                                # coin control: exactly these
                chosen, total = utxos, sum(u["value"] for u in utxos)
            else:
                chosen, total = [], 0
                for u in utxos:
                    chosen.append(u)
                    total += u["value"]
                    if total >= sats + fee_for(len(chosen), 2):
                        break
            if total < sats + fee_for(len(chosen), 1):
                raise WalletError("not enough funds for amount + fee")
            change = total - sats - fee_for(len(chosen), 2)
            outputs.append((dest, sats, None))
            if change >= DUST:
                ci = self._first_unused(1, self._used())
                outputs.append((self._script(1, ci), change, (1, ci)))
                fee = fee_for(len(chosen), 2)
            else:
                fee = total - sats                   # tiny remainder goes to the fee
        return {"psbt": self._psbt(chosen, outputs), "fee": fee, "amount": outputs[0][1]}

    def consolidate(self, coins: list[str] | None, fee_rate: int) -> dict:
        """Merge coins into one, sent to this wallet's next unused change
        address (so the signer sees it as yours, not a payment).
        coins=None merges every coin. Returns {psbt, fee, amount, count}."""
        rate = int(fee_rate)
        if rate < 1:
            raise WalletError("fee rate must be at least 1 sat/vB")
        if self.kind == "inherit" and self.role == "heir":
            raise WalletError("an heir can't consolidate: send the coins instead")
        utxos = self._utxos()
        if coins:
            utxos = [u for u in utxos if f"{u['txid']}:{u['vout']}" in coins]
            if len(utxos) != len(coins):
                raise WalletError("some chosen coins are already spent")
        if len(utxos) < 2:
            raise WalletError("pick at least two coins to merge")
        total = sum(u["value"] for u in utxos)
        fee = math.ceil(rate * self._vsize(len(utxos), 1))
        if total - fee < DUST:
            raise WalletError("the fee would eat these coins")
        ci = self._first_unused(1, self._used())
        outputs = [(self._script(1, ci), total - fee, (1, ci))]
        return {"psbt": self._psbt(utxos, outputs), "fee": fee, "amount": total - fee,
                "count": len(utxos), "total": total}

    def _psbt(self, chosen: list[dict], outputs: list[tuple]) -> str:
        """Unsigned PSBT with everything the signer needs to check it."""
        # The heir's path needs nSequence >= the delay (BIP68/112); it's also
        # below 0xfffffffe, so the transaction still signals RBF.
        seq = self.delay if (self.kind == "inherit" and self.role == "heir") else RBF_SEQUENCE
        tx = Transaction(
            version=2,
            vin=[TransactionInput(bytes.fromhex(u["txid"]), u["vout"], sequence=seq)
                 for u in chosen],
            vout=[TransactionOutput(v, s) for s, v, _ in outputs],
            locktime=self.tip_height or 0)          # anti fee-sniping
        psbt = PSBT(tx)
        for k, u in enumerate(chosen):
            keys, ws = self._keys_at(u["branch"], u["index"])
            psbt.inputs[k].witness_utxo = TransactionOutput(u["value"], u["script"])
            psbt.inputs[k].non_witness_utxo = self._tx(u["txid"])
            if ws is not None:
                psbt.inputs[k].witness_script = ws
            self._add_keys(psbt.inputs[k], keys)
        for k, (_, _, own) in enumerate(outputs):
            if own:
                keys, ws = self._keys_at(*own)
                if ws is not None:
                    psbt.outputs[k].witness_script = ws
                self._add_keys(psbt.outputs[k], keys)
        return psbt.to_string()

    def _add_keys(self, scope, keys):
        """Key origins for the signer: BIP32 for SegWit v0, the Taproot
        fields (x-only key, no script leaves, internal key) for Taproot."""
        for pub, fp, path in keys:
            if self.taproot:
                scope.taproot_bip32_derivations[pub] = ([], DerivationPath(fp, path))
                scope.taproot_internal_key = pub
            else:
                scope.bip32_derivations[pub] = DerivationPath(fp, path)

    # -------------------------------------------------------- RBF (BIP125)
    def replaceable(self, txid: str) -> dict | None:
        """If txid is our own unconfirmed RBF payment, its details:
        {inputs, fee, fee_rate, payments: [(script, value, own_path)]}."""
        with self._lock:
            rec = self.state["txs"].get(txid)
            if not rec or rec["height"] > 0:
                return None
            tx = self._tx(txid)
            if not any(i.sequence < 0xFFFFFFFE for i in tx.vin):
                return None
            # Something already spends its outputs: replacing would evict that too.
            if any(i.txid.hex() == txid for t in self.state["txs"] for i in self._tx(t).vin):
                return None
            inputs = []
            for i in tx.vin:
                prev_id = i.txid.hex()
                if prev_id not in self.state["txs"]:
                    return None
                prev = self._tx(prev_id).vout[i.vout]
                key = prev.script_pubkey.data.hex()
                if key not in self._scripts:
                    return None                        # not all inputs are ours
                b, idx = self._scripts[key]
                inputs.append({"txid": prev_id, "vout": i.vout, "value": prev.value,
                               "script": prev.script_pubkey, "branch": b, "index": idx,
                               "height": self.state["txs"][prev_id]["height"]})
            payments, change = [], 0
            for o in tx.vout:
                key = o.script_pubkey.data.hex()
                own = self._scripts.get(key)
                if own and own[0] == 1:
                    change += o.value
                else:
                    payments.append((o.script_pubkey, o.value, own))
            fee = sum(u["value"] for u in inputs) - sum(o.value for o in tx.vout)
            return {"inputs": inputs, "fee": fee, "payments": payments, "change": change,
                    "fee_rate": fee / self._vsize(len(tx.vin), len(tx.vout))}

    def bump(self, txid: str, fee_rate: int, cancel: bool = False) -> dict:
        """Replacement for an unconfirmed payment: same payments with a higher
        fee, or (cancel) everything back to our own change address.
        Returns {psbt, fee, replaces}."""
        info = self.replaceable(txid)
        if not info:
            raise WalletError("that transaction can't be replaced")
        rate = int(fee_rate)
        if rate <= info["fee_rate"]:
            raise WalletError(f"new fee must beat {info['fee_rate']:.1f} sat/vB")
        chosen = list(info["inputs"])
        # Coins we may add: unspent, not created by the tx being replaced,
        # and (for an heir) already unlocked.
        extra = [u for u in self._spendable(self._utxos()) if u["txid"] != txid]
        extra.sort(key=lambda u: (u["height"] <= 0, -u["value"]))

        def need(n_in, n_out):
            vs = self._vsize(n_in, n_out)
            # BIP125: pay at least the old fee plus 1 sat/vB for the new size
            return max(math.ceil(rate * vs), info["fee"] + math.ceil(vs))

        change_i = self._first_unused(1, self._used() - self._outputs_of(txid))
        change_script = self._script(1, change_i)
        while True:
            total = sum(u["value"] for u in chosen)
            if cancel:
                fee = need(len(chosen), 1)
                if total - fee >= DUST:
                    outputs = [(change_script, total - fee, (1, change_i))]
                    break
            else:
                pay = sum(v for _, v, _ in info["payments"])
                outputs = list(info["payments"])
                fee2 = need(len(chosen), len(outputs) + 1)
                if total - pay - fee2 >= DUST:
                    outputs.append((change_script, total - pay - fee2, (1, change_i)))
                    fee = fee2
                    break
                fee1 = need(len(chosen), len(outputs))
                if total - pay >= fee1:
                    fee = total - pay                     # small remainder goes to fee
                    break
            if not extra:
                raise WalletError("not enough funds for that fee")
            chosen.append(extra.pop(0))
        return {"psbt": self._psbt(chosen, outputs), "fee": fee, "replaces": txid}

    def refresh(self, fee_rate: int) -> dict:
        """Inheritance owner: move every coin to a fresh address of this same
        wallet, which restarts the heir's timer on all of them."""
        if self.kind != "inherit" or self.role != "owner":
            raise WalletError("only the owner of an inheritance wallet refreshes")
        utxos = [u for u in self._utxos() if u["height"] > 0]
        if not utxos:
            raise WalletError("no confirmed coins to refresh")
        fee = math.ceil(int(fee_rate) * self._vsize(len(utxos), 1))
        total = sum(u["value"] for u in utxos)
        if total - fee < DUST:
            raise WalletError("coins too small to cover the fee")
        ci = self._first_unused(1, self._used())
        return {"psbt": self._psbt(utxos, [(self._script(1, ci), total - fee, (1, ci))]),
                "fee": fee}

    def _outputs_of(self, txid: str) -> set[str]:
        return {o.script_pubkey.data.hex() for o in self._tx(txid).vout}

    # ------------------------------------------------------------ labels
    def label(self, kind: str, ref: str) -> str:
        """kind: "tx" or "addr" (BIP329 types)."""
        return self.state.get("labels", {}).get(f"{kind}:{ref}", "")

    def set_label(self, kind: str, ref: str, text: str) -> None:
        with self._lock:
            labels = self.state.setdefault("labels", {})
            key = f"{kind}:{ref}"
            if text.strip():
                labels[key] = text.strip()[:60]
            else:
                labels.pop(key, None)
            self._save()

    def tx_label(self, txid: str) -> str:
        """The tx's own label, else the label of an address it paid or was paid to."""
        own = self.label("tx", txid)
        if own or txid not in self.state["txs"]:
            return own
        for o in self._tx(txid).vout:
            try:
                lab = self.label("addr", o.script_pubkey.address(self.net))
            except Exception:
                continue
            if lab:
                return lab
        return ""

    def export_labels(self) -> str:
        """BIP329 JSON Lines (import into Sparrow and other wallets)."""
        lines = []
        for key, text in sorted(self.state.get("labels", {}).items()):
            kind, ref = key.split(":", 1)
            lines.append(json.dumps({"type": kind, "ref": ref, "label": text}))
        return "\n".join(lines)

    def signatures(self, psbt_b64: str) -> tuple[int, int]:
        """(fewest signatures on any input, signatures needed)."""
        psbt = PSBT.from_string(psbt_b64)
        return min(len(i.partial_sigs) for i in psbt.inputs), self.k

    @staticmethod
    def combine(a_b64: str, b_b64: str) -> str:
        """Merge signatures from two copies of the same PSBT."""
        a, b = PSBT.from_string(a_b64), PSBT.from_string(b_b64)
        if a.tx.txid() != b.tx.txid():
            raise WalletError("that PSBT is for a different transaction")
        for ia, ib in zip(a.inputs, b.inputs):
            ia.partial_sigs.update(ib.partial_sigs)
        return a.to_string()

    def finalize(self, signed_b64: str) -> SignedTx:
        psbt = PSBT.from_string(signed_b64)
        tx = Transaction.parse(psbt.tx.serialize())
        for i, inp in enumerate(psbt.inputs):
            if self.taproot:
                # key-path spend: one Schnorr signature (embit puts it in the
                # final witness when it signs; BIP174 signers use taproot_key_sig)
                w = inp.final_scriptwitness
                sig = getattr(inp, "taproot_key_sig", None)
                if w is not None and len(w.items) == 1 and len(w.items[0]) in (64, 65):
                    tx.vin[i].witness = w
                elif sig and len(sig) in (64, 65):
                    tx.vin[i].witness = Witness([sig])
                else:
                    raise WalletError("signatures missing; couldn't finalize")
                continue
            if not self.multisig:
                if len(inp.partial_sigs) != 1:
                    raise WalletError("signatures missing; couldn't finalize")
                pub, sig = next(iter(inp.partial_sigs.items()))
                tx.vin[i].witness = Witness([sig, pub.sec()])
                continue
            ws = inp.witness_script
            if ws is None:
                raise WalletError("PSBT is missing the multisig script")
            data = ws.data
            if self.kind == "inherit":
                tx.vin[i].witness = self._inherit_witness(inp, data, tx, i)
                continue
            order = [data[2 + 34 * j:2 + 34 * j + 33] for j in range(self.n)]   # pubkeys in script order
            by_key = {pub.sec(): sig for pub, sig in inp.partial_sigs.items()}
            sigs = [by_key[pk] for pk in order if pk in by_key][:self.k]
            if len(sigs) < self.k:
                raise WalletError(f"needs {self.k - len(sigs)} more signature(s)")
            tx.vin[i].witness = Witness([b""] + sigs + [data])
        return SignedTx(tx.txid().hex(), tx.serialize())

    def _inherit_witness(self, inp, script: bytes, tx, i) -> Witness:
        owner_pub = script[1:34]                     # <33-byte owner key> CHECKSIG ...
        by_key = {pub.sec(): sig for pub, sig in inp.partial_sigs.items()}
        if owner_pub in by_key:
            return Witness([by_key[owner_pub], script])
        from embit.hashes import hash160     # pure Python: OpenSSL 3 may lack ripemd160
        heir_hash = script[script.index(b"\x76\xa9\x14") + 3:][:20]
        for pub, sig in by_key.items():
            if hash160(pub) == heir_hash:
                if tx.version < 2 or tx.vin[i].sequence < self.delay:
                    raise WalletError("heir spend needs the time-lock sequence")
                return Witness([sig, pub, b"", script])
        raise WalletError("signature missing; couldn't finalize")

    def broadcast(self, tx: SignedTx, replaces: str | None = None) -> str:
        if self._client is None:
            self.connect()
        try:
            txid = self._client.call("blockchain.transaction.broadcast", tx.raw.hex())
        except (OSError, ElectrumError) as e:
            raise WalletError(f"broadcast failed: {e}") from e
        self.record_unconfirmed(tx, local=False, replaces=replaces)
        return txid

    def record_unconfirmed(self, tx, local: bool = True, replaces: str | None = None) -> None:
        """Add a transaction we made (or, in tests, a made-up incoming one)."""
        if isinstance(tx, (bytes, bytearray)):
            parsed = Transaction.parse(bytes(tx))
            tx = SignedTx(parsed.txid().hex(), bytes(tx))
        with self._lock:
            rec = {"hex": tx.raw.hex(), "height": 0, "seen": int(time.time())}
            if local:
                rec["local"] = True
            if replaces and replaces in self.state["txs"]:
                del self.state["txs"][replaces]
                self._parsed.pop(replaces, None)
                old_label = self.label("tx", replaces)
                if old_label:
                    self.state.setdefault("labels", {})[f"tx:{tx.txid}"] = old_label
            self.state["txs"][tx.txid] = rec
            self._save()


# ------------------------------------------------- wrong-address messages
def why_not_bitcoin(text: str, network: str) -> str:
    """A short (<= 40 chars, it shows under the text box) reason why text
    isn't an address this wallet can pay: another coin, the other Bitcoin
    network, or a typo. Detection is a courtesy: the real check is that the
    address parses for this network at all."""
    import re
    from embit import base58

    t = text.strip()
    low = t.lower()
    main = network == "bitcoin"
    here = "mainnet" if main else network

    # other coins, by their shapes
    if re.fullmatch(r"0x[0-9a-fA-F]{40}", t):
        return "That's an Ethereum address, not bitcoin"
    if low.startswith(("bitcoincash:", "bchtest:")) or re.fullmatch(r"[qp][0-9a-z]{41}", low):
        return "That's a Bitcoin Cash address"
    if low.startswith(("ltc1", "tltc1")):
        return "That's a Litecoin address, not bitcoin"
    if low.startswith("addr1"):
        return "That's a Cardano address, not bitcoin"
    if low.startswith(("lnbc", "lntb", "lightning:", "lnurl")):
        return "Lightning isn't supported yet"
    try:
        version = base58.decode_check(t)[0]
    except Exception:  # noqa: BLE001
        version = None
    other = {0x30: "Litecoin", 0x32: "Litecoin", 0x1E: "Dogecoin", 0x16: "Dogecoin",
             0x41: "Tron", 0x4C: "Dash", 0x10: "Dash"}.get(version)
    if other:
        return f"That's a {other} address, not bitcoin"

    # bitcoin, but the other network
    hrp = low.split("1", 1)[0] if "1" in low else ""
    btc_main = hrp == "bc" or version in (0x00, 0x05)
    btc_test = hrp in ("tb", "bcrt") or version in (0x6F, 0xC4)
    if btc_main and not main and _bech_ok(low) | (version is not None):
        return f"Mainnet address; wallet is on {here}"
    if btc_test and main and _bech_ok(low) | (version is not None):
        return "Test-network address; wallet is mainnet"

    if btc_test and not main and _bech_ok(low):
        return "Address is for another test network"

    # right shape, wrong checksum: a typo
    if hrp in ("bc", "tb", "bcrt") or (t[:1] in "123mn" and 25 <= len(t) <= 35):
        return "Typo? The address checksum is wrong"
    return "Not a bitcoin address"


def _bech_ok(low: str) -> bool:
    from embit import bech32
    try:
        hrp, data, _ = bech32.bech32_decode(low)
        return hrp is not None and data is not None
    except Exception:  # noqa: BLE001
        return False

