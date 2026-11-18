"""
Offline tests for seed, vault, signer and wallet service. No network: the
wallet is "funded" by handing BDK a made-up transaction paying to it.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("CZ_ARGON_T", "1")
os.environ.setdefault("CZ_ARGON_M_KIB", "1024")   # fast KDF for tests only
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from embit.psbt import PSBT  # noqa: E402

from core import seed as seedlib  # noqa: E402
from core.config import Settings  # noqa: E402
from core.signer import Signer, SignerError  # noqa: E402
from core.signer_service import RemoteError, SignerClient  # noqa: E402
from core.vault import Vault, Wiped, WrongPin  # noqa: E402
from core.wallet import WalletError, WalletService  # noqa: E402

ABANDON = "abandon " * 11 + "about"
NET = "signet"


# ---------------------------------------------------------------- seed
def test_new_mnemonic_valid_and_unique():
    a, b = seedlib.new_mnemonic(12), seedlib.new_mnemonic(24)
    assert seedlib.is_valid(a) and len(a.split()) == 12
    assert seedlib.is_valid(b) and len(b.split()) == 24
    assert seedlib.new_mnemonic(12, b"123456") != seedlib.new_mnemonic(12, b"123456")


def test_known_vector_bip84():
    # BIP84 test vector: abandon x11 about, first receive address
    root = seedlib.root_key(ABANDON, "", "bitcoin")
    recv, change = seedlib.public_descriptors(root, "bitcoin")
    w = WalletService(Settings(network="bitcoin"), recv, change, "73c5da0a", in_memory=True)
    assert w.receive_address() == (0, "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu")
    assert w.coins() == [] and w.balance()["total"] == 0


def test_completions_and_last_word():
    assert seedlib.completions("aban") == ["abandon"]
    opts = seedlib.last_word_options(["abandon"] * 11)
    assert "about" in opts and 100 < len(opts) < 140   # 128 valid words


# --------------------------------------------------------------- vault
def test_vault_roundtrip_and_wipe(tmp_path):
    v = Vault(str(tmp_path))
    v.create("123456", ABANDON, NET, max_tries=3)
    assert tuple(v.unlock("123456")) == (ABANDON, None)
    with pytest.raises(WrongPin) as e:
        v.unlock("000000")
    assert e.value.tries_left == 2
    assert v.unlock("123456").mnemonic == ABANDON     # success resets the count
    for left in (2, 1):
        with pytest.raises(WrongPin):
            v.unlock("999999")
    with pytest.raises(Wiped):
        v.unlock("999999")
    assert not v.exists()


def test_vault_file_has_no_plaintext(tmp_path):
    v = Vault(str(tmp_path))
    v.create("123456", ABANDON, NET, passphrase="hunter2")
    raw = (tmp_path / "seed.vault").read_text()
    assert "abandon" not in raw and "hunter2" not in raw


# ----------------------------------------------------- signer + wallet
def fake_tx(script, sats):
    from embit.transaction import Transaction, TransactionInput, TransactionOutput
    return Transaction(vin=[TransactionInput(os.urandom(32), 0)],
                       vout=[TransactionOutput(sats, script)])


def fund(wallet: WalletService, sats: int) -> str:
    """Give the watch-only wallet a fake incoming transaction."""
    from embit.script import address_to_scriptpubkey
    _, addr = wallet.receive_address()
    wallet.record_unconfirmed(fake_tx(address_to_scriptpubkey(addr), sats).serialize())
    return addr


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("CZ_FORCE_ONLINE", "0")
    signer = Signer(str(tmp_path / "signer"))
    info = signer.setup("2468", ABANDON, NET)
    wallet = WalletService(Settings(network=NET), info["descriptor"],
                           info["change_descriptor"], info["fingerprint"], in_memory=True)
    fund(wallet, 100_000)
    return signer, wallet


DEST = "tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx"   # BIP173 example, valid on signet


def test_full_send_pipeline(rig):
    signer, wallet = rig
    assert wallet.balance()["total"] == 100_000
    built = wallet.build(DEST, 30_000, fee_rate=5)
    s = signer.describe(built["psbt"])
    kinds = sorted(o["kind"] for o in s["outputs"])
    assert kinds == ["change", "send"]
    assert s["spend"] == 30_000 and s["fee"] == built["fee"]
    assert 4 <= s["fee_rate"] <= 6
    signed = signer.sign(built["psbt"], s["digest"])
    tx = wallet.finalize(signed)
    from embit.transaction import Transaction
    parsed = Transaction.parse(tx.raw)
    assert len(parsed.vin) == 1 and len(parsed.vout) == 2 and parsed.vin[0].witness
    assert_valid_signatures(signed, parsed)
    wallet.record_unconfirmed(tx)
    assert wallet.balance()["total"] == 100_000 - 30_000 - built["fee"]


def assert_valid_signatures(signed_b64, tx):
    """Check each witness signature against the BIP143 sighash."""
    from embit import ec
    from embit.script import p2pkh
    psbt = PSBT.from_string(signed_b64)
    for i, vin in enumerate(tx.vin):
        sig, pubsec = vin.witness.items
        pub = ec.PublicKey.parse(pubsec)
        h = tx.sighash_segwit(i, p2pkh(pub), psbt.utxo(i).value)
        assert pub.verify(ec.Signature.parse(sig[:-1]), h)


def test_send_max(rig):
    signer, wallet = rig
    built = wallet.build(DEST, None, fee_rate=2)
    s = signer.describe(built["psbt"])
    assert [o["kind"] for o in s["outputs"]] == ["send"]
    assert s["spend"] + s["fee"] == 100_000


def test_hidden_payment_disguised_as_change_is_caught(rig):
    """A compromised wallet service relabels a foreign output as change."""
    signer, wallet = rig
    built = wallet.build(DEST, 30_000, fee_rate=5)
    p = PSBT.from_string(built["psbt"])
    change_i = next(i for i, o in enumerate(p.outputs) if o.bip32_derivations)
    send_i = 1 - change_i
    p.outputs[send_i].bip32_derivations = dict(p.outputs[change_i].bip32_derivations)
    s = signer.describe(p.to_string())
    assert sorted(o["kind"] for o in s["outputs"]) == ["change", "send"]   # not fooled


def test_signer_refuses_changed_tx(rig):
    signer, wallet = rig
    a = wallet.build(DEST, 30_000, fee_rate=5)
    s = signer.describe(a["psbt"])
    b = wallet.build(DEST, 40_000, fee_rate=5)
    with pytest.raises(SignerError):
        signer.sign(b["psbt"], s["digest"])


def test_signer_refuses_absurd_fee(rig, monkeypatch):
    signer, wallet = rig
    monkeypatch.setattr("core.signer.HARD_FEE_RATE", 500)
    built = wallet.build(DEST, 1_000, fee_rate=600)
    with pytest.raises(SignerError, match="absurd"):
        signer.describe(built["psbt"])


def test_online_cap(rig, monkeypatch):
    signer, wallet = rig
    monkeypatch.setenv("CZ_FORCE_ONLINE", "1")
    built = wallet.build(DEST, 30_000, fee_rate=5)
    s = signer.describe(built["psbt"])
    assert s["online"] and not s["cap_ok"]
    with pytest.raises(SignerError, match="cap"):
        signer.sign(built["psbt"], s["digest"])
    signer.set_cap("2468", 50_000)
    s = signer.describe(built["psbt"])
    assert s["cap_ok"]
    signed = signer.sign(built["psbt"], s["digest"])
    assert signer.status()["cap"]["spent"] == 30_000 + built["fee"]
    first = wallet.finalize(signed)
    wallet.record_unconfirmed(first)
    second = wallet.build(DEST, 30_000, fee_rate=5)      # spends the change
    s2 = signer.describe(second["psbt"])
    assert not s2["cap_ok"]          # 50k cap, ~30.7k already spent today

    # A fee bump of the first payment only costs the extra fee
    bump = wallet.bump(first.txid, 20)
    s3 = signer.describe(bump["psbt"])
    assert s3["replaces_signed"] and s3["cap_cost"] == bump["fee"] - built["fee"]
    assert s3["cap_ok"]
    signer.sign(bump["psbt"], s3["digest"])
    assert signer.status()["cap"]["spent"] == 30_000 + bump["fee"]


def test_vault_mode_no_cap(rig):
    signer, wallet = rig      # CZ_FORCE_ONLINE=0
    built = wallet.build(DEST, 90_000, fee_rate=5)
    s = signer.describe(built["psbt"])
    assert s["cap_ok"]
    signer.sign(built["psbt"], s["digest"])


def test_insufficient_funds(rig):
    _, wallet = rig
    with pytest.raises(WalletError, match="not enough"):
        wallet.build(DEST, 200_000, fee_rate=5)


def test_wrong_network_address(rig):
    _, wallet = rig
    with pytest.raises(WalletError):
        wallet.build("bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu", 1000, 5)


def test_lock_blocks_signing(rig):
    signer, wallet = rig
    signer.lock()
    with pytest.raises(SignerError):
        signer.describe(wallet.build(DEST, 1000, 5)["psbt"])


def test_client_has_no_pin_or_seed_methods(tmp_path):
    """The trusted path: nothing the app can call takes a PIN or returns
    seed words. Those only happen on the signer's own screen."""
    from core.signer_service import _dispatch
    c = SignerClient(inproc=True, data_dir=str(tmp_path))
    assert c.status()["has_seed"] is False
    c.local.setup(pin="1111", mnemonic=ABANDON, network=NET)
    for name in ("setup", "unlock", "sign", "reveal_words", "set_cap", "wipe", "change_pin",
                 "set_duress", "bind_chip", "set_quick_unlock", "register_multisig"):
        with pytest.raises(AttributeError):
            getattr(c, name)
        r = _dispatch(c._local, {"method": name, "params": {"pin": "1111"}})
        assert r == {"ok": False, "error": "unknown method", "type": "BadRequest"}
    r = _dispatch(c._local, {"method": "status", "params": "nope"})
    assert r["type"] == "BadRequest"
    c.lock()
    with pytest.raises(RemoteError) as e:
        c.describe(psbt="x")
    assert e.value.kind == "Locked"


