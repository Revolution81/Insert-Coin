"""
Settings and paths. Everything lives on the microSD card under two folders
with different owners:

  /var/lib/czwallet/signer   owned by the `czsigner` user: encrypted seed,
                             PIN counter, spend-cap ledger. The app cannot read it.
  /var/lib/czwallet/app      owned by the app user: settings, the BDK wallet
                             database (public keys and history only).

Override the root with CZ_DATA (handy for development and tests).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field

DATA_ROOT = os.environ.get("CZ_DATA", "/var/lib/czwallet")
SIGNER_DIR = os.path.join(DATA_ROOT, "signer")
APP_DIR = os.path.join(DATA_ROOT, "app")
SIGNER_SOCKET = os.environ.get("CZ_SIGNER_SOCK", "/run/czwallet/signer.sock")

# network name -> (BDK name, embit name, BIP44 coin type)
NETWORKS = {
    "bitcoin":  ("BITCOIN", "main", 0),
    "testnet4": ("TESTNET4", "test", 1),
    "signet":   ("SIGNET", "signet", 1),
    "regtest":  ("REGTEST", "regtest", 1),
}

DEFAULT_SERVERS = {
    "bitcoin": "ssl://electrum.blockstream.info:50002",
    "testnet4": "ssl://mempool.space:40002",
    "signet": "ssl://mempool.space:60602",
    "regtest": "tcp://127.0.0.1:50001",
}


@dataclass
class Settings:
    network: str = "bitcoin"
    electrum_url: str = ""            # empty = DEFAULT_SERVERS[network]
    use_tor: bool = True
    tor_proxy: str = "127.0.0.1:9050"
    stop_gap: int = 20
    auto_lock_s: int = 120            # SETUP > AUTO LOCK (0 = off); the signer's copy rules
    qr_format: str = "ur"             # signed PSBTs out: "ur" (Sparrow's default) or "bbqr"
    finger_unlock: bool = False       # fingerprint reopens the screen during a session
    sound: bool = True                # SETUP › SOUND: title fanfare and menu boop, saved
    amount_style: str = "big"         # "big": 0.02150000 all big; "split": last 3 small
    box_color: list | None = None     # [r, g, b] for the boxes; None = classic blue
    box_choice: str | None = None     # "CUSTOM" when using your own mix
    box_custom: list | None = None    # your own mix, kept while trying presets
    explorer: str = "https://mempool.space"   # where transaction links point
    share_user: str = "admin"         # SHARE FILES login
    share_pass_hash: str | None = None   # None = the default password, bitcoin
    share_minutes: int = 10           # SHARE FILES switches itself off after this
    labels: dict = field(default_factory=dict)   # address or txid -> label
    accounts: list = field(default_factory=list)  # extra single-sig accounts: {"id", "name"}

    @property
    def server(self) -> str:
        return self.electrum_url or DEFAULT_SERVERS[self.network]

    @property
    def own_server(self) -> bool:
        """A server you set yourself (your node), not the built-in public one."""
        return bool(self.electrum_url) and self.electrum_url not in DEFAULT_SERVERS.values()

    @classmethod
    def load(cls) -> "Settings":
        path = os.path.join(APP_DIR, "settings.json")
        try:
            with open(path) as f:
                raw = json.load(f)
            return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})
        except FileNotFoundError:
            return cls()

    def save(self) -> None:
        os.makedirs(APP_DIR, exist_ok=True)
        path = os.path.join(APP_DIR, "settings.json")
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(asdict(self), f, indent=1)
        os.replace(tmp, path)


_SERVER_HOST = re.compile(r"^[A-Za-z0-9.-]{1,253}$")


def parse_server(text: str) -> str:
    """A server as typed or scanned, as ssl://host:port or tcp://host:port.
    Accepts what node dashboards show and put in their QR codes:
    "umbrel.local:50001:t" (Electrum's style, t = plain, s = SSL),
    "tcp://host:port", "ssl://host:port", or "host:port" (plain for a
    home or .onion server, SSL otherwise)."""
    t = (text or "").strip()
    if "://" in t:
        scheme, _, rest = t.partition("://")
        scheme = scheme.lower()
        rest = rest.rstrip("/")
    else:
        parts = t.split(":")
        if len(parts) == 3 and parts[2].lower() in ("t", "s"):
            scheme, rest = ("tcp" if parts[2].lower() == "t" else "ssl"), f"{parts[0]}:{parts[1]}"
        elif len(parts) == 2:
            from .electrum import local_host
            scheme = "tcp" if local_host(parts[0]) or parts[0].endswith(".onion") else "ssl"
            rest = t
        else:
            raise ValueError("not a server address")
    host, _, port = rest.rpartition(":")
    if scheme not in ("ssl", "tcp") or not _SERVER_HOST.match(host) or not port.isdigit() \
            or not 1 <= int(port) <= 65535:
        raise ValueError("not a server address")
    return f"{scheme}://{host.lower()}:{int(port)}"

