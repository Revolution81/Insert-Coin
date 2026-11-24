"""
Releases: reproducible builds, Ed25519 signatures, and an installer that
refuses tampered, unsigned, old, or booby-trapped archives.
"""
import io
import json
import os
import shutil
import tarfile
import time

import pytest

from core import update
from core.update import UpdateError
from ui.widgets import MenuScreen, MessageScreen

from test_app import app, create_wallet, enter, press  # noqa: F401

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PW = b"correct horse battery"


@pytest.fixture
def key():
    pem, pub = update.keygen(PW)
    return pem, pub


def release(tmp_path, key, version="0.3.0", src=SRC):
    path, _ = update.build(src, str(tmp_path / "dist"), version)
    update.sign(path, version, key[0], PW)
    return path


# ------------------------------------------------------------------ build
def test_build_is_reproducible(tmp_path):
    a, ha = update.build(SRC, str(tmp_path / "a"), "0.3.0")
    copy = tmp_path / "copy"
    shutil.copytree(SRC, copy, ignore=shutil.ignore_patterns("__pycache__", "dist"))
    for root, _, files in os.walk(copy):              # different times on every file
        for f in files:
            os.utime(os.path.join(root, f), (time.time() - 99999, time.time() - 99999))
    b, hb = update.build(str(copy), str(tmp_path / "b"), "0.3.0")
    assert ha == hb and open(a, "rb").read() == open(b, "rb").read()


def test_release_contents(tmp_path):
    path, _ = update.build(SRC, str(tmp_path), "0.3.0")
    with tarfile.open(path) as t:
        names = t.getnames()
        assert all(n.startswith("czwallet-0.3.0/") for n in names)
        assert "czwallet-0.3.0/core/signer.py" in names
        assert "czwallet-0.3.0/deploy/cz-update" in names
        assert not any("/tests/" in n or "__pycache__" in n or "/preview/" in n for n in names)
        assert {m.mtime for m in t.getmembers()} == {update.EPOCH}
        assert {(m.uid, m.gid) for m in t.getmembers()} == {(0, 0)}


# ----------------------------------------------------------- signatures
def test_sign_and_verify(tmp_path, key):
    path = release(tmp_path, key)
    info = update.verify(path, path + ".sig", [key[1]], current="0.2.0")
    assert info["version"] == "0.3.0" and info["key"] == update.key_id(key[1])


