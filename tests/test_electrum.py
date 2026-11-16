"""
Sync, fees and broadcast against a fake Electrum server on localhost.
"""
import json
import socketserver
import threading

import pytest
from embit.script import address_to_scriptpubkey
from embit.transaction import Transaction

from core.config import Settings
from core.electrum import scripthash
from core.signer import Signer
from core.wallet import WalletService
from test_core import ABANDON, DEST, NET, fake_tx


class FakeChain:
    def __init__(self):
        self.height = 900_000
        self.txs = {}            # txid -> (hex, height)
        self.broadcasts = []

    def add(self, tx: Transaction, height: int):
        self.txs[tx.txid().hex()] = (tx.serialize().hex(), height)

    def history(self, sh):
        out = []
        for txid, (hx, h) in self.txs.items():
            tx = Transaction.from_string(hx)
            scripts = [o.script_pubkey.data for o in tx.vout]
            for i in tx.vin:                      # spends of our outputs count too
                prev = self.txs.get(i.txid.hex())
                if prev:
                    scripts.append(Transaction.from_string(prev[0]).vout[i.vout].script_pubkey.data)
            if any(scripthash(s) == sh for s in scripts):
                out.append({"tx_hash": txid, "height": h})
        return out

    def handle(self, method, params):
        if method == "server.version":
            return ["fake", "1.4"]
        if method == "blockchain.headers.subscribe":
            return {"height": self.height, "hex": "00" * 80}
        if method == "blockchain.scripthash.get_history":
            return self.history(params[0])
        if method == "blockchain.transaction.get":
            return self.txs[params[0]][0]
        if method == "blockchain.block.header":
            hdr = bytearray(80)
            hdr[68:72] = (1_700_000_000 + params[0]).to_bytes(4, "little")
            return hdr.hex()
        if method == "blockchain.estimatefee":
            return {1: 0.0002, 3: 0.0001, 6: -1}[params[0]]
        if method == "blockchain.transaction.broadcast":
            tx = Transaction.from_string(params[0])
            self.add(tx, 0)
            self.broadcasts.append(tx.txid().hex())
            return tx.txid().hex()
        raise ValueError(method)


@pytest.fixture
def server():
    chain = FakeChain()

    class H(socketserver.StreamRequestHandler):
        def handle(self):
            for line in self.rfile:
                req = json.loads(line)
                try:
                    res = {"id": req["id"], "result": chain.handle(req["method"], req["params"])}
                except Exception as e:  # noqa: BLE001
                    res = {"id": req["id"], "error": {"message": str(e)}}
                self.wfile.write(json.dumps(res).encode() + b"\n")

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield chain, f"tcp://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def wallet(server, tmp_path):
    chain, url = server
    signer = Signer(str(tmp_path / "s"))
    info = signer.setup("2468", ABANDON, NET)
    s = Settings(network=NET, electrum_url=url, use_tor=False)
    w = WalletService(s, info["descriptor"], info["change_descriptor"], info["fingerprint"],
                      data_dir=str(tmp_path / "app"))
    return chain, w, signer, info


def addr_script(w, branch, i):
    return w._script(branch, i)


def test_sync_finds_coins_and_gap(wallet):
    chain, w, _, _ = wallet
    chain.add(fake_tx(addr_script(w, 0, 0), 50_000), 899_990)
    chain.add(fake_tx(addr_script(w, 0, 25), 7_000), 0)        # past a gap, in mempool
    chain.add(fake_tx(addr_script(w, 0, 80), 1_000), 899_000)  # way past the gap: not found
    w.sync()
    b = w.balance()
    assert b == {"confirmed": 50_000, "pending": 7_000, "spendable": 57_000, "total": 57_000}
    h = w.history()
    assert h[0].delta == 7_000 and not h[0].confirmed
    assert h[1].timestamp == 1_700_000_000 + 899_990
    assert w.receive_address()[0] == 1                       # 0 is used now
    assert w.fee_estimates() == {"fast": 20, "normal": 10, "slow": 2}


def test_send_broadcast_and_resync(wallet):
    chain, w, signer, _ = wallet
    chain.add(fake_tx(addr_script(w, 0, 0), 100_000), 899_990)
    w.sync()
    built = w.build(DEST, 40_000, 3)
    s = signer.describe(built["psbt"])
    tx = w.finalize(signer.sign(built["psbt"], s["digest"]))
    assert w.broadcast(tx) == tx.txid and chain.broadcasts == [tx.txid]
    w.sync()                                                 # server now reports it
    assert w.balance()["total"] == 100_000 - 40_000 - built["fee"]
    assert len(w.coins()) == 1 and w.coins()[0].change
    assert not w.state["txs"][tx.txid].get("local")


def test_state_survives_restart(wallet, tmp_path):
    chain, w, _, info = wallet
    chain.add(fake_tx(addr_script(w, 0, 0), 12_345), 899_990)
    w.sync()
    w2 = WalletService(w.settings, info["descriptor"], info["change_descriptor"],
                       info["fingerprint"], data_dir=str(tmp_path / "app"))
    assert w2.balance()["total"] == 12_345


def test_unbroadcast_local_tx_kept_until_conflict(wallet):
    chain, w, signer, _ = wallet
    chain.add(fake_tx(addr_script(w, 0, 0), 100_000), 899_990)
    w.sync()
    built = w.build(DEST, 10_000, 2)
    s = signer.describe(built["psbt"])
    tx = w.finalize(signer.sign(built["psbt"], s["digest"]))
    w.record_unconfirmed(tx)                                 # Vault mode: outbox
    w.sync()
    assert tx.txid in w.state["txs"]                         # server doesn't know it yet: kept
    # A different spend of the same coin confirms elsewhere -> ours is dropped
    w.state["txs"].pop(tx.txid)
    other = w.build(DEST, 20_000, 2)                         # same coin, different tx
    s2 = signer.describe(other["psbt"])
    tx2 = w.finalize(signer.sign(other["psbt"], s2["digest"]))
    w.record_unconfirmed(tx)
    chain.add(Transaction.parse(tx2.raw), 900_000)
    w.sync()
    assert tx.txid not in w.state["txs"] and tx2.txid in w.state["txs"]
