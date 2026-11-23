"""
SETUP > SECURITY > SPENDING POLICY: rules the signer checks before every
signature, in Vault and Online mode alike, on top of the online daily cap.

  per_tx       most one payment may send (sats, 0 = no limit)
  per_day      most all payments together may send per day (0 = no limit)
  cooldown_s   least time between two payments (0 = none)
  allow        addresses payments may go to; change and self-sends always
  allow_on     are fine. Only enforced while allow_on is True.
  max_fee      highest fee rate the signer will sign (sat/vB)
  totp         phone 2FA: a 6-digit code from an authenticator app (RFC 6238)
  totp_above   ask for the code only above this many sats (0 = every payment)
  bypass       a separate BYPASS PIN. While one is set the policy is locked:
               changing or removing it needs the bypass PIN and then your
               PIN, and the seed can't be shown or exported in any form, so
               the PIN alone can't get around the rules.

The file lives in the signer's own folder. It holds the 2FA secret and an
Argon2 hash of the bypass PIN, nothing about the seed.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import secrets
import struct
import time

MAX_FEE_DEFAULT = 1000
MAX_ALLOW = 50
STEP = 30
# Before this the clock can't be right (it's before this firmware existed),
# so 2FA codes can't be checked: go online once to set the time.
CLOCK_FLOOR = int(dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc).timestamp())

FIELDS = {"per_tx", "per_day", "cooldown_s", "allow_on", "allow_add", "allow_remove",
          "max_fee", "totp_above"}


class PolicyError(Exception):
    pass


DEFAULT = {"per_tx": 0, "per_day": 0, "cooldown_s": 0, "allow": [], "allow_on": False,
           "max_fee": MAX_FEE_DEFAULT, "totp": None, "totp_above": 0, "bypass": None}


def load(path: str) -> dict:
    """The policy. A missing file means no policy; a damaged one fails
    closed (damaged=True: no signing, no seed export) rather than silently
    dropping every rule, a bypass PIN included."""
    damaged = False
    try:
        with open(path) as f:
            p = json.load(f)
        if not isinstance(p, dict):
            raise ValueError
    except FileNotFoundError:
        p = {}
    except (ValueError, OSError, UnicodeDecodeError):
        p, damaged = {}, True
    out = dict(DEFAULT)
    out.update({k: v for k, v in p.items() if k in DEFAULT or k == "state"})
    if not isinstance(out.get("state"), dict):
        out["state"] = {}
    out["damaged"] = damaged
    return out


def public(p: dict) -> dict:
    """What the app may see: the rules, never the 2FA secret or bypass hash."""
    return {"per_tx": p["per_tx"], "per_day": p["per_day"], "cooldown_s": p["cooldown_s"],
            "allow": list(p["allow"]), "allow_on": p["allow_on"], "max_fee": p["max_fee"],
            "totp": bool(p["totp"]), "totp_above": p["totp_above"],
            "locked": bool(p["bypass"]) or p.get("damaged", False), "active": active(p),
            "damaged": p.get("damaged", False)}


def active(p: dict) -> bool:
    return bool(p["per_tx"] or p["per_day"] or p["cooldown_s"] or p["allow_on"]
                or p["totp"] or p["max_fee"] != MAX_FEE_DEFAULT)


# ------------------------------------------------------------------ editing
def change(p: dict, field: str, value, check_address=None) -> dict:
    """A copy of p with one rule changed, after checking the value."""
    if field not in FIELDS:
        raise PolicyError("unknown policy setting")
    p = json.loads(json.dumps(p))
    if field in ("per_tx", "per_day", "totp_above"):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 21_000_000 * 10 ** 8:
            raise PolicyError("amount out of range")
        p[field] = value
    elif field == "cooldown_s":
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 7 * 86400:
            raise PolicyError("cooldown must be 0 to 7 days")
        p[field] = value
    elif field == "max_fee":
        if isinstance(value, bool) or not isinstance(value, int) or not 2 <= value <= 5000:
            raise PolicyError("max fee must be 2 to 5000 sat/vB")
        p[field] = value
    elif field == "allow_on":
        if not isinstance(value, bool):
            raise PolicyError("on or off")
        if value and not p["allow"]:
            raise PolicyError("add an address first")
        p[field] = value
    elif field == "allow_add":
        addr = (check_address or (lambda a: a))(value)
        if addr in p["allow"]:
            raise PolicyError("already on the list")
        if len(p["allow"]) >= MAX_ALLOW:
            raise PolicyError(f"at most {MAX_ALLOW} addresses")
        p["allow"].append(addr)
    elif field == "allow_remove":
        if value not in p["allow"]:
            raise PolicyError("not on the list")
        p["allow"].remove(value)
        if not p["allow"]:
            p["allow_on"] = False
    return p


def describe_change(field: str, value) -> str:
    sats = lambda v: "no limit" if not v else f"{v:,} sats"       # noqa: E731
    if field == "per_tx":
        return f"Per payment: {sats(value)}"
    if field == "per_day":
        return f"Per day: {sats(value)}"
    if field == "cooldown_s":
        return "Cooldown: off" if not value else f"Cooldown: {value // 60} min"
    if field == "max_fee":
        return f"Max fee: {value} sat/vB"
    if field == "allow_on":
        return "Allow-list: " + ("ON" if value else "OFF")
    if field == "allow_add":
        return f"Allow {str(value)[:14]}...{str(value)[-6:]}"
    if field == "allow_remove":
        return f"Remove {str(value)[:14]}...{str(value)[-6:]}"
    if field == "totp_above":
        return "2FA: every payment" if not value else f"2FA above {value:,} sats"
    return field


# ------------------------------------------------------------------ checking
def check(p: dict, sends: list[tuple[str, int]], spend: int, fee: int, fee_rate: float,
          now: float, today_spent: int, last_sign: float | None,
          day_cost: int | None = None, replaces: bool = False) -> tuple[list[str], bool]:
    """(reasons this payment breaks the policy, whether it needs a 2FA code).
    day_cost: what it adds to today's total (a fee bump only adds the
    difference); replaces: it replaces a payment already signed, so the
    cooldown doesn't apply (no new money is leaving)."""
    why = []
    if p.get("damaged"):
        return ["SPENDING POLICY FILE DAMAGED"], False
    day_cost = spend + fee if day_cost is None else day_cost
    if fee_rate > p["max_fee"]:
        why.append(f"FEE OVER YOUR MAX ({p['max_fee']} SAT/VB)")
    if p["per_tx"] and spend > p["per_tx"]:
        why.append(f"OVER YOUR PER-PAYMENT LIMIT ({p['per_tx']:,} SATS)")
    if p["per_day"] and spend and today_spent + day_cost > p["per_day"]:
        left = max(0, p["per_day"] - today_spent)
        why.append(f"OVER TODAY'S LIMIT ({left:,} SATS LEFT)")
    if p["cooldown_s"] and spend and not replaces and last_sign is not None \
            and now - last_sign < p["cooldown_s"]:
        wait = int(p["cooldown_s"] - (now - last_sign)) // 60 + 1
        why.append(f"COOLDOWN: WAIT {wait} MIN")
    if p["allow_on"]:
        bad = [a for a, _ in sends if a not in p["allow"]]
        if bad:
            why.append("ADDRESS NOT ON YOUR ALLOW-LIST")
    needs = bool(p["totp"]) and spend > 0 and spend > p["totp_above"]
    return why, needs


