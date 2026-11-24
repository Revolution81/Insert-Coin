"""PayJoin (BIP-78), sending: a receiver adds a coin; the signer signs the
proposal only if it passes every check against the original it signed,
and anything else (a refusal, a bad proposal, no answer) sends the
original instead."""
import base64

import pytest
from embit import bip32
from embit.networks import NETWORKS as N
from embit.psbt import PSBT, DerivationPath, InputScope
from embit.script import Witness, p2wpkh
from embit.transaction import Transaction, TransactionInput

from core import payjoin as pj
from core.qr import parse_bip21
from core.signer import SignerError
from script_vm import verify_tx
from test_accounts import signer, wallet_for  # noqa: F401
from test_core import DEST, fake_tx, fund
from test_app import app  # noqa: F401

RECV = bip32.HDKey.from_seed(b"\x07" * 32, version=N["test"]["xprv"])


def _setup(signer, sats=30_000, rate=3):  # noqa: F811
    w = wallet_for(signer, "wpkh:0")
    fund(w, 100_000)
    built = w.build(DEST, sats, fee_rate=rate)
    s = signer.describe(built["psbt"])
    signed = signer.sign(built["psbt"], s["digest"], payjoin=True)
    return w, s, signed


def receiver_proposal(original_b64, add_value=50_000, take_from_change=0, mutate=None,
                      prove=True):
    """What a BIP-78 receiver does: add its own coin, pay itself back in the
    payment output, clear the sender's signatures, sign and finalize its input."""
    p = PSBT.from_string(original_b64)
    key = RECV.derive("m/84h/1h/0h/0/0")
    coin = fake_tx(p2wpkh(key.key), add_value)
    vin = TransactionInput(coin.txid(), 0, sequence=p.inputs[0].sequence)
    new = InputScope(unknown={}, vin=vin)
    new.witness_utxo = coin.vout[0]
    new.non_witness_utxo = coin if prove else None
    new.bip32_derivations[key.key.get_public_key()] = DerivationPath(
        RECV.my_fingerprint, [84 + 2**31, 1 + 2**31, 2**31, 0, 0])
    p.inputs.append(new)
    pay = [i for i, o in enumerate(p.outputs) if o.script_pubkey.address(N["test"]) == DEST][0]
    p.outputs[pay].value += add_value
    change = [i for i in range(len(p.outputs)) if i != pay][0]
    p.outputs[change].value -= take_from_change
    for inp in p.inputs[:-1]:
        inp.final_scriptwitness = None
    if mutate:
        mutate(p, pay, change)
    p.sign_with(RECV)
    last = p.inputs[-1]
    (pub, sig), = last.partial_sigs.items()
    last.final_scriptwitness = Witness([sig, pub.sec()])
    last.partial_sigs.clear()
    last.bip32_derivations.clear()
    return p.to_string()


def _poster(**kw):
    def post(url, body):
        assert "disableoutputsubstitution=true" in url and "v=1" in url
        assert PSBT.from_string(body).inputs[0].final_scriptwitness is not None
        return 200, receiver_proposal(body, **kw)
    return post


def test_payjoin_happy_path(signer):  # noqa: F811
    w, s, signed = _setup(signer)
    out, note = pj.run(w, None, signer, "https://shop.example/pj", signed, s["fee_rate"],
                       poster=_poster(take_from_change=200))
    assert note == "PAYJOIN"
    tx = w.finalize(out)
    p = PSBT.from_string(out)
    parsed = Transaction.parse(tx.raw)
    assert len(parsed.vin) == 2
    verify_tx(parsed, [(p.inputs[i].witness_utxo.script_pubkey.data, p.inputs[i].witness_utxo.value)
                       for i in range(2)])


@pytest.mark.parametrize("bad, why", [
    (lambda p, pay, ch: setattr(p.outputs[pay], "value", p.outputs[pay].value - 60_000), "payment"),
    (lambda p, pay, ch: setattr(p.outputs[pay], "script_pubkey", p.outputs[ch].script_pubkey),
     "payment"),
    (lambda p, pay, ch: p.outputs.pop(ch), "missing"),
    (lambda p, pay, ch: setattr(p.outputs[ch], "value", p.outputs[ch].value - 5_000), "too much"),
    (lambda p, pay, ch: p.inputs.pop(0), "missing"),
    (lambda p, pay, ch: setattr(p.inputs[0], "sequence", 1), "sequence"),
])
def test_bad_proposals_send_the_original(signer, bad, why):  # noqa: F811
    w, s, signed = _setup(signer)
    out, note = pj.run(w, None, signer, "https://shop.example/pj", signed, s["fee_rate"],
                       poster=_poster(mutate=bad))
    assert out == signed and why in note.lower(), note
    with pytest.raises(SignerError):                     # one try only
        signer.sign_payjoin(psbt=receiver_proposal(w.finalized_psbt(signed)))


def test_receiver_errors_and_no_answer(signer):  # noqa: F811
    w, s, signed = _setup(signer)
    out, note = pj.run(w, None, signer, "https://shop.example/pj", signed, s["fee_rate"],
                       poster=lambda u, b: (400, '{"errorCode": "unavailable"}'))
    assert out == signed and "unavailable" in note

    def boom(u, b):
        raise OSError("timed out")
    w2, s2, signed2 = _setup(signer)
    out, note = pj.run(w2, None, signer, "https://shop.example/pj", signed2, s2["fee_rate"],
                       poster=boom)
    assert out == signed2 and "timed out" in note