def test_tampered_or_untrusted_is_refused(tmp_path, key):
    path = release(tmp_path, key)
    sig = path + ".sig"
    other = update.keygen(PW)[1]
    with pytest.raises(UpdateError, match="trusted"):
        update.verify(path, sig, [other], current="0.2.0")
    with pytest.raises(UpdateError, match="no release keys"):
        update.verify(path, sig, [], current="0.2.0")
    with pytest.raises(UpdateError, match="newer"):
        update.verify(path, sig, [key[1]], current="0.3.0")       # same version
    with pytest.raises(UpdateError, match="newer"):
        update.verify(path, sig, [key[1]], current="1.0.0")       # downgrade

    s = json.load(open(sig))
    s["version"] = "9.9.9"                                         # claim a higher version
    json.dump(s, open(sig, "w"))
    with pytest.raises(UpdateError, match="trusted"):
        update.verify(path, sig, [key[1]], current="0.2.0")

    path = release(tmp_path, key)                                  # fresh, then flip a byte
    data = bytearray(open(path, "rb").read())
    data[len(data) // 2] ^= 1
    open(path, "wb").write(data)
    with pytest.raises(UpdateError, match="doesn't match"):
        update.verify(path, path + ".sig", [key[1]], current="0.2.0")


def test_renamed_file_is_refused(tmp_path, key):
    path = release(tmp_path, key)
    renamed = str(tmp_path / "czwallet-0.4.0.tar.gz")
    shutil.copy(path, renamed)
    shutil.copy(path + ".sig", renamed + ".sig")
    with pytest.raises(UpdateError, match="name"):
        update.verify(renamed, renamed + ".sig", [key[1]], current="0.2.0")


# -------------------------------------------------------------- install
def fake_install_root(tmp_path, reqs="embit>=0.8\n"):
    releases = tmp_path / "releases"
    cur = releases / "0.2.0"
    cur.mkdir(parents=True)
    (cur / "requirements.txt").write_text(reqs)
    link = tmp_path / "czwallet"
    os.symlink(cur, link)
    return str(releases), str(link)


def test_install_switches_and_keeps_previous(tmp_path, key):
    src = tmp_path / "src"
    shutil.copytree(SRC, src, ignore=shutil.ignore_patterns("__pycache__", "dist"))
    releases, link = fake_install_root(tmp_path, (src / "requirements.txt").read_text())
    path = release(tmp_path, key, src=str(src))
    info = update.install(path, path + ".sig", [key[1]], "0.2.0", releases, link)
    assert info["version"] == "0.3.0"
    assert os.path.realpath(link) == os.path.join(releases, "0.3.0")
    assert os.path.exists(os.path.join(releases, "0.2.0"))        # rollback target kept
    assert os.path.exists(os.path.join(link, "core", "signer.py"))
    assert os.access(os.path.join(link, "deploy", "cz-update"), os.X_OK)


def test_install_refuses_new_packages(tmp_path, key):
    releases, link = fake_install_root(tmp_path, "something-else==1\n")
    path = release(tmp_path, key)
    with pytest.raises(UpdateError, match="packages"):
        update.install(path, path + ".sig", [key[1]], "0.2.0", releases, link)
    assert os.path.realpath(link).endswith("0.2.0")
    assert not os.path.exists(os.path.join(releases, "0.3.0"))


@pytest.mark.parametrize("evil", ["symlink", "dotdot", "absolute", "outside", "device"])
def test_signed_but_booby_trapped_archive_is_refused(tmp_path, key, evil):
    """Even with a valid signature, unpacking only allows plain files."""
    version = "0.3.0"
    path = str(tmp_path / f"czwallet-{version}.tar.gz")
    with tarfile.open(path, "w:gz") as t:
        good = tarfile.TarInfo(f"czwallet-{version}/requirements.txt")
        good.size = 3
        t.addfile(good, io.BytesIO(b"x\n\n"))
        bad = tarfile.TarInfo({"symlink": f"czwallet-{version}/core",
                               "dotdot": f"czwallet-{version}/../../etc/passwd",
                               "absolute": "/etc/passwd",
                               "outside": "elsewhere/app.py",
                               "device": f"czwallet-{version}/dev"}[evil])
        if evil == "symlink":
            bad.type, bad.linkname = tarfile.SYMTYPE, "/etc"
        elif evil == "device":
            bad.type = tarfile.CHRTYPE
        else:
            bad.size = 1
        t.addfile(bad, io.BytesIO(b"!") if bad.isfile() else None)
    update.sign(path, version, key[0], PW)
    releases, link = fake_install_root(tmp_path)
    with pytest.raises(UpdateError, match="unsafe"):
        update.install(path, path + ".sig", [key[1]], "0.2.0", releases, link)
    assert os.path.realpath(link).endswith("0.2.0")
    assert sorted(os.listdir(releases)) == ["0.2.0"]


def test_find_and_allowed_paths(tmp_path, key):
    sd = tmp_path / "boot" / "czwallet-update"
    usb = tmp_path / "media" / "STICK" / "czwallet-update"
    for d in (sd, usb):
        d.mkdir(parents=True)
    path = release(tmp_path, key)
    shutil.copy(path, sd)
    shutil.copy(path + ".sig", sd)
    shutil.copy(path, usb)                                          # no .sig: ignored
    dirs = f"{tmp_path / 'boot'}:{tmp_path / 'media'}"
    found = update.find(dirs)
    assert [os.path.dirname(t) for t, _ in found] == [str(sd)]
    assert update.allowed_path(str(sd / os.path.basename(path)), dirs)
    os.symlink("/etc/passwd", sd / "sneaky.tar.gz")
    assert not update.allowed_path(str(sd / "sneaky.tar.gz"), dirs)
    assert not update.allowed_path("/tmp/x.tar.gz", dirs)


# ------------------------------------------------------------------- UI
def open_update(app):
    press(app, "KEY_X", "KEY_C", "KEY_C")
    enter(app)
    app.visible.sel = next(i for i, it in enumerate(app.visible.items) if it[0] == "UPDATE")
    enter(app)


def test_update_screen(app, tmp_path, key, monkeypatch):  # noqa: F811
    create_wallet(app)
    keys = tmp_path / "keys.txt"
    keys.write_text(f"# test key\n{key[1].hex()}\n")
    real_load = update.load_keys
    monkeypatch.setattr(update, "load_keys", lambda path=None: real_load(str(keys)))
    sd = tmp_path / "sd"
    sd.mkdir()
    for version in ("0.9.0", "0.1.0"):
        p = release(tmp_path, key, version)
        shutil.copy(p, sd)
        shutil.copy(p + ".sig", sd)
    real_find = update.find
    monkeypatch.setattr(update, "find", lambda dirs=None: real_find(str(sd)))
    calls = []
    monkeypatch.setattr(update, "run_helper", lambda p: calls.append(p) or "installed 0.9.0")
    open_update(app)
    assert isinstance(app.visible, MenuScreen) and app.visible.title == "UPDATES"
    states = {it[0]: it[2] for it in app.visible.items}
    assert states == {"VERSION 0.1.0": "REFUSED", "VERSION 0.9.0": "SIGNED"}
    app.render(0)
    enter(app)                                       # 0.1.0: refused, tells why
    assert isinstance(app.visible, MessageScreen) and "newer" in " ".join(app.visible.lines)
    enter(app)
    app.visible.sel = 1
    enter(app)
    assert app.visible.title == "UPDATE TO 0.9.0?"
    app.render(0)
    press(app, "KEY_Z")
    enter(app)
    assert app.visible.title == "UPDATED" and calls == [str(sd / "Insert-Coin-Update-0.9.0.tar.gz")]


def test_update_without_keys(app, monkeypatch):  # noqa: F811
    create_wallet(app)
    monkeypatch.setattr(update, "load_keys", lambda path=None: [])
    open_update(app)
    assert app.visible.title == "NO RELEASE KEY"


def test_lock_matches_pins_and_requirements():
    """Every pinned package is in requirements.lock with hashes, at the same
    version as requirements.txt."""
    import importlib.util
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location("lock", os.path.join(root, "tools", "lock.py"))
    lock = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lock)
    with open(os.path.join(root, "requirements.lock")) as f:
        have = lock.parse(f.read())
    assert {n: v for n, (v, _) in have.items()} == lock.PINS
    assert all(len(h) >= 1 and all(len(x) == 64 for x in h) for _, h in have.values())
    with open(os.path.join(root, "requirements.txt")) as f:
        txt = dict(line.split("#")[0].strip().split("==") for line in f
                   if "==" in line.split("#")[0])
    assert txt == lock.PINS
    with open(os.path.join(root, "deploy", "install.sh")) as f:
        sh = f.read()
    assert "--require-hashes" in sh and "install --upgrade pip" not in sh


