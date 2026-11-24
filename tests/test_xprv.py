"""IMPORT XPRV: a master extended private key instead of seed words."""
import pytest

from core import seed as seedlib
from test_app import app, enter, settle, type_text  # noqa: F401
from ui.flow import HomeScreen, restore_flow
from ui.widgets import MenuScreen, MessageScreen, PinScreen

WORDS = "abandon " * 11 + "about"


def master(network="signet"):
    return seedlib.root_key(WORDS, "", network).to_string()


def test_check_xprv():
    k = master()
    assert k.startswith("tprv") and seedlib.check_xprv(" " + k + " ", "signet") == k
    with pytest.raises(ValueError, match="mainnet|xprv"):
        seedlib.check_xprv(k, "bitcoin")
    acct = seedlib.root_key(WORDS, "", "signet").derive("m/84h/1h/0h").to_string()
    with pytest.raises(ValueError, match="depth-3"):
        seedlib.check_xprv(acct, "signet")
    pub = seedlib.root_key(WORDS, "", "signet").to_public().to_string()
    with pytest.raises(ValueError):
        seedlib.check_xprv(pub, "signet")
    bad = k[:-1] + ("A" if k[-1] != "A" else "B")
    with pytest.raises(ValueError, match="checksum"):
        seedlib.check_xprv(bad, "signet")
    assert seedlib.is_xprv(k) and not seedlib.is_xprv(WORDS)


def test_same_wallet_as_the_words():
    """The master key of a seed opens exactly the wallet those words do."""
    assert seedlib.root_key(master(), "", "signet").to_string() == master()


def _import_typed(app, key):
    restore_flow(app)
    m = app.visible
    m.sel = [i[0] for i in m.items].index("IMPORT XPRV")
    enter(app)
    enter(app)                                       # TYPE IT
    settle(app)
    type_text(app, key)
    enter(app)
    settle(app)


def test_import_typed_then_backups_say_what_they_can(app):  # noqa: F811
    _import_typed(app, master())
    assert isinstance(app.visible, MessageScreen) and app.visible.title == "NO SEED WORDS"
    enter(app)
    for _ in range(2):
        type_text(app, "1234")
        enter(app)
    settle(app)
    enter(app)                                       # READY!
    assert isinstance(app.visible, HomeScreen)
    # same addresses as the 12 words would give
    from core.signer import Signer
    import tempfile
    s = Signer(tempfile.mkdtemp())
    s.setup("1111", WORDS, "signet")
    assert app.wallet.addresses(0, count=1)[0]["address"] == s.address(index=0)
    # SHOW SEED WORDS shows the key itself
    from ui.secure_flows import XprvScreen
    app.prompt("words_prompt", lambda app, r: None)
    settle(app)
    type_text(app, "1234")
    enter(app)
    settle(app)
    assert isinstance(app.visible, XprvScreen) and app.visible.key == master()
    enter(app)
    settle(app)
    # SeedQR and split backups explain they need words
    app.prompt("seedqr_prompt", lambda app, r: None)
    settle(app)
    type_text(app, "1234")
    enter(app)
    settle(app)
    assert app.visible.title == "NO SEEDQR"
    enter(app)
    settle(app)
    app.prompt("split_prompt", lambda app, r: None)
    settle(app)
    enter(app)                                       # 2 OF 3
    settle(app)
    type_text(app, "1234")
    enter(app)
    settle(app)
    assert app.visible.title == "NO SPLIT"


def test_wrong_keys_are_refused_on_the_signer(app):  # noqa: F811
    acct = seedlib.root_key(WORDS, "", "signet").derive("m/84h/1h/0h").to_string()
    _import_typed(app, acct)
    assert "depth" in (app.visible.error or "")


def test_scanned_xprv_says_it_passed_through_the_app(app):  # noqa: F811
    app.prompt("import_prompt", lambda app, r: None, network="signet", mnemonic=master())
    settle(app)
    assert isinstance(app.visible, MessageScreen) and app.visible.title == "XPRV READ"
    enter(app)
    assert isinstance(app.visible, PinScreen)
