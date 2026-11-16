"""
Throw junk at everything that reads data from outside the wallet and report
anything that isn't a clean, expected refusal: a crash with an unexpected
exception type, a hang, or a huge memory grab.

    python3 tools/fuzz.py [iterations]
"""
import os
import random
import resource
import struct
import signal
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault("CZ_DATA", tempfile.mkdtemp(prefix="czfuzz-"))
os.environ.setdefault("CZ_ARGON_T", "1")
os.environ.setdefault("CZ_ARGON_M_KIB", "1024")

# 1 GB address-space cap: a parser that tries to allocate gigabytes fails fast
resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))

FOUND = {}


class Hang(Exception):
    pass


def _alarm(*_):
    raise Hang()


signal.signal(signal.SIGALRM, _alarm)


def check(name, fn, data, ok=()):
    signal.setitimer(signal.ITIMER_REAL, 2.0)
    try:
        fn(data)
    except ok:
        pass
    except Exception as e:  # noqa: BLE001
        key = (name, type(e).__name__, traceback.extract_tb(e.__traceback__)[-1].lineno)
        if key not in FOUND:
            FOUND[key] = (repr(data)[:200], traceback.format_exc()[-1200:])
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def mutate(rnd, b: bytes) -> bytes:
    b = bytearray(b)
    for _ in range(rnd.randint(1, 6)):
        op = rnd.random()
        if op < 0.4 and b:
            b[rnd.randrange(len(b))] = rnd.randrange(256)
        elif op < 0.6 and b:
            i = rnd.randrange(len(b))
            del b[i:i + rnd.randint(1, 8)]
        elif op < 0.8:
            i = rnd.randrange(len(b) + 1)
            b[i:i] = bytes(rnd.randrange(256) for _ in range(rnd.randint(1, 8)))
        else:
            i = rnd.randrange(len(b) + 1)
            b[i:i] = rnd.choice([b"\xff" * 9, b"\x1b\xff\xff\xff\xff\xff\xff\xff\xff",
                                 b"\xc6" * 50, b"\x9b\xff\xff\xff\xff\xff\xff\xff\xff",
                                 b"\x00", b"\x80"])
    return bytes(b)