def test_project_release_key_is_valid():
    """deploy/release_keys.txt holds the key every install trusts for updates."""
    import os
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from core import update
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "deploy", "release_keys.txt")
    keys = update.load_keys(path)
    assert len(keys) >= 1
    for k in keys:
        Ed25519PublicKey.from_public_bytes(k)
    assert update.key_id(keys[0]) == "d1b3b27e99714b0a"


def test_update_precompiles_the_new_code(tmp_path):
    """Installed code is compiled once by the root helper, so the sandboxed
    services don't recompile every screen each time (they can't write)."""
    from core import update
    src = tmp_path / "0.9.0.new"
    (src / "ui").mkdir(parents=True)
    (src / "ui" / "x.py").write_text("A = 1\n")
    update.precompile(str(src), str(tmp_path / "0.9.0"))
    assert list((src / "ui" / "__pycache__").glob("x.*.pyc"))


def test_a_release_built_on_windows_matches_one_built_on_linux(tmp_path, monkeypatch):
    """Built on Windows, names came out as core\\x.py (refused on the device
    as an unsafe entry), every file looked executable and text files had
    CRLF line endings. A release is now the same bytes from any computer."""
    import os
    import shutil
    import tarfile
    from core import update
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    lin = tmp_path / "lin"
    win = tmp_path / "win"
    for d in (lin, win):
        d.mkdir()
        for item in ("app.py", "core", "deploy"):
            s = os.path.join(root, item)
            (shutil.copytree if os.path.isdir(s) else shutil.copy)(
                s, d / item, **({"ignore": shutil.ignore_patterns("__pycache__")}
                                if os.path.isdir(s) else {}))
    for p in win.rglob("*"):                       # what a Windows checkout looks like
        if p.is_file():
            data = p.read_bytes()
            if b"\0" not in data and p.suffix in (".py", ".service", ".sh", ""):
                p.write_bytes(data.replace(b"\n", b"\r\n"))
            p.chmod(0o755)
    monkeypatch.setattr(update, "INCLUDE", ["app.py", "core", "deploy"])
    a, ha = update.build(str(lin), str(tmp_path / "out1"), "9.9.9")
    b, hb = update.build(str(win), str(tmp_path / "out2"), "9.9.9")
    assert ha == hb
    with tarfile.open(b, "r:gz") as t:
        names = t.getnames()
        assert names and not any("\\" in n for n in names)
        modes = {m.name.split("/", 1)[1]: m.mode for m in t.getmembers()}
    assert modes["core/update.py"] == 0o644 and modes["deploy/cz-backup"] == 0o755
    update.safe_extract(b, "9.9.9", str(tmp_path / "x"))      # the device's own check


