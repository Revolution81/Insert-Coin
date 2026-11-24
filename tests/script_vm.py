"""
A small Bitcoin Script interpreter for tests: enough to run the scripts this
wallet makes (P2WPKH, P2WSH sortedmulti, the inheritance miniscript, P2TR
key-path, and P2TR script-path for sortedmulti_a) against
a real transaction, with real signature checks (BIP143) and BIP112
CHECKSEQUENCEVERIFY. It's a test oracle, not consensus code.
"""
from __future__ import annotations

import hashlib

from embit import ec
from embit.hashes import hash160
from embit.script import Script


class ScriptFail(Exception):
    pass


def _num(b: bytes) -> int:
    if not b:
        return 0
    n = int.from_bytes(b, "little")
    if b[-1] & 0x80:
        return -(n & ~(0x80 << (8 * (len(b) - 1))))
    return n


def _true(b: bytes) -> bool:
    for i, x in enumerate(b):
        if x != 0:
            return not (i == len(b) - 1 and x == 0x80)
    return False


def parse(script: bytes) -> list:
    ops, i = [], 0
    while i < len(script):
        op = script[i]
        i += 1
        if 1 <= op <= 75:
            ops.append(script[i:i + op])
            i += op
        elif op == 0x4C:
            n = script[i]
            ops.append(script[i + 1:i + 1 + n])
            i += 1 + n
        elif op == 0x00:
            ops.append(b"")
        elif 0x51 <= op <= 0x60:
            ops.append(bytes([op - 0x50]))
        else:
            ops.append(op)
    return ops


def run(script: bytes, stack: list[bytes], tx, i: int, value: int):
    def checksig(sig, pub):
        if not sig:
            return False
        h = tx.sighash_segwit(i, Script(script), value, sighash=sig[-1])
        return ec.PublicKey.parse(pub).verify(ec.Signature.parse(sig[:-1]), h)

    st = list(stack)
    cond: list[bool] = []
    for op in parse(script):
        live = all(cond)
        if isinstance(op, bytes):
            if live:
                st.append(op)
            continue
        if op in (0x63, 0x64):                    # IF / NOTIF
            v = False
            if live:
                v = _true(st.pop())
                if op == 0x64:
                    v = not v
            cond.append(v)
            continue
        if op == 0x67:                            # ELSE
            cond[-1] = not cond[-1]
            continue
        if op == 0x68:                            # ENDIF
            cond.pop()
            continue
        if not live:
            continue
        if op == 0x76:                            # DUP
            st.append(st[-1])
        elif op == 0x73:                          # IFDUP
            if _true(st[-1]):
                st.append(st[-1])
        elif op == 0xA9:                          # HASH160
            st.append(hash160(st.pop()))
        elif op in (0x87, 0x88):                  # EQUAL(VERIFY)
            ok = st.pop() == st.pop()
            if op == 0x88:
                if not ok:
                    raise ScriptFail("EQUALVERIFY")
            else:
                st.append(b"\x01" if ok else b"")
        elif op in (0xAC, 0xAD):                  # CHECKSIG(VERIFY)
            pub, sig = st.pop(), st.pop()
            ok = checksig(sig, pub)
            if op == 0xAD:
                if not ok:
                    raise ScriptFail("CHECKSIGVERIFY")
            else:
                st.append(b"\x01" if ok else b"")
        elif op == 0xAE:                          # CHECKMULTISIG
            n = _num(st.pop())
            keys = [st.pop() for _ in range(n)][::-1]
            m = _num(st.pop())
            sigs = [st.pop() for _ in range(m)][::-1]
            if st.pop() != b"":
                raise ScriptFail("NULLDUMMY")
            k = 0
            for sig in sigs:
                while k < len(keys) and not checksig(sig, keys[k]):
                    k += 1
                if k == len(keys):
                    st.append(b"")
                    break
                k += 1
            else:
                st.append(b"\x01")
        elif op == 0x69:                          # VERIFY
            if not _true(st.pop()):
                raise ScriptFail("VERIFY")
        elif op == 0xB2:                          # CHECKSEQUENCEVERIFY (BIP112)
            n = _num(st[-1])
            if n < 0:
                raise ScriptFail("negative CSV")
            if not n & (1 << 31):
                seq = tx.vin[i].sequence
                if tx.version < 2 or seq & (1 << 31):
                    raise ScriptFail("CSV: tx can't satisfy relative locks")
                mask = (1 << 22) | 0xFFFF
                if (n & (1 << 22)) != (seq & (1 << 22)) or (seq & mask & 0xFFFF) < (n & 0xFFFF):
                    raise ScriptFail("CSV: not old enough")
        else:
            raise ScriptFail(f"opcode {op:#x} not supported")
    if cond:
        raise ScriptFail("unbalanced IF")
    if len(st) != 1 or not _true(st[-1]):
        raise ScriptFail("script didn't leave exactly one true value")


def verify_input(tx, i: int, prev_script: bytes, value: int, prevouts=None):
    """Raises ScriptFail unless input i correctly spends prev_script.
    prevouts (every input's (script, value)) is needed for Taproot."""
    try:
        if len(prev_script) == 34 and prev_script[:2] == b"\x51\x20":
            _verify_taproot(tx, i, prev_script, prevouts)
        else:
            _verify_input(tx, i, prev_script, value)
    except (IndexError, ValueError) as e:      # stack underflow, bad encodings
        raise ScriptFail(f"script failed: {e!r}")


