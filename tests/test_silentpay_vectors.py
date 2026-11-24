"""BIP-352 (silent payments): every sending and receiving test vector from
the BIP (tests/data/bip352_vectors.json, copied from bitcoin/bips)."""
import io
import json
import os

import pytest

from core import silentpay as sp

HERE = os.path.dirname(__file__)
VECTORS = json.load(open(os.path.join(HERE, "data", "bip352_vectors.json")))


def _witness(hexstr):
    if not hexstr:
        return []
    f = io.BytesIO(bytes.fromhex(hexstr))

    def cs():
        b = f.read(1)[0]
        if b < 0xFD:
            return b
        return int.from_bytes(f.read({0xFD: 2, 0xFE: 4, 0xFF: 8}[b]), "little")
    return [f.read(cs()) for _ in range(cs())]


def _inputs(vins):
    pubs, outpoints, keys = [], [], []
    for v in vins:
        spk = bytes.fromhex(v["prevout"]["scriptPubKey"]["hex"])
        pk = sp.input_pubkey(spk, bytes.fromhex(v["scriptSig"]), _witness(v["txinwitness"]))
        outpoints.append((v["txid"], v["vout"]))
        if pk is None:
            continue
        pubs.append(pk)
        if "private_key" in v:
            keys.append((int(v["private_key"], 16), len(spk) == 34 and spk[:2] == b"\x51\x20"))
    return pubs, outpoints, keys


@pytest.mark.parametrize("case", VECTORS, ids=[c["comment"][:50] for c in VECTORS])
def test_vector(case):
    for t in case["sending"]:
        g, e = t["given"], t["expected"]
        pubs, outpoints, keys = _inputs(g["vin"])
        assert [sp.ser(p).hex() for p in pubs] == e.get("input_pub_keys", [sp.ser(p).hex() for p in pubs])
        recips = []
        for r in g["recipients"]:
            scan, spend = sp.decode(r["address"], "bitcoin")
            assert scan.hex() == r["scan_pub_key"] and spend.hex() == r["spend_pub_key"]
            recips += [(scan, spend)] * r.get("count", 1)
        try:
            got = [o.hex() for o in sp.outputs(keys, outpoints, recips)] if keys else []
        except sp.SilentPaymentError:
            got = []
        assert any(set(got) == set(opt) for opt in e["outputs"])
    for t in case["receiving"]:
        g, e = t["given"], t["expected"]
        b_scan = int(g["key_material"]["scan_priv_key"], 16)
        b_spend = int(g["key_material"]["spend_priv_key"], 16)
        scan_pub, spend_pub = sp.ser(sp.base(b_scan)), sp.ser(sp.base(b_spend))
        addrs = [sp.encode(scan_pub, spend_pub, "bitcoin")]
        labels = {}
        for m in g["labels"]:
            lt = sp.label_tweak(b_scan, m)
            labels[sp.ser(sp.base(lt))] = lt
            addrs.append(sp.encode(scan_pub, sp.ser(sp.add(sp.base(b_spend), sp.base(lt))), "bitcoin"))
        assert addrs == e["addresses"]
        pubs, outpoints, _ = _inputs(g["vin"])
        found = []
        a_sum = None
        for p in pubs:
            a_sum = sp.add(a_sum, p)
        if pubs and a_sum is not None:
            tw = sp.tweak_point(outpoints, pubs)
            assert sp.ser(tw).hex() == e.get("tweak", sp.ser(tw).hex())
            found = sp.scan(b_scan, sp.base(b_spend), tw, [bytes.fromhex(o) for o in g["outputs"]],
                            labels)
        if "outputs" in e:
            assert {(o.hex(), t.to_bytes(32, "big").hex()) for o, t in found} == \
                {(o["pub_key"], o["priv_key_tweak"]) for o in e["outputs"]}
            for o, t in found:                       # the key really opens the output
                assert sp.ser(sp.base(sp.spend_key(b_spend, t)))[1:] == o
        else:
            assert len(found) == e["n_outputs"]


def test_pure_python_maths_agree(monkeypatch):
    """Without libsecp256k1 (embit's pure-Python mode) the results are the same."""
    monkeypatch.setattr(sp, "_lib", lambda: None)
    test_vector(VECTORS[0])
    test_vector(VECTORS[5])
