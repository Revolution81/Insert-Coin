#!/usr/bin/env python3
"""
deploy/czshare.service: the SHARE FILES web page, as its own user (czshare)
in its own sandbox. Reads /run/czshare/config.json (written by the root
helper cz-share from what the app sent), serves until the time is up, and
reports the address and fingerprint in /run/czshare/status.json for the
app's screen. Exits on its own when the time is up.
"""
import json
import os
import sys
import time

sys.path.insert(0, "/opt/czwallet")
from core import share  # noqa: E402

RUN = share.RUN_DIR


def main() -> int:
    with open(os.path.join(RUN, "config.json")) as f:
        cfg = json.load(f)
    timeout = max(60, min(int(cfg.get("timeout_s", share.TIMEOUT_S)), 60 * 60))
    srv = share.ShareServer(share.folders(), cfg["password_hash"], cfg.get("user", "admin"),
                            bool(cfg.get("include_paper")), "/var/lib/czshare",
                            share.PORT, timeout)
    srv.start()
    ends = time.time() + timeout
    try:
        while srv.seconds_left() > 0:
            st = {"url": srv.url(), "fingerprint": srv.fingerprint, "ends": ends,
                  "downloads": srv.downloads}
            tmp = os.path.join(RUN, "status.json.tmp")
            with open(tmp, "w") as f:
                json.dump(st, f)
            os.replace(tmp, os.path.join(RUN, "status.json"))
            time.sleep(1)
    finally:
        srv.stop()
        try:
            os.remove(os.path.join(RUN, "status.json"))
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
