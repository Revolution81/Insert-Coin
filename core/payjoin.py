"""
PayJoin (BIP-78), sending side. A payment request with pj=<url> (BTCPay
and other merchants) lets the receiver add a coin of their own to your
payment, which breaks the "all inputs belong to the payer" guess chain
analysis relies on.

1. You confirm the payment as usual; the signer signs it (the "original",
   a complete transaction that can be broadcast as it is).
2. The app sends it to the receiver's pj address (through cznet, over Tor
   unless it's a home server) and gets back a "proposal": the same payment
   with the receiver's input(s) added.
3. The signer checks the proposal against the original it signed
   (check_proposal) and signs only its own inputs; if anything is off, or
   the receiver doesn't answer, the original goes out instead.

The rules (BIP-78's sender checklist, made stricter): the payment output
keeps its address and doesn't shrink (we always ask for no output
substitution, so the address you confirmed is where the money goes); none
of your other outputs disappear; your change pays at most the extra fee
you allowed (maxadditionalfeecontribution, about 110 vB at the original's
fee rate) and only toward fee; your inputs are exactly the original's;
version, lock time and your sequences unchanged; the receiver's inputs
come finalized, yours unsigned.
"""
from __future__ import annotations

import base64
import json
import re
import socket
import ssl
from urllib.parse import quote, urlencode, urlparse

from embit.psbt import PSBT

MAX_BODY = 400_000
EXTRA_VB = 110                          # BIP-78's suggested contribution: rate * 110 vB
_HOST = re.compile(r"^[A-Za-z0-9.-]{1,253}$")


class PayjoinError(Exception):
    pass


def check_url(url: str) -> tuple[str, str, int, str]:
    """(scheme, host, port, path+query) of a pj endpoint: https anywhere, or
    plain http only to a .onion address (Tor encrypts that end to end)."""
    if not isinstance(url, str) or len(url) > 1000:
        raise PayjoinError("bad PayJoin address")
    u = urlparse(url.strip())
    host = (u.hostname or "").lower()
    if u.scheme not in ("https", "http") or not _HOST.match(host) or u.username or u.password:
        raise PayjoinError("bad PayJoin address")
    if u.scheme == "http" and not host.endswith(".onion"):
        raise PayjoinError("PayJoin needs https (or a .onion address)")
    port = u.port or (443 if u.scheme == "https" else 80)
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    if any(c in path for c in "\r\n "):
        raise PayjoinError("bad PayJoin address")
    return u.scheme, host, port, path


def request_url(pj: str, fee_rate: float, change_index: int | None, contribution: int) -> str:
    """The pj URL with BIP-78's sender parameters added."""
    params = {"v": "1", "disableoutputsubstitution": "true",
              "minfeerate": f"{max(1.0, fee_rate):.2f}"}
    if change_index is not None and contribution > 0:
        params["additionalfeeoutputindex"] = str(change_index)
        params["maxadditionalfeecontribution"] = str(contribution)
    sep = "&" if "?" in pj else "?"
    return pj + sep + urlencode(params, quote_via=quote)


def http_post(url: str, body: str, socks5: str | None, timeout: float = 60) -> tuple[int, str]:
    """POST text/plain, read at most MAX_BODY. Through Tor when socks5 is set."""
    scheme, host, port, path = check_url(url)
    if socks5:
        from .electrum import ElectrumError, _socks5_connect
        try:
            raw = _socks5_connect(socks5, host, port, timeout)
        except ElectrumError as e:
            raise PayjoinError(str(e))
    else:
        raw = socket.create_connection((host, port), timeout=timeout)
    raw.settimeout(timeout)
    if scheme == "https":
        raw = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
    data = body.encode()
    req = (f"POST {path} HTTP/1.1\r\nHost: {host}\r\nContent-Type: text/plain\r\n"
           f"Content-Length: {len(data)}\r\nConnection: close\r\nUser-Agent: InsertCoin\r\n\r\n")
    try:
        raw.sendall(req.encode() + data)
        buf = b""
        while len(buf) <= MAX_BODY + 8192:
            chunk = raw.recv(65536)
            if not chunk:
                break
            buf += chunk
    finally:
        raw.close()
    head, _, rest = buf.partition(b"\r\n\r\n")
    lines = head.decode("latin1").split("\r\n")
    try:
        status = int(lines[0].split()[1])
    except (IndexError, ValueError):
        raise PayjoinError("the receiver's answer isn't HTTP")
    if any(x.lower().startswith("transfer-encoding:") and "chunked" in x.lower() for x in lines):
        rest = _unchunk(rest)
    if len(rest) > MAX_BODY:
        raise PayjoinError("answer too big")
    return status, rest.decode("utf-8", "replace")


