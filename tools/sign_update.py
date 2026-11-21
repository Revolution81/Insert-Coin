"""
One step for an update, on your PC (Windows, macOS or Linux):

  1. finds the newest czwallet-X.Y.Z.tar.gz in your Downloads folder
  2. signs it with your release key (asks for the key's passphrase)
  3. if the wallet's microSD card is in the PC, copies both files into its
     czwallet-update folder

Then put the card back in the Cardputer: SETUP > UPDATE.
On Windows, double-click sign-update.bat in the Insert-Coin folder.
"""
import getpass
import glob
import os
import re
import shutil
import string
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import update  # noqa: E402

KEY = os.path.expanduser("~/.czwallet-release-key.pem")
NAME = re.compile(r"czwallet-(\d+\.\d+\.\d+)\.tar\.gz$")


def newest_download() -> tuple[str, str] | None:
    found = []
    for p in glob.glob(os.path.join(os.path.expanduser("~"), "Downloads", "czwallet-*.tar.gz")):
        m = NAME.search(os.path.basename(p))
        if m:
            found.append((update.vtuple(m.group(1)), p, m.group(1)))
    if not found:
        return None
    _, path, version = max(found)
    return path, version


def card_boot_partitions() -> list[str]:
    """Mounted drives that look like a Cardputer card's boot partition."""
    roots = []
    if os.name == "nt":
        roots = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
    else:
        for base in ("/media", "/run/media", "/Volumes"):
            roots += glob.glob(os.path.join(base, "*")) + glob.glob(os.path.join(base, "*", "*"))
    return [r for r in roots
            if os.path.isfile(os.path.join(r, "cmdline.txt"))
            and os.path.isfile(os.path.join(r, "config.txt"))]


def main() -> int:
    print("Insert Coin: sign an update\n")
    if not os.path.isfile(KEY):
        print(f"No release key at {KEY}. (Made once with: py tools\\release.py keygen)")
        return 1
    got = newest_download()
    if not got:
        print("No czwallet-X.Y.Z.tar.gz in your Downloads folder.\n"
              "Download it from the update's release page on GitHub first.")
        return 1
    path, version = got
    print(f"File:     {path}\nVersion:  {version}\nSHA-256:  {update.sha256_file(path)}\n")
    pw = getpass.getpass("Key passphrase (nothing shows while you type): ").encode()
    try:
        with open(KEY, "rb") as f:
            sig = update.sign(path, version, f.read(), pw)
    except (ValueError, TypeError):
        print("\nThat passphrase didn't unlock the key. Nothing was signed.")
        return 1
    print(f"\nSigned:   {sig}")
    cards = card_boot_partitions()
    if len(cards) == 1:
        dest = os.path.join(cards[0], "czwallet-update")
        os.makedirs(dest, exist_ok=True)
        for p in (path, sig):
            shutil.copy2(p, dest)
        print(f"Copied both files to {dest}\n\n"
              "Done. Eject the card, put it in the Cardputer, then SETUP > UPDATE.")
    elif len(cards) > 1:
        print("\nMore than one card found; copy both files yourself into the "
              "czwallet-update folder on the right one:\n  " + "\n  ".join(cards))
    else:
        print("\nThe wallet's card isn't in this PC. Put it in and run this again,\n"
              "or copy both files into a czwallet-update folder on its boot partition.")
    return 0


if __name__ == "__main__":
    code = main()
    if os.name == "nt":
        input("\nPress Enter to close.")
    sys.exit(code)