def test_history_and_coins(rig):
    _, wallet = rig
    h = wallet.history()
    assert len(h) == 1 and h[0].delta == 100_000 and not h[0].confirmed
    c = wallet.coins()
    assert c[0].sats == 100_000 and not c[0].change


def test_fee_from_amount(rig):
    """Sending everything you have, typed as an amount: the fee comes out of
    it instead of failing. And a partial amount keeps its change."""
    from embit.psbt import PSBT
    signer, wallet = rig
    with pytest.raises(WalletError, match="not enough funds"):
        wallet.build(DEST, 100_000, 5)
    b = wallet.build(DEST, 100_000, 5, fee_from_amount=True)
    tx = PSBT.from_string(b["psbt"]).tx
    assert len(tx.vout) == 1 and b["amount"] == tx.vout[0].value == 100_000 - b["fee"]
    s = signer.describe(b["psbt"])                       # the signer agrees on the numbers
    signer.sign(b["psbt"], s["digest"])
    b = wallet.build(DEST, 40_000, 5, fee_from_amount=True)
    tx = PSBT.from_string(b["psbt"]).tx
    assert len(tx.vout) == 2 and tx.vout[0].value == 40_000 - b["fee"]
    assert tx.vout[1].value == 60_000                     # change untouched
    with pytest.raises(WalletError, match="eat the whole"):
        wallet.build(DEST, 400, 50, fee_from_amount=True)