def _unchunk(b: bytes) -> bytes:
    out = b""
    while b:
        size, _, b = b.partition(b"\r\n")
        try:
            n = int(size.split(b";")[0], 16)
        except ValueError:
            raise PayjoinError("bad chunked answer")
        if n == 0:
            break
        out, b = out + b[:n], b[n + 2:]
    return out


def error_text(body: str) -> str:
    try:
        j = json.loads(body)
        return str(j.get("errorCode") or j.get("message") or "refused")[:60]
    except ValueError:
        return body.strip()[:60] or "refused"


# ------------------------------------------------------------ the checks
def _outpoint(inp) -> tuple[bytes, int]:
    return bytes(inp.txid), inp.vout


def _sequence(inp) -> int:
    return inp.sequence if inp.sequence is not None else 0xFFFFFFFF


def check_proposal(original: PSBT, proposal: PSBT, pay_index: int, ours: set[bytes],
                   max_contribution: int, fee_index: int | None) -> dict:
    """BIP-78's sender checks (stricter: no output substitution). original:
    what the signer signed (its utxo data is the signer's own); ours: the
    scripts of the sender's own outputs (change). Returns {extra_fee,
    receiver_inputs}. Raises PayjoinError."""
    otx, ptx = original.tx, proposal.tx
    if ptx.version != otx.version or ptx.locktime != otx.locktime:
        raise PayjoinError("version or lock time changed")
    mine = {_outpoint(i): i for i in original.inputs}
    seen, receiver_in, seqs = set(), 0, set()
    for inp in proposal.inputs:
        if inp.bip32_derivations or inp.taproot_bip32_derivations:
            raise PayjoinError("key paths in the proposal")
        if inp.partial_sigs or inp.taproot_sigs:
            raise PayjoinError("signatures in the proposal")
        seqs.add(_sequence(inp))
        op = _outpoint(inp)
        if op in mine:
            if op in seen:
                raise PayjoinError("an input twice")
            seen.add(op)
            if _sequence(inp) != _sequence(mine[op]):
                raise PayjoinError("your input's sequence changed")
            if inp.final_scriptwitness or inp.final_scriptsig:
                raise PayjoinError("your input came back finalized")
        else:
            receiver_in += 1
            if not (inp.final_scriptwitness or inp.final_scriptsig):
                raise PayjoinError("the receiver's input isn't signed")
            if inp.witness_utxo is None and inp.non_witness_utxo is None:
                raise PayjoinError("the receiver's input has no amount")
    if seen != set(mine):
        raise PayjoinError("one of your inputs is missing")
    if len(seqs) != 1:
        raise PayjoinError("inputs with different sequences")
    for out in proposal.outputs:
        if out.bip32_derivations or out.taproot_bip32_derivations:
            raise PayjoinError("key paths in the proposal's outputs")
    # outputs: the payment (same script, not smaller), each of ours present,
    # only the fee output may shrink, by at most the contribution
    o_outs = [(o.script_pubkey.data, o.value) for o in otx.vout]
    p_outs = [(o.script_pubkey.data, o.value) for o in ptx.vout]
    pay_script, pay_value = o_outs[pay_index]
    left = list(p_outs)

    def take(script):
        for j, (s, v) in enumerate(left):
            if s == script:
                return left.pop(j)[1]
        return None

    got = take(pay_script)
    if got is None or got < pay_value:
        raise PayjoinError("the payment changed")
    contribution = 0
    for j, (s, v) in enumerate(o_outs):
        if j == pay_index:
            continue
        nv = take(s)
        if nv is None:
            raise PayjoinError("one of your outputs is missing")
        if nv < v:
            if j != fee_index or s not in ours:
                raise PayjoinError("one of your outputs shrank")
            contribution += v - nv
    if contribution > max_contribution:
        raise PayjoinError("asks you to pay too much fee")
    for s, _v in left:                                # anything new must not be ours
        if s in ours:
            raise PayjoinError("an extra output to you (confusing)")
    o_fee = _fee(original)
    p_fee = _fee(proposal)
    if p_fee is None or p_fee < o_fee:
        raise PayjoinError("the fee went down")
    if contribution > p_fee - o_fee:
        raise PayjoinError("your contribution doesn't all go to fee")
    if contribution and receiver_in == 0:
        raise PayjoinError("you'd pay more fee for no added input")
    return {"extra_fee": contribution, "receiver_inputs": receiver_in}


