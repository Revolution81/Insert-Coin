"""
Inheritance wallets: the owner can spend any time; the heir can spend a coin
only after it has sat unmoved for a set number of blocks.

  wsh(or_d(pk(OWNER/<0;1>/*),and_v(v:pkh(HEIR/<0;1>/*),older(N))))

The same template Liana uses. The script:
  <owner> CHECKSIG IFDUP NOTIF
      DUP HASH160 <heir hash> EQUALVERIFY CHECKSIGVERIFY <N> CHECKSEQUENCEVERIFY
  ENDIF

Owner spends:  [owner sig, script]
Heir spends:   [heir sig, heir pubkey, <empty>, script]  with nSequence >= N

The timer runs per coin, from the block it confirmed in. The owner keeps the
heir locked out by moving coins (REFRESH) before the timer runs out. N is a
block count (BIP68 allows at most 65535 blocks, about 15 months).
"""
from __future__ import annotations

import re

from .multisig import Policy, PolicyError, normalize_descriptor, parse_key

MAX_DELAY = 65535
BLOCKS_PER_DAY = 144

DELAYS = [  # (label, blocks)
    ("3 MONTHS", 13140), ("6 MONTHS", 26280), ("1 YEAR", 52560), ("15 MONTHS", 65535),
]
TEST_DELAYS = [("10 BLOCKS", 10), ("1 DAY", 144)]

_RE = re.compile(r"^wsh\(or_d\(pk\((.+?)/<0;1>/\*\),and_v\(v:pkh\((.+?)/<0;1>/\*\),older\((\d+)\)\)\)\)$")


def build(owner_key: str, heir_key: str, blocks: int) -> str:
    if not 1 <= blocks <= MAX_DELAY:
        raise PolicyError(f"delay must be 1 to {MAX_DELAY} blocks")
    return (f"wsh(or_d(pk({owner_key}/<0;1>/*),"
            f"and_v(v:pkh({heir_key}/<0;1>/*),older({blocks}))))")


def parse(text: str, network: str, my_key=None, name: str = "") -> Policy:
    t = normalize_descriptor(text)
    m = _RE.match(t)
    if not m:
        raise PolicyError("not an inheritance wallet descriptor")
    owner = parse_key(m.group(1), network)
    heir = parse_key(m.group(2), network)
    blocks = int(m.group(3))
    if owner[1:9] == heir[1:9]:
        raise PolicyError("owner and heir must be different signers")
    if my_key is not None and not isinstance(my_key, str):
        my_key = my_key[0]                       # SegWit (/2h) key: this is wsh(...)
    if my_key is not None and my_key not in (owner, heir):
        raise PolicyError("this device's key isn't in that wallet")
    desc = build(owner, heir, blocks)
    p = Policy(desc, 1, 2, [owner[1:9], heir[1:9]], name, kind="inherit", delay=blocks)
    try:
        d = p.derive(0, 0)
        d.script_pubkey()
        p.ws_len = len(d.witness_script().data)
    except Exception as e:
        raise PolicyError(f"descriptor doesn't work: {e}")
    return p


def role(policy: Policy, my_fingerprint: str) -> str:
    return "owner" if policy.cosigners[0] == my_fingerprint else "heir"


def input_vbytes(ws_len: int) -> float:
    """Heir path (the bigger one): sig, pubkey, empty, script."""
    witness = 1 + (1 + 73) + (1 + 33) + 1 + (1 + ws_len)
    return 41 + witness / 4


def describe_delay(blocks: int) -> str:
    days = blocks / BLOCKS_PER_DAY
    if days < 1:
        return f"{blocks} BLOCKS"
    if days < 60:
        return f"{round(days)} DAYS"
    return f"{round(days / 30.4)} MONTHS"