# ------------------------------------------------------------------ 2FA (TOTP)
def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def otpauth_uri(secret: str, label: str = "Insert Coin") -> str:
    return f"otpauth://totp/{label.replace(' ', '%20')}?secret={secret}&issuer=Insert%20Coin"


def totp(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    o = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def clock_ok(now: float | None = None) -> bool:
    return (now if now is not None else time.time()) >= CLOCK_FLOOR


def verify_code(secret: str, code: str, last_step: int, now: float | None = None) -> int:
    """The time step the code matched (one step either side allowed), never
    one already used. Raises if wrong."""
    now = time.time() if now is None else now
    if not clock_ok(now):
        raise PolicyError("the device clock isn't set: go Online once to set it")
    code = (code or "").strip()
    if len(code) != 6 or not code.isdigit():
        raise PolicyError("type the 6-digit code from your phone")
    here = int(now // STEP)
    for step in (here, here - 1, here + 1):
        if step > last_step and hmac.compare_digest(totp(secret, step), code):
            return step
    raise PolicyError("wrong 2FA code")


# ------------------------------------------------------------------ bypass PIN
def hash_bypass(pin: str, t: int, m: int) -> dict:
    from argon2.low_level import Type, hash_secret_raw
    salt = secrets.token_bytes(16)
    h = hash_secret_raw(pin.encode(), salt, time_cost=t, memory_cost=m, parallelism=4,
                        hash_len=32, type=Type.ID)
    return {"salt": base64.b64encode(salt).decode(), "hash": base64.b64encode(h).decode(),
            "t": t, "m": m}


def check_bypass(stored: dict, pin: str) -> bool:
    from argon2.low_level import Type, hash_secret_raw
    h = hash_secret_raw(pin.encode(), base64.b64decode(stored["salt"]), time_cost=stored["t"],
                        memory_cost=stored["m"], parallelism=4, hash_len=32, type=Type.ID)
    return hmac.compare_digest(h, base64.b64decode(stored["hash"]))


def save(path: str, p: dict, write) -> None:
    write(path, json.dumps(p).encode())