def _fee(p: PSBT) -> int | None:
    total = 0
    for i, inp in enumerate(p.inputs):
        u = inp.witness_utxo
        if u is None and inp.non_witness_utxo is not None:
            u = inp.non_witness_utxo.vout[inp.vout]
        if u is None:
            return None
        total += u.value
    return total - sum(o.value for o in p.tx.vout)


def restore_ours(proposal: PSBT, original: PSBT) -> PSBT:
    """Put the sender's own input and output details (utxo data, key paths)
    back into the proposal (BIP-78 receivers strip them), so the signer can
    sign its inputs. Signatures aren't copied."""
    mine = {_outpoint(i): i for i in original.inputs}
    for inp in proposal.inputs:
        src = mine.get(_outpoint(inp))
        if src is None:
            continue
        inp.witness_utxo = src.witness_utxo
        inp.non_witness_utxo = src.non_witness_utxo
        inp.bip32_derivations = dict(src.bip32_derivations)
        inp.taproot_bip32_derivations = dict(src.taproot_bip32_derivations)
        inp.taproot_internal_key = src.taproot_internal_key
        inp.final_scriptwitness = None
    return proposal


def encode(p: PSBT) -> str:
    return base64.b64encode(p.serialize()).decode()


# ------------------------------------------------------------ the app side
def post(settings, url: str, body: str) -> tuple[int, str]:
    """Through cznet on the device, directly in the simulator and tests.
    Tor as for the Electrum server (not for a home address)."""
    import os
    from .electrum import local_host
    proxy = settings.tor_proxy if settings.use_tor else None
    _s, host, _p, _path = check_url(url)
    if proxy and local_host(host):
        proxy = None
    if os.environ.get("CZ_NET_SOCK"):
        from .netd import RelayClient
        return RelayClient(settings.server, socks5=proxy).payjoin_post(url, body)
    return http_post(url, body, proxy)


def run(wallet, settings, signer, pj_url: str, signed_b64: str, fee_rate: float,
        poster=None) -> tuple[str, str]:
    """Try the PayJoin. Returns (PSBT to finish and broadcast, note): the
    signed proposal and "PAYJOIN", or the original and why not."""
    params = signer.payjoin_params()
    if not params:
        return signed_b64, "not a single payment"
    try:
        url = request_url(pj_url, fee_rate, params["fee_index"], params["max"])
        original = wallet.finalized_psbt(signed_b64)
        status, body = (poster or (lambda u, b: post(settings, u, b)))(url, original)
        if status != 200:
            return signed_b64, f"receiver said no ({error_text(body)})"
        joined = signer.sign_payjoin(psbt=body.strip())
        prop = PSBT.from_string(joined)
        added = len(prop.inputs) - len(PSBT.from_string(signed_b64).inputs)
        return joined, "PAYJOIN" if added > 0 else "payjoin: no coin added"
    except Exception as e:  # noqa: BLE001 - any failure: the original goes out
        return signed_b64, str(e)[:60]