def _verify_tapscript(tx, i, prev_script, prevouts, w):
    """BIP341/342 script-path spend: the control block must commit the leaf
    to the output key (merkle path, tweak, parity), then the leaf runs with
    Schnorr CHECKSIG / CHECKSIGADD / NUMEQUAL(VERIFY) / VERIFY."""
    from embit.hashes import tagged_hash
    from embit.transaction import SIGHASH
    cb, script, stack = w[-1], w[-2], w[:-2]
    if len(cb) < 33 or (len(cb) - 33) % 32:
        raise ScriptFail("bad control block")
    ver = cb[0] & 0xFE
    h = tagged_hash("TapLeaf", bytes([ver]) + Script(script).serialize())
    for j in range(33, len(cb), 32):
        node = cb[j:j + 32]
        h = tagged_hash("TapBranch", h + node if h < node else node + h)
    out = ec.PublicKey.from_xonly(cb[1:33]).taproot_tweak(h)
    if out.xonly() != prev_script[2:] or (out.sec()[0] & 1) != (cb[0] & 1):
        raise ScriptFail("control block doesn't commit to the output")

    def checksig(sig, pub):
        if not sig:
            return False
        sighash = sig[64] if len(sig) == 65 else SIGHASH.DEFAULT
        m = tx.sighash_taproot(i, script_pubkeys=[Script(s) for s, _ in prevouts],
                               values=[v for _, v in prevouts], sighash=sighash, ext_flag=1,
                               script=Script(script), leaf_version=ver)
        if not ec.PublicKey.from_xonly(pub).schnorr_verify(ec.SchnorrSig.parse(sig[:64]), m):
            raise ScriptFail("bad Schnorr signature")   # BIP342: a non-empty bad sig fails
        return True

    st = list(stack)
    for op in parse(script):
        if isinstance(op, bytes):
            st.append(op)
        elif op == 0xAC:                          # CHECKSIG
            pub, sig = st.pop(), st.pop()
            st.append(b"\x01" if checksig(sig, pub) else b"")
        elif op == 0xBA:                          # CHECKSIGADD
            pub, n, sig = st.pop(), _num(st.pop()), st.pop()
            n += 1 if checksig(sig, pub) else 0
            st.append(bytes([n]) if n else b"")
        elif op in (0x9C, 0x9D):                  # NUMEQUAL(VERIFY)
            ok = _num(st.pop()) == _num(st.pop())
            if op == 0x9D:
                if not ok:
                    raise ScriptFail("NUMEQUALVERIFY")
            else:
                st.append(b"\x01" if ok else b"")
        elif op == 0x69:
            if not _true(st.pop()):
                raise ScriptFail("VERIFY")
        else:
            raise ScriptFail(f"tapscript opcode {op:#x} not supported")
    if len(st) != 1 or not _true(st[-1]):
        raise ScriptFail("script didn't leave exactly one true value")


def _verify_taproot(tx, i, prev_script, prevouts):
    """BIP341 key-path spend: one Schnorr signature by the output key, or a
    script-path spend (_verify_tapscript). (The sighash comes from embit; the
    key, tweak and signature are checked here.)"""
    from embit.script import Script
    from embit.transaction import SIGHASH
    w = list(tx.vin[i].witness.items)
    if len(w) >= 2 and w[-1][:1] and (w[-1][0] & 0xFE) == 0xC0:
        _verify_tapscript(tx, i, prev_script, prevouts, w)
        return
    if len(w) != 1 or len(w[0]) not in (64, 65):
        raise ScriptFail("not a key-path spend")
    sig = w[0]
    sighash = sig[64] if len(sig) == 65 else SIGHASH.DEFAULT
    if len(sig) == 65 and sighash == SIGHASH.DEFAULT:
        raise ScriptFail("explicit SIGHASH_DEFAULT byte is invalid")
    h = tx.sighash_taproot(i, script_pubkeys=[Script(s) for s, _ in prevouts],
                           values=[v for _, v in prevouts], sighash=sighash)
    if not ec.PublicKey.from_xonly(prev_script[2:]).schnorr_verify(
            ec.SchnorrSig.parse(sig[:64]), h):
        raise ScriptFail("bad Schnorr signature")


def _verify_input(tx, i: int, prev_script: bytes, value: int):
    w = list(tx.vin[i].witness.items)
    if len(prev_script) == 22 and prev_script[:2] == b"\x00\x14":         # P2WPKH
        sig, pub = w
        if hash160(pub) != prev_script[2:]:
            raise ScriptFail("P2WPKH key mismatch")
        code = b"\x76\xa9\x14" + prev_script[2:] + b"\x88\xac"
        run(code, [sig, pub], tx, i, value)
        return
    if len(prev_script) == 34 and prev_script[:2] == b"\x00\x20":         # P2WSH
        ws = w[-1]
        if hashlib.sha256(ws).digest() != prev_script[2:]:
            raise ScriptFail("witness script hash mismatch")
        run(ws, w[:-1], tx, i, value)
        return
    raise ScriptFail("unknown output type")


def verify_tx(tx, prevouts: list[tuple[bytes, int]]):
    for i, (script, value) in enumerate(prevouts):
        verify_input(tx, i, script, value, prevouts)