def test_own_server_and_tor_skip_for_home_nodes(monkeypatch):
    from core import wallet as wmod
    from core.config import DEFAULT_SERVERS
    from core.electrum import local_host
    s = Settings(network="bitcoin")
    assert not s.own_server
    s.electrum_url = DEFAULT_SERVERS["bitcoin"]
    assert not s.own_server                       # typing the default back in: still public
    s.electrum_url = "tcp://umbrel.local:50001"
    assert s.own_server
    for h in ("umbrel.local", "192.168.1.20", "10.0.0.5", "127.0.0.1", "node.lan"):
        assert local_host(h), h
    for h in ("electrum.blockstream.info", "8.8.8.8", "abc.onion"):
        assert not local_host(h), h
    seen = {}

    class Fake:
        def __init__(self, url, socks5=None):
            seen["socks5"] = socks5

        def connect(self):
            raise OSError("stop here")
    monkeypatch.setattr(wmod, "ElectrumClient", Fake)
    monkeypatch.delenv("CZ_NET_SOCK", raising=False)
    w = WalletService.__new__(WalletService)
    w.settings = Settings(network="bitcoin", electrum_url="tcp://umbrel.local:50001", use_tor=True)
    with pytest.raises(WalletError):
        w.connect()
    assert seen["socks5"] is None                 # home node: straight there, not via Tor
    w.settings.electrum_url = "ssl://electrum.blockstream.info:50002"
    with pytest.raises(WalletError):
        w.connect()
    assert seen["socks5"] == w.settings.tor_proxy


def test_parse_server_from_node_dashboards():
    from core.config import parse_server
    assert parse_server("umbrel.local:50003:t") == "tcp://umbrel.local:50003"
    assert parse_server("umbrel.local:50002:s") == "ssl://umbrel.local:50002"
    assert parse_server(" tcp://Umbrel.local:50001/ ") == "tcp://umbrel.local:50001"
    assert parse_server("ssl://electrum.blockstream.info:50002") == \
        "ssl://electrum.blockstream.info:50002"
    assert parse_server("192.168.1.20:50001") == "tcp://192.168.1.20:50001"
    assert parse_server("abc123.onion:50001") == "tcp://abc123.onion:50001"
    assert parse_server("electrum.example.com:50002") == "ssl://electrum.example.com:50002"
    for bad in ("", "bc1qxyz", "http://x:1", "host:99999", "a b:1", "x:1:q", "bitcoin:bc1q"):
        with pytest.raises(ValueError):
            parse_server(bad)
