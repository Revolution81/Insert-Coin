"""
Reproducible, signed releases and offline updates.

Release = a tar.gz built deterministically from the source tree (sorted
files, fixed timestamps and owners, no compiler output), so anyone can
rebuild it from the same source and get the same SHA-256. The signature is
Ed25519 over:

    czwallet-release-v1\\n<version>\\n<sha256 hex>\\n

Updating (Vault mode is fine; nothing is downloaded):
  1. put Insert-Coin-Update-X.Y.Z.tar.gz and its .sig on the microSD boot
     partition (folder Insert-Coin-Update) or a USB stick
  2. SETUP > UPDATE checks the signature against the keys pinned in
     /etc/czwallet/release_keys.txt and shows version, key and hash
  3. the root helper copies the file somewhere only root can write,
     verifies it again itself (it doesn't trust the app), unpacks it with
     strict checks into /opt/czwallet-releases/<version>, and switches the
     /opt/czwallet link. The previous version stays for rollback.

Downgrades are refused. A release whose Python packages differ from the
installed ones is refused too (the device is offline; run install.sh).
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import tempfile

from .version import VERSION

FORMAT = "czwallet-release-v1"
KEYS_FILE = os.environ.get("CZ_RELEASE_KEYS", "/etc/czwallet/release_keys.txt")
RELEASES = "/opt/czwallet-releases"
LINK = "/opt/czwallet"
SEARCH = os.environ.get("CZ_UPDATE_DIRS", "/boot/firmware/Insert-Coin-Update:"
                        "/boot/firmware/czwallet-update:/media")
# Update files are named Insert-Coin-Update-X.Y.Z.tar.gz (+ .sig) since
# 0.8.26; the old czwallet-X.Y.Z.tar.gz name is still accepted, as is the
# old czwallet-update folder. (Inside, the files stay under czwallet-X.Y.Z/
# and the signed message keeps its format name: changing either would make
# releases unreadable to devices that are already out there.)
PREFIXES = ("Insert-Coin-Update-", "czwallet-")
FOLDERS = ("Insert-Coin-Update", "czwallet-update")
NAME_RE = re.compile(r"^(?:Insert-Coin-Update-|czwallet-)(\d+\.\d+\.\d+)\.tar\.gz$")


def file_name(version: str) -> str:
    return f"Insert-Coin-Update-{version}.tar.gz"


def name_version(path: str) -> str | None:
    """The version in an update file's name, or None if it isn't one."""
    m = NAME_RE.match(os.path.basename(path))
    return m.group(1) if m else None
EPOCH = 1704067200                      # fixed timestamp in every release (2024-01-01)
MAX_FILES, MAX_BYTES = 2000, 50 * 1024 * 1024

INCLUDE = ["app.py", "sim.py", "requirements.txt", "requirements.lock", "README.md", "SECURITY.md",
           "core", "ui", "tools", "deploy"]
SKIP_DIRS = {"__pycache__", "tests", "preview", "simdata", ".pytest_cache", ".git"}
_VER = re.compile(r"^\d+\.\d+\.\d+$")


class UpdateError(Exception):
    pass


def vtuple(v: str) -> tuple[int, ...]:
    if not _VER.match(v):
        raise UpdateError(f"bad version {v!r}")
    return tuple(int(x) for x in v.split("."))


def message(version: str, sha256: str) -> bytes:
    return f"{FORMAT}\n{version}\n{sha256}\n".encode()


def key_id(pub: bytes) -> str:
    return hashlib.sha256(pub).hexdigest()[:16]


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------ build
def collect(src: str) -> list[str]:
    out = []
    for item in INCLUDE:
        p = os.path.join(src, item)
        if os.path.isfile(p):
            out.append(item)
        elif os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
                for f in files:
                    if f.endswith((".pyc", ".pyo")) or f.startswith("."):
                        continue
                    out.append(os.path.relpath(os.path.join(root, f), src))
    # names inside a release always use "/": built on Windows, relpath gives
    # "core\\x.py", which the device rightly refuses as an unsafe entry
    return sorted(r.replace(os.sep, "/").replace("\\", "/") for r in out)


# text files get Linux line endings whatever the checkout did (Git for
# Windows turns LF into CRLF), so a release is the same from any computer
TEXT = (".py", ".sh", ".txt", ".md", ".json", ".lock", ".cfg", ".ini", ".toml",
        ".service", ".rules", ".conf", ".html", ".css", ".js", ".svg", ".csv", ".in")


def normalise(rel: str, data: bytes) -> tuple[bytes, int]:
    """(the file as it goes in a release, its mode). Executable means it
    starts with #!, which works the same on Windows, where every file looks
    executable to os.access."""
    name = rel.rsplit("/", 1)[-1]
    if (name.endswith(TEXT) or "." not in name) and b"\0" not in data:
        data = data.replace(b"\r\n", b"\n")
    return data, (0o755 if data.startswith(b"#!") else 0o644)


def build(src: str, out_dir: str, version: str = VERSION) -> tuple[str, str]:
    """Deterministic tarball. Returns (path, sha256)."""
    vtuple(version)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, file_name(version))
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for rel in collect(src):
            full = os.path.join(src, *rel.split("/"))
            with open(full, "rb") as f:
                data, mode = normalise(rel, f.read())
            info = tarfile.TarInfo(f"czwallet-{version}/{rel}")
            info.size = len(data)
            info.mtime = EPOCH
            info.mode = mode
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))
    with open(path, "wb") as f:
        with gzip.GzipFile(filename="", mode="wb", fileobj=f, mtime=0, compresslevel=9) as gz:
            gz.write(raw.getvalue())
    return path, sha256_file(path)


# ------------------------------------------------------------- sign/verify
def keygen(passphrase: bytes) -> tuple[bytes, bytes]:
    """(encrypted private key PEM, raw 32-byte public key)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    k = Ed25519PrivateKey.generate()
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                          serialization.BestAvailableEncryption(passphrase))
    pub = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return pem, pub