def test_backslash_names_become_slashes(monkeypatch, tmp_path):
    from core import update
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "a.py").write_text("x")
    monkeypatch.setattr(update, "INCLUDE", ["core"])
    monkeypatch.setattr(update.os.path, "relpath", lambda p, s: "core\\a.py")
    assert update.collect(str(tmp_path)) == ["core/a.py"]


def test_update_names_new_and_old(tmp_path):
    """Insert-Coin-Update-X.Y.Z.tar.gz since 0.8.26; czwallet-X.Y.Z.tar.gz and
    the czwallet-update folder still work."""
    from core import update
    assert update.file_name("1.2.3") == "Insert-Coin-Update-1.2.3.tar.gz"
    assert update.name_version("/x/Insert-Coin-Update-1.2.3.tar.gz") == "1.2.3"
    assert update.name_version("czwallet-1.2.3.tar.gz") == "1.2.3"
    for bad in ("Insert-Coin-Update-1.2.tar.gz", "evil-1.2.3.tar.gz",
                "Insert-Coin-Update-1.2.3.tar.gz.sig", "../czwallet-1.2.3.tar.gz/x"):
        assert update.name_version(bad) is None
    new = tmp_path / "Insert-Coin-Update"
    old = tmp_path / "czwallet-update"
    new.mkdir()
    old.mkdir()
    for d, n in ((new, "Insert-Coin-Update-0.9.1.tar.gz"), (old, "czwallet-0.9.0.tar.gz")):
        (d / n).write_bytes(b"x")
        (d / (n + ".sig")).write_text("{}")
    found = [p for p, _ in update.find(str(tmp_path))]
    assert sorted(os.path.basename(p) for p in found) == ["Insert-Coin-Update-0.9.1.tar.gz",
                                                         "czwallet-0.9.0.tar.gz"]