def mutate_text(rnd, s: str) -> str:
    return mutate(rnd, s.encode()).decode("latin-1")


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    rnd = random.Random(7)
    from core import qr, ur, cbor, cktap, multisig
    from core.qr import Collector

    # ------------------------------------------------ valid seeds to mutate
    psbt = open(os.path.join(ROOT, "tests", "data", "sample.psbt"), "rb").read() \
        if os.path.exists(os.path.join(ROOT, "tests", "data", "sample.psbt")) else \
        bytes.fromhex("70736274ff01005e02000000010000000000000000000000000000000000000000"
                      "000000000000000000000000000000000000ffffffff0110270000000000001600"
                      "14751e76e8199196d454941c45d1b3a323f1433bd6000000000000")
    ur_parts = ur.ur_encode(psbt * 3, "crypto-psbt", 40)
    bb_parts = qr.bbqr_encode(psbt * 3, "P", 40)
    texts = ["bitcoin:tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx?amount=0.001&label=hi%20x",
             "lnbc1...", psbt.hex(), __import__("base64").b64encode(psbt).decode(),
             "0" * 48, "tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx",
             "ur:bytes/" + ur.bytewords_encode(ur.cbor_encode(b"lnbc1xyz")),
             "B$2U0100" + qr._b32encode(b"lightning:lnbc1")]

    def collect(frames):
        c = Collector()
        for f in frames:
            c.add(f)
            if c.result:
                break

    for i in range(n):
        # single QR strings
        t = mutate_text(rnd, rnd.choice(texts))
        check("Collector.add(single)", lambda d: collect([d]), t)
        check("classify", qr.classify, t, ok=(qr.QRFormatError,))
        # animated UR and BBQr with one or two damaged frames
        frames = list(ur_parts)
        for _ in range(rnd.randint(1, 2)):
            j = rnd.randrange(len(frames))
            frames[j] = mutate_text(rnd, frames[j])
        rnd.shuffle(frames)
        check("Collector.add(UR)", collect, frames * 2)
        frames = list(bb_parts)
        j = rnd.randrange(len(frames))
        frames[j] = mutate_text(rnd, frames[j])
        check("Collector.add(BBQr)", collect, frames)
        # crafted single UR parts with hostile CBOR
        body = mutate(rnd, ur.cbor_encode([1, 3, 100, 12345, b"abcdefghij"]))
        check("Collector.add(UR crafted)", lambda d: collect([d]),
              "ur:crypto-psbt/1-3/" + ur.bytewords_encode(body))
        # raw CBOR decoders
        raw = mutate(rnd, ur.cbor_encode([1, [2, b"xyz"], b"\x00" * 5]))
        check("ur.cbor_decode", ur.cbor_decode, raw, ok=(ur.URError,))
        check("cbor.decode", cbor.decode, mutate(rnd, cbor.encode({"a": 1, "b": [b"x", "y"]})),
              ok=(cbor.CBORError,))

    # ------------------------------------------------ multisig descriptors
    desc = ("wsh(sortedmulti(2,[73c5da0a/48h/1h/0h/2h]tpubDFH9dgzveyD8zTbPUFuLrGmCydNvxehyNdUXKJAQN8x4aZ4j6UZqGfnqFrD4NqyaTVGKbvEW54tsvPTK2UoSjCC1PJY8iCNiwTL3RWZEheQ/<0;1>/*,"
            "[f245ae38/48h/1h/0h/2h]tpubDDUBeeT8CVSEPYvDHHrjpJF4zVdrqjeYRJkS4TMdtj9F8rLkWZYodUCh2G6oN7SuxKLCvNVwSfnLPRVvBf6qbfNB7rHkGqZSHzwZCvCfFbr/<0;1>/*,"
            "[c5d87297/48h/1h/0h/2h]tpubDF3wwZcLHzeQCpNz6fyJMjFNFqZyjWNHwSEqnMxpT3fq6Cfn29H4hbKVxPLjXCWvLNVVpXBqCvLrYKSi7cdPjQJr5rM3mhuJjS7ymqJRyhV/<0;1>/*))")
    for i in range(n // 3):
        check("multisig.parse_any", lambda d: multisig.parse_any(d, "signet"),
              mutate_text(rnd, desc), ok=(multisig.PolicyError,))

    # ------------------------------------------------ card replies
    good = cbor.encode({"proto": 1, "ver": "1.0", "birth": 1, "slots": [0, 10],
                        "addr": "tb1q...", "pubkey": b"\x02" + b"\x11" * 32,
                        "card_nonce": b"x" * 16, "testnet": True})
    for i in range(n // 3):
        reply = mutate(rnd, good)

        def card_ops(r):
            c = cktap.Card(lambda apdu: (r, 0x9000))
            c.select()
            c.slot()
        check("cktap.Card(status reply)", card_ops, reply, ok=(cktap.CardError,))

    # --- 0.8.19 / 0.8.20: printing, discovery, paper files, spending policy
    from core import ipp, mdns, paper, policy, printing
    good_ipp = ipp.encode_request(ipp.GET_PRINTER_ATTRIBUTES, 1, "ipps://10.0.0.5:631/ipp/print",
                                  [(ipp.KEYWORD, "requested-attributes", "printer-state")])
    for i in range(n // 3):
        check("ipp.decode", ipp.decode, mutate(rnd, good_ipp), ok=(ipp.PrinterError,))
        check("mdns.parse", mdns.parse, mutate(rnd, mdns.query()),
              ok=(ValueError, struct.error, IndexError))
        check("ipp.local_ip", ipp.local_ip, mutate_text(rnd, "192.168.1.40"),
              ok=(ipp.PrinterError,))
    w = paper.build(paper.random_key(), "signet")
    good_txt = paper.text_file(w)
    for i in range(n // 3):
        check("paper.from_text", paper.from_text, mutate_text(rnd, good_txt),
              ok=(paper.PaperError,))
    svc = printing.PrintService(tempfile.mkdtemp(), tempfile.mkdtemp())
    junk = [None, True, -1, 2 ** 70, "", "x" * 5000, "../..", [], {}, 3.5, "192.168.1.1"]
    for i in range(n // 3):
        params = {rnd.choice(["address", "host", "port", "path", "fingerprint", "x"]):
                  rnd.choice(junk) for _ in range(rnd.randrange(0, 4))}
        method = rnd.choice(["files", "check", "trust", "forget", "print", "nope"])
        check("printing.handle", lambda pm: svc.handle(method, pm), params,
              ok=(printing.PrintError, ipp.PrinterError))
    p0 = dict(policy.DEFAULT)
    for i in range(n // 3):
        field = rnd.choice(sorted(policy.FIELDS) + ["bogus"])
        check("policy.change", lambda v: policy.change(p0, field, v), rnd.choice(junk),
              ok=(policy.PolicyError,))
        check("policy.verify_code", lambda c: policy.verify_code(policy.new_secret(), c, 0),
              rnd.choice(junk[4:6] + ["123456", "abcdef", "12345"]), ok=(policy.PolicyError,
                                                                           AttributeError))

    print(f"{len(FOUND)} distinct problems")
    for (name, et, line), (data, tb) in FOUND.items():
        print("=" * 70)
        print(f"{name}: {et} (line {line})\ninput: {data}\n{tb}")


if __name__ == "__main__":
    main()
