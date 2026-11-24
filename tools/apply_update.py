"""
Root side of an update (run by /usr/local/sbin/cz-update via sudo).
It trusts nothing the app says: it checks the path, copies the files,
verifies the signature with the pinned keys itself, then installs.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, HERE)

from core import update  # noqa: E402


def main():
    if os.geteuid() != 0 or len(sys.argv) != 2:
        sys.exit("usage (as root): apply_update.py /path/Insert-Coin-Update-X.Y.Z.tar.gz")
    tar = sys.argv[1]
    if not update.allowed_path(tar) or not os.path.isfile(tar):
        sys.exit("update must be on the microSD boot partition or a USB stick")
    try:
        info = update.install(tar, tar + ".sig", update.load_keys())
    except update.UpdateError as e:
        sys.exit(f"refused: {e}")
    print(f"installed {info['version']} (was {info['previous']})")
    # restart after we've answered, so the app sees the result first
    subprocess.run(["systemd-run", "--on-active=3", "systemctl", "restart",
                    "czsigner", "cznet", "czwallet"], check=False)
    # the helpers' services run the same code: restart those that are running
    subprocess.run(["systemd-run", "--on-active=3", "systemctl", "try-restart",
                    "czprint", "czscan", "czshare"], check=False)


if __name__ == "__main__":
    main()