def test_no_pending_payjoin_means_no_signature(signer):  # noqa: F811
    w = wallet_for(signer, "wpkh:0")
    fund(w, 100_000)
    built = w.build(DEST, 30_000, fee_rate=3)
    s = signer.describe(built["psbt"])
    signed = signer.sign(built["psbt"], s["digest"])      # not a PayJoin
    assert signer.payjoin_params() is None
    with pytest.raises(SignerError, match="no PayJoin"):
        signer.sign_payjoin(psbt=receiver_proposal(w.finalized_psbt(signed)))


def test_urls_and_payment_requests():
    for ok in ("https://btcpay.example.com/BTC/pj", "http://abcdefghij234567.onion/pj"):
        pj.check_url(ok)
    for bad in ("http://shop.example/pj", "ftp://x/pj", "https://user:pw@x/pj", "https://x/p j"):
        with pytest.raises(pj.PayjoinError):
            pj.check_url(bad)
    r = parse_bip21(f"bitcoin:{DEST}?amount=0.001&pj=https%3A%2F%2Fbtcpay.example.com%2FBTC%2Fpj")
    assert r.payjoin == "https://btcpay.example.com/BTC/pj" and r.sats == 100_000
    assert parse_bip21(f"bitcoin:{DEST}?pj=http://shop.example/pj").payjoin == ""
    url = pj.request_url("https://x/pj?id=1", 3.2, 1, 352)
    assert url.startswith("https://x/pj?id=1&v=1") and "maxadditionalfeecontribution=352" in url


def test_relay_refuses_bad_requests(tmp_path):
    import json
    import socket
    import threading
    from core import netd
    path = str(tmp_path / "net.sock")
    srv = netd.RelayServer(path, None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        for req in ({"op": "payjoin", "url": "http://shop.example/pj", "body": "cHNidP8BAA=="},
                    {"op": "payjoin", "url": "https://x/pj", "body": "<script>"},
                    {"op": "payjoin", "url": "https://x/pj", "body": "cHNidP8BAA==", "tor": "1.2.3.4:9050"}):
            s = socket.socket(socket.AF_UNIX)
            s.connect(path)
            f = s.makefile("rwb")
            f.write(json.dumps(req).encode() + b"\n")
            f.flush()
            assert json.loads(f.readline())["ok"] is False
            s.close()
    finally:
        srv.shutdown()
        srv.server_close()
    assert base64


def test_the_sign_prompt_keeps_the_original(app):  # noqa: F811
    from test_app import create_wallet, enter, press, settle
    create_wallet(app)
    fund(app.wallet, 100_000)
    built = app.wallet.build(DEST, 30_000, fee_rate=3)
    got = []
    app.prompt("sign_prompt", lambda app, r: got.append(r), psbt=built["psbt"], payjoin=True)
    settle(app)
    press(app, "KEY_Z")                              # YES
    enter(app)
    settle(app)
    assert got and app.signer._local.signer.payjoin_params()["max"] > 0


def test_damaged_pj_links_dont_break_scanning():
    """Found by the fuzzer: urlparse raises ValueError on some junk."""
    for junk in ("https://[::1/pj", "https://x:99999/pj", "https://ex]ample.com/pj"):
        with pytest.raises(pj.PayjoinError):
            pj.check_url(junk)
        r = parse_bip21(f"bitcoin:{DEST}?pj={junk}")
        assert r.payjoin == "" and r.address == DEST


def test_spec_receiver_without_our_amounts_works(signer):  # noqa: F811
    """Found in review: BIP-78 receivers strip the sender's utxo data; the
    fee must come from the signer's own copy, not be refused."""
    def post(url, body):
        p = PSBT.from_string(receiver_proposal(body, take_from_change=200))
        for inp in p.inputs[:-1]:                    # as BIP-78 says: only the receiver's
            inp.witness_utxo = None
            inp.non_witness_utxo = None
        return 200, p.to_string()
    w, s, signed = _setup(signer)
    out, note = pj.run(w, None, signer, "https://shop.example/pj", signed, s["fee_rate"],
                       poster=post)
    assert note == "PAYJOIN", note


def test_overstated_amount_cant_steal_the_contribution(signer):  # noqa: F811
    """Found in review: a receiver inflating your input's amount used to pass
    the fee checks while moving your contribution into its own output."""
    def cheat(p, pay, ch):
        p.inputs[0].witness_utxo.value += 900_000
        p.outputs[pay].value += 300                   # the 300 taken from change goes to them
    w, s, signed = _setup(signer)
    out, note = pj.run(w, None, signer, "https://shop.example/pj", signed, s["fee_rate"],
                       poster=_poster(take_from_change=300, mutate=cheat))
    assert out == signed and "fee" in note, note


def test_unproven_receiver_coin_means_no_contribution(signer):  # noqa: F811
    w, s, signed = _setup(signer)
    out, note = pj.run(w, None, signer, "https://shop.example/pj", signed, s["fee_rate"],
                       poster=_poster(take_from_change=200, prove=False))
    assert out == signed and "didn't" in note, note
    w2, s2, signed2 = _setup(signer)
    out, note = pj.run(w2, None, signer, "https://shop.example/pj", signed2, s2["fee_rate"],
                       poster=_poster(take_from_change=0, prove=False))
    assert note == "PAYJOIN", note
