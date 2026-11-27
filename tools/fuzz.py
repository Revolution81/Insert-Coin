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


def ext_bus_loaded(out: str):
    import types

    import ext_bus
    real = ext_bus._run
    ext_bus._run = lambda cmd, timeout=20: types.SimpleNamespace(returncode=0, stdout=out, stderr="")
    try:
        got = ext_bus._loaded()
    finally:
        ext_bus._run = real
    assert all(i.isdigit() for i in got)
    return got


def grove_idem(text: str):
    import migrate
    once = migrate.grove_block(text)
    assert migrate.grove_block(once) == once, "not idempotent"
    assert "gpio=4=op,dl" in once


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

    # 0.8.21: node server strings (typed or scanned), the keyboard driver's
    # keymap file, the power-supply readings
    from core.config import parse_server
    from ui.input import load_driver_symbols
    from core import power
    seeds = ["umbrel.local:50003:t", "tcp://192.168.1.20:50001", "ssl://h.example:50002",
             "abc.onion:50001:t", "host:1"]
    tmp = __import__("tempfile").mkdtemp()
    for i in range(n // 3):
        check("parse_server", parse_server, mutate_text(rnd, rnd.choice(seeds)), ok=(ValueError,))
        km = os.path.join(tmp, "k.map")
        with open(km, "wb") as f:
            f.write(mutate(rnd, b"keycode 80 = colon\nkeycode 92 = slash # x\n"))
        check("load_driver_symbols", load_driver_symbols, km)
        d = os.path.join(tmp, f"ps{i % 4}")
        os.makedirs(d, exist_ok=True)
        for name, val in (("type", b"Battery"), ("capacity", b"57"), ("status", b"Charging")):
            with open(os.path.join(d, name), "wb") as f:
                f.write(mutate(rnd, val))
        check("power.read", power.read, tmp)

    # the toolbox: silent payment addresses, PayJoin URLs and proposals,
    # Key Teleport packages and secrets, notes from a teleport, miniscript
    from core import notes, payjoin, silentpay, teleport
    from core.hw import nfc, unit_relay
    sys.path.insert(0, HERE)
    import migrate
    import app as app_mod
    from embit.psbt import PSBT
    sp_addr = silentpay.encode(b"\x02" + b"\x11" * 32, b"\x03" + b"\x22" * 32, "signet")
    sp_addr = silentpay.encode(silentpay.ser(silentpay.base(5)), silentpay.ser(silentpay.base(7)),
                               "signet")
    rx, tx = teleport.new_secret(), teleport.new_secret()
    noid, _ = teleport.new_password()
    pkg = teleport.seal(tx, teleport.pubkey(rx), noid, b"n" + teleport.notes_out(
        [{"kind": "note", "title": "t", "text": "x"}]))
    mini = ("wsh(or_d(pk([73c5da0a/48h/1h/0h/2h]tpubDFH9dgzveyD8zTbPUFuLrGmCydNvxehyNdUXKJAQN8x4aZ4j6UZqGfnqFrD4NqyaTVGKbvEW54tsvPTK2UoSjCC1PJY8iCNiwTL3RWZEheQ/<0;1>/*),"
            "and_v(v:pk([f245ae38/48h/1h/0h/2h]tpubDDUBeeT8CVSEPYvDHHrjpJF4zVdrqjeYRJkS4TMdtj9F8rLkWZYodUCh2G6oN7SuxKLCvNVwSfnLPRVvBf6qbfNB7rHkGqZSHzwZCvCfFbr/<0;1>/*),older(100))))")
    from embit.transaction import Transaction, TransactionInput, TransactionOutput
    from embit.script import Script
    _t = Transaction(vin=[TransactionInput(b"\x11" * 32, 0, sequence=0xfffffffd)],
                     vout=[TransactionOutput(5000, Script(b"\x00\x14" + b"\x22" * 20)),
                           TransactionOutput(9000, Script(b"\x00\x14" + b"\x33" * 20))])
    orig = PSBT(_t)
    orig.inputs[0].witness_utxo = TransactionOutput(15000, Script(b"\x00\x14" + b"\x44" * 20))
    psbt = orig.serialize()
    for i in range(n // 3):
        check("silentpay.decode", lambda d: silentpay.decode(d, "signet"),
              mutate_text(rnd, sp_addr), ok=(silentpay.SilentPaymentError,))
        check("silentpay.input_pubkey", lambda d: silentpay.input_pubkey(d[:34], d[34:], [d[5:38]]),
              mutate(rnd, b"\x00\x14" + b"\x01" * 20 + b"\x02" * 40))
        check("payjoin.check_url", payjoin.check_url,
              mutate_text(rnd, "https://btcpay.example.com/BTC/pj?x=1"), ok=(payjoin.PayjoinError,))
        check("payjoin.error_text", payjoin.error_text, mutate_text(rnd, '{"errorCode":"x"}'))

        try:                                 # embit's own parse errors: the signer
            prop = PSBT.parse(mutate(rnd, psbt))   # catches those; fuzz what parses
        except Exception:  # noqa: BLE001
            prop = None
        if prop is not None:
            check("payjoin.check_proposal",
                  lambda p: payjoin.check_proposal(orig, p, 0, set(), 500, None), prop,
                  ok=(payjoin.PayjoinError,))
        check("teleport.open_outer", lambda d: teleport.open_outer(rx, d[:33], d[33:]),
              mutate(rnd, pkg))
        check("teleport.decrypt_rx", lambda d: teleport.decrypt_rx("12345678", d),
              mutate(rnd, teleport.rx_code(rx)[1]))
        check("teleport.decode_secret", lambda d: teleport.decode_secret(d, b"\x04\x35\x83\x94"),
              mutate(rnd, teleport.encode_secret("abandon " * 11 + "about")),
              ok=(teleport.TeleportError,))
        check("teleport.notes_in", teleport.notes_in, mutate(rnd, teleport.notes_out(
            [{"kind": "password", "title": "a", "user": "", "password": "p"}])),
            ok=(teleport.TeleportError,))
        check("teleport.vault_in", teleport.vault_in,
              mutate(rnd, teleport.vault_out("abcd1234", b"\x80" + b"\x01" * 16, "L")),
              ok=(teleport.TeleportError,))
        check("teleport.backup_in", teleport.backup_in,
              mutate(rnd, b'raw_secret = "80' + b"11" * 16 + b'"\nsetting.notes = []\n'),
              ok=(teleport.TeleportError,))
        check("notes.clean", notes.clean, {"kind": rnd.choice(["note", "password", 3]),
                                           "title": rnd.choice(junk), "text": rnd.choice(junk),
                                           "password": rnd.choice(junk)}, ok=(notes.NoteError,))
        check("multisig.parse_any(miniscript)", lambda d: multisig.parse_any(d, "signet"),
              mutate_text(rnd, mini), ok=(multisig.PolicyError,))

        # ---- 0.8.42+: side port, the signer's NFC relay, the EXT bus helper
        def junk_msg():
            pick = rnd.randrange(8)
            if pick == 0:
                return [rnd.choice([0, 1, 2, -1, 0x8000, "0", None]), rnd.choice(junk)]
            if pick == 1:
                return [0, mutate(rnd, b"c1").hex() if rnd.random() < .5 else mutate_text(rnd, "c1")]
            if pick == 2:
                return [1, rnd.choice([0, 1, 300, 301, -5, 2**40, 1.5, "3", None])]
            if pick == 3:
                return rnd.choice(junk)
            return [rnd.choice([0, 1]), rnd.choice([rnd.randrange(400), "ab" * rnd.randrange(320)])]
        msgs = [junk_msg() for _ in range(rnd.randrange(4))] if rnd.random() < .9 else rnd.choice(junk)
        check("unit_relay._check", unit_relay.UnitRelay._check, msgs, ok=(unit_relay.RelayError,))

        reply = mutate_text(rnd, "0a0b0c") if rnd.random() < .5 else mutate(rnd, b"\x0a\x0b").hex()
        check("nfc.RelayI2C.xfer", lambda r: nfc.RelayI2C(lambda wire: r).xfer([0x40 | 0x3F, 0, 0]),
              reply, ok=(ValueError,))

        listing = mutate_text(rnd, "Overlays (in load order):\n0:  i2c-gpio  bus=3 i2c_gpio_sda=23\n"
                                   "1:  spi1-1cs\n")
        check("ext_bus._loaded", lambda out: ext_bus_loaded(out), listing)

        cfg = mutate_text(rnd, "dtparam=i2c_arm=on\n[cm4]\notg_mode=1\n" + migrate.grove_block(""))
        check("migrate.grove_block (once, same twice)", grove_idem, cfg)

        st = {"found": [rnd.choice([{"where": rnd.choice(["GROVE", "GPIO", 3, None])}, junk[0], {}])
                        for _ in range(rnd.randrange(3))]}
        check("App._chip_at", lambda d: app_mod.App._chip_at(d, "GROVE"), st)

    print(f"{len(FOUND)} distinct problems")
    for (name, et, line), (data, tb) in FOUND.items():
        print("=" * 70)
        print(f"{name}: {et} (line {line})\ninput: {data}\n{tb}")


if __name__ == "__main__":
    main()
