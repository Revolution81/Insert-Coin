"""
Build, sign and check Insert Coin releases.

  python3 tools/release.py keygen                 # once: make your signing key
  python3 tools/release.py build                  # dist/czwallet-X.Y.Z.tar.gz + its SHA-256
  python3 tools/release.py sign [FILE]            # FILE.sig (default: dist/czwallet-X.Y.Z.tar.gz)
  python3 tools/release.py verify FILE [KEYHEX]   # anyone: check a release

Reproducible: run `build` on the same source and you get the same SHA-256,
so you can check a downloaded file against your own build before signing.
The private key is stored encrypted with a passphrase; keep it off the
device and backed up. Put the public key (hex) in
/etc/czwallet/release_keys.txt on each device you update.
"""
import getpass
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import update  # noqa: E402
from core.version import VERSION  # noqa: E402

DIST = os.path.join(ROOT, "dist")
KEY = os.path.expanduser("~/.czwallet-release-key.pem")


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "keygen":
        if os.path.exists(KEY):
            sys.exit(f"{KEY} already exists")
        pw = getpass.getpass("Passphrase for the new key: ").encode()
        if pw != getpass.getpass("Again: ").encode() or len(pw) < 12:
            sys.exit("passphrases differ or are shorter than 12 characters")
        pem, pub = update.keygen(pw)
        fd = os.open(KEY, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, pem)
        os.close(fd)
        print(f"Private key: {KEY} (back it up)")
        print(f"Public key (put in /etc/czwallet/release_keys.txt):\n{pub.hex()}")
    elif cmd == "build":
        path, digest = update.build(ROOT, DIST, VERSION)
        print(f"{path}\nsha256 {digest}")
    elif cmd == "sign":
        import re
        path = argv[2] if len(argv) > 2 else os.path.join(DIST, f"czwallet-{VERSION}.tar.gz")
        m = re.fullmatch(r"czwallet-(\d+\.\d+\.\d+)\.tar\.gz", os.path.basename(path))
        if not m or not os.path.isfile(path):
            sys.exit(f"not a release file: {path} (expected czwallet-X.Y.Z.tar.gz)")
        version = m.group(1)
        print(f"Signing Insert Coin {version}\nsha256 {update.sha256_file(path)}")
        pw = getpass.getpass("Key passphrase: ").encode()
        with open(KEY, "rb") as f:
            print(update.sign(path, version, f.read(), pw))
    elif cmd == "verify" and len(argv) >= 3:
        keys = [bytes.fromhex(argv[3])] if len(argv) > 3 else update.load_keys()
        info = update.verify(argv[2], argv[2] + ".sig", keys, current="0.0.0")
        print(f"OK: version {info['version']}, key {info['key']}, sha256 {info['sha256']}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