def sign(tar_path: str, version: str, pem: bytes, passphrase: bytes) -> str:
    from cryptography.hazmat.primitives import serialization
    k = serialization.load_pem_private_key(pem, passphrase)
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    pub = k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    digest = sha256_file(tar_path)
    sig = {"format": FORMAT, "version": version, "sha256": digest, "key": key_id(pub),
           "sig": k.sign(message(version, digest)).hex()}
    sig_path = tar_path + ".sig"
    with open(sig_path, "w") as f:
        json.dump(sig, f, indent=1)
    return sig_path


def load_keys(path: str = KEYS_FILE) -> list[bytes]:
    """One hex Ed25519 public key per line; # comments allowed."""
    keys = []
    try:
        with open(path) as f:
            for line in f:
                line = line.split("#")[0].strip()
                if line:
                    keys.append(bytes.fromhex(line))
    except FileNotFoundError:
        pass
    return [k for k in keys if len(k) == 32]


def verify(tar_path: str, sig_path: str, keys: list[bytes], current: str = VERSION) -> dict:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if not keys:
        raise UpdateError("no release keys are set up on this device")
    try:
        with open(sig_path) as f:
            sig = json.load(f)
        version, digest = sig["version"], sig["sha256"]
        signature = bytes.fromhex(sig["sig"])
    except (OSError, ValueError, KeyError, TypeError):
        raise UpdateError("signature file is missing or damaged")
    if sig.get("format") != FORMAT:
        raise UpdateError("unknown signature format")
    if sha256_file(tar_path) != digest:
        raise UpdateError("the file doesn't match its signature")
    for pub in keys:
        try:
            Ed25519PublicKey.from_public_bytes(pub).verify(signature, message(version, digest))
            break
        except InvalidSignature:
            continue
    else:
        raise UpdateError("not signed by a trusted release key")
    if vtuple(version) <= vtuple(current):
        raise UpdateError(f"{version} isn't newer than {current}")
    if name_version(tar_path) != version:
        raise UpdateError("file name doesn't match the signed version")
    return {"version": version, "sha256": digest, "key": key_id(pub)}


