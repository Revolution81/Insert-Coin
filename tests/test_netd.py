"""
The cznet relay: the wallet syncs and broadcasts through it exactly as
before, and it refuses anything outside the small set of Electrum calls the
wallet needs, servers that aren't host:port, remote Tor proxies, and
connections from the wrong user.
"""
import json
import os
import socket
import tempfile

import pytest

from core.config import Settings
from core.electrum import ElectrumError
from core.netd import RelayClient, RelayServer, check_calls, check_server, RelayError
from core.signer import Signer
from core.wallet import WalletService

from test_core import ABANDON, DEST, fake_tx
from test_electrum import FakeChain, server  # noqa: F401  (fixture)


@pytest.fixture
def relay(monkeypatch):
    d = tempfile.mkdtemp(prefix="cz")                 # short path: Unix sockets max ~108 chars
    path = os.path.join(d, "net.sock")
    srv = RelayServer(path, allowed_uid=os.getuid())
    import threading
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("CZ_NET_SOCK", path)
    yield path
    srv.shutdown()
    srv.server_close()


def test_wallet_through_relay(server, relay, tmp_path):  # noqa: F811
    chain, url = server
    s = Signer(str(tmp_path / "s"))
    info = s.setup("2468", ABANDON, "signet")
    w = WalletService(Settings(network="signet", electrum_url=url, use_tor=False),
                      info["descriptor"], info["change_descriptor"], info["fingerprint"],
                      in_memory=True)
    chain.add(fake_tx(w._script(0, 0), 100_000), 899_990)
    w.sync()
    assert type(w._client).__name__ == "RelayClient"
    assert w.balance()["total"] == 100_000 and w.tip_height == chain.height
    assert w.fee_estimates()["fast"] == 20
    built = w.build(DEST, 40_000, 3)
    summary = s.describe(built["psbt"])
    tx = w.finalize(s.sign(built["psbt"], summary["digest"]))
    assert w.broadcast(tx) == tx.txid and chain.broadcasts == [tx.txid]


def test_relay_refuses_other_methods(server, relay):  # noqa: F811
    _, url = server
    c = RelayClient(url).connect()
    assert c.call("blockchain.headers.subscribe")["height"] == 900_000
    for method, params in [("server.peers.subscribe", []),
                           ("blockchain.scripthash.subscribe", ["00" * 32]),
                           ("blockchain.transaction.get", ["not hex"]),
                           ("blockchain.block.header", [-1])]:
        with pytest.raises(ElectrumError):
            c.call(method, *params)
    assert c.call("blockchain.headers.subscribe")["height"] == 900_000   # still usable


def test_relay_refuses_bad_servers_and_remote_tor(relay):
    for url in ("http://example.com:80", "tcp://exa mple.com:1", "ssl://host:99999", "ssl://"):
        with pytest.raises(ElectrumError):
            RelayClient(url).connect()
    with pytest.raises(ElectrumError, match="this device"):
        RelayClient("ssl://example.com:50002", socks5="10.0.0.5:9050").connect()


def test_relay_only_serves_the_app_user(monkeypatch):
    d = tempfile.mkdtemp(prefix="cz")
    path = os.path.join(d, "net.sock")
    srv = RelayServer(path, allowed_uid=os.getuid() + 12345)
    import threading
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pytest.raises(ElectrumError):
            RelayClient("tcp://127.0.0.1:1", path=path).connect()
    finally:
        srv.shutdown()
        srv.server_close()


def test_validators():
    assert check_server("ssl://electrum.blockstream.info:50002")[1] == "electrum.blockstream.info"
    assert check_server("tcp://abcdefghijklmnop.onion:50001")[2] == 50001
    with pytest.raises(RelayError):
        check_calls([["blockchain.transaction.broadcast", ["zz"]]])
    with pytest.raises(RelayError):
        check_calls([["blockchain.estimatefee", [True]]])
    with pytest.raises(RelayError):
        check_calls([["blockchain.estimatefee", [3]]] * 201)


def test_relay_raw_garbage(relay):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(relay)
    f = s.makefile("rwb")
    for junk in (b"not json\n", b'{"op": "call", "calls": [["x"]]}\n', b'{"op": 5}\n'):
        f.write(junk)
        f.flush()
        assert json.loads(f.readline())["ok"] is False
    s.close()
