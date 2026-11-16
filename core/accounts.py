"""
Single-sig accounts: one seed, many separate wallets.

An account is a script type and a number:

  wpkh:N   native SegWit (bc1q...), BIP84   m/84'/coin'/N'
  tr:N     Taproot (bc1p...), BIP86 key-path m/86'/coin'/N'

"wpkh:0" is the main wallet everything starts with. Other wallets (Sparrow,
Bitcoin Core, ...) find the same accounts from the same seed. Accounts
don't share addresses or coins, so they're handy for keeping savings,
spending or a business apart. They all come back from your one backup.
"""
from __future__ import annotations

KINDS = {
    "wpkh": {"purpose": 84, "name": "NATIVE SEGWIT", "prefix": "bc1q", "input_vb": 68.0},
    "tr":   {"purpose": 86, "name": "TAPROOT", "prefix": "bc1p", "input_vb": 57.5},
}
MAX_ACCOUNT = 999
MAIN = "wpkh:0"


class AccountError(ValueError):
    pass


def parse(account: str | None) -> tuple[str, int]:
    """'tr:2' -> ('tr', 2). None means the main account."""
    if account is None:
        return "wpkh", 0
    if not isinstance(account, str) or account.count(":") != 1:
        raise AccountError("bad account")
    kind, num = account.split(":")
    if kind not in KINDS or not num.isdigit() or len(num) > 3:
        raise AccountError("bad account")
    n = int(num)
    if not 0 <= n <= MAX_ACCOUNT:
        raise AccountError("bad account")
    return kind, n


def make_id(kind: str, n: int) -> str:
    return f"{kind}:{n}"


def label(account: str | None) -> str:
    kind, n = parse(account)
    if (kind, n) == ("wpkh", 0):
        return "Main"
    return f"{'Taproot' if kind == 'tr' else 'Segwit'} #{n}"


def purpose(kind: str) -> int:
    return KINDS[kind]["purpose"]