# --------------------------------------------------------------- install
def safe_extract(tar_path: str, version: str, dest: str) -> None:
    """Unpack only plain files under czwallet-<version>/, nothing else."""
    prefix = f"czwallet-{version}/"
    total = 0
    with tarfile.open(tar_path, "r:gz") as tar:
        members = tar.getmembers()
        if len(members) > MAX_FILES:
            raise UpdateError("release has too many files")
        for m in members:
            name = m.name
            if not m.isfile() or not name.startswith(prefix) or name.startswith("/") \
                    or ".." in name.split("/") or "\\" in name:
                raise UpdateError(f"unsafe entry in release: {name[:60]}")
            total += m.size
            if total > MAX_BYTES:
                raise UpdateError("release is too big")
        os.makedirs(dest)
        for m in members:
            rel = m.name[len(prefix):]
            target = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with tar.extractfile(m) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            os.chmod(target, 0o755 if m.mode & 0o100 else 0o644)


def install(tar_path: str, sig_path: str, keys: list[bytes], current: str = VERSION,
            releases: str = RELEASES, link: str = LINK) -> dict:
    """Root side. Copies first (so the app can't swap the file mid-check),
    verifies, unpacks, checks packages, flips the link."""
    work = tempfile.mkdtemp(prefix="czupdate-")
    try:
        tmp_tar = os.path.join(work, os.path.basename(tar_path))
        tmp_sig = tmp_tar + ".sig"
        shutil.copyfile(tar_path, tmp_tar)
        shutil.copyfile(sig_path, tmp_sig)
        info = verify(tmp_tar, tmp_sig, keys, current)
        version = info["version"]
        final = os.path.join(releases, version)
        staging = final + ".new"
        shutil.rmtree(staging, ignore_errors=True)
        safe_extract(tmp_tar, version, staging)
        old_req = os.path.join(os.path.realpath(link), "requirements.txt")
        new_req = os.path.join(staging, "requirements.txt")
        if os.path.exists(old_req) and _reqs(old_req) != _reqs(new_req):
            shutil.rmtree(staging)
            raise UpdateError("this release needs new packages: run deploy/install.sh")
        precompile(staging, final)
        shutil.rmtree(final, ignore_errors=True)
        os.rename(staging, final)
        tmp_link = link + ".new"
        if os.path.lexists(tmp_link):
            os.remove(tmp_link)
        os.symlink(final, tmp_link)
        os.replace(tmp_link, link)                 # atomic switch
        info["previous"] = current
        return info
    finally:
        shutil.rmtree(work, ignore_errors=True)


def precompile(src: str, ddir: str) -> None:
    """Compile the (verified) source once, as root, so the sandboxed services
    don't compile every module again each time they import it. Best effort:
    the source alone still runs."""
    import compileall
    try:
        compileall.compile_dir(src, ddir=ddir, quiet=2)
    except Exception:  # noqa: BLE001
        pass


def _reqs(path: str) -> list[str]:
    with open(path) as f:
        return sorted(l.split("#")[0].strip() for l in f if l.split("#")[0].strip())


def find(dirs: str = SEARCH) -> list[tuple[str, str]]:
    """(tarball, signature) pairs on removable storage."""
    found = []
    for d in dirs.split(":"):
        for base in [d] + ([os.path.join(d, x) for x in sorted(os.listdir(d))]
                           if os.path.isdir(d) else []):
            for sub in [base] + [os.path.join(base, x) for x in FOLDERS]:
                if not os.path.isdir(sub):
                    continue
                for f in sorted(os.listdir(sub)):
                    p = os.path.join(sub, f)
                    if name_version(f) and os.path.isfile(p + ".sig"):
                        if (p, p + ".sig") not in found:
                            found.append((p, p + ".sig"))
    return found


def allowed_path(path: str, dirs: str = SEARCH) -> bool:
    real = os.path.realpath(path)
    return any(real.startswith(os.path.realpath(d) + os.sep) for d in dirs.split(":") if d)


def run_helper(tar_path: str) -> str:
    """App side: ask the root helper to install. Returns its message."""
    import subprocess
    if os.environ.get("CZ_SIM") or not os.path.exists("/usr/local/sbin/cz-update"):
        raise UpdateError("updates install on the device only")
    r = subprocess.run(["sudo", "-n", "/usr/local/sbin/cz-update", tar_path],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise UpdateError((r.stderr or r.stdout).strip()[-120:] or "update failed")
    return r.stdout.strip()
