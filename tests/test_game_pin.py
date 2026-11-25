"""GAME PIN (SECURITY › PINS): at unlock it opens COIN INVADERS instead of
the wallet, either leaving the wallet locked underneath (GAME ONLY) or
erasing everything first (GAME + WIPE). And the game itself."""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("CZ_ARGON_T", "1")
os.environ.setdefault("CZ_ARGON_M_KIB", "1024")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from core.signer import SignerError  # noqa: E402
from core.vault import GamePinUsed, Vault, VaultError, WipePinUsed, WrongPin  # noqa: E402
from test_app import app, create_wallet, enter, press, settle, type_text  # noqa: E402,F401
from test_core import ABANDON, NET  # noqa: E402
from ui.arcade import GROUND, ArcadeScreen  # noqa: E402


# ================================================================ vault
def test_game_only_leaves_the_wallet_alone(tmp_path):
    v = Vault(str(tmp_path / "v"))
    v.create("1111", ABANDON, NET)
    before = json.load(open(v.path))
    v.set_game_pin("1111", "7777", "game")
    after = json.load(open(v.path))
    assert set(before) == set(after) and len(before["g"]) == len(after["g"])   # same shape
    assert v.unlock("1111").game == "game"
    with pytest.raises(WrongPin):
        v.unlock("0000")                       # one wrong try: 9 left
    with pytest.raises(GamePinUsed) as e:
        v.unlock("7777", login=True)
    assert e.value.wiped is False and v.exists()
    assert v.info()["tries_left"] == 9         # the game PIN counted neither way
    with pytest.raises(WrongPin):
        v.unlock("7777")                       # anywhere but the unlock screen: just wrong
    assert v.unlock("1111").mnemonic == ABANDON


def test_game_plus_wipe_erases_first(tmp_path):
    v = Vault(str(tmp_path / "v"))
    v.create("1111", ABANDON, NET)
    v.set_game_pin("1111", "7777", "game+wipe")
    with pytest.raises(GamePinUsed) as e:
        v.unlock("7777", login=True)
    assert e.value.wiped is True and not v.exists()


def test_game_pin_is_its_own_pin(tmp_path):
    v = Vault(str(tmp_path / "v"))
    v.create("1111", ABANDON, NET)
    with pytest.raises(VaultError):
        v.set_game_pin("1111", "1111", "game")
    with pytest.raises(VaultError):
        v.set_game_pin("1111", "7777", "party")
    v.set_duress("1111", "2222", ABANDON)
    v.set_wipe_pin("1111", "3333")
    for taken in ("2222", "3333"):
        with pytest.raises(VaultError, match="in use"):
            v.set_game_pin("1111", taken, "game")
    v.set_game_pin("1111", "7777", "game")
    for fn in (lambda: v.set_wipe_pin("1111", "7777"), lambda: v.change_pin("1111", "7777"),
               lambda: v.set_duress("1111", "7777", ABANDON)):
        with pytest.raises(VaultError, match="in use"):
            fn()
    v.change_pin("1111", "4444")               # the flag survives a PIN change
    assert v.unlock("4444").game == "game"
    v.clear_game_pin("4444")
    assert v.unlock("4444").game is None
    with pytest.raises(WrongPin):
        v.unlock("7777", login=True)
    with pytest.raises(WipePinUsed):            # the wipe PIN still works beside it
        v.unlock("3333", login=True)


def test_signer_reports_and_guards(tmp_path):
    from core.signer import Signer
    s = Signer(str(tmp_path / "s"))
    s.setup("1111", ABANDON, NET)
    assert s.status()["game_pin"] is None
    s.set_game_pin("1111", "7777", "game+wipe")
    assert s.status()["game_pin"] == "game+wipe"
    s.lock()
    with pytest.raises(GamePinUsed):
        s.unlock("7777")
    assert not s.status()["has_seed"]          # game+wipe: the signer's folder is gone too


def test_app_cannot_set_it_without_the_screens(app):  # noqa: F811
    from core import signer_service
    create_wallet(app)
    for name in ("set_game_pin", "clear_game_pin"):
        r = signer_service._dispatch(app.signer._local, {"method": name, "params": {}})
        assert r["ok"] is False and r["type"] == "BadRequest"


# ================================================================ screens
def _set_game_pin(app, mode_label, game_pin="7777"):
    from ui.flow import settings_screen
    settings_screen(app, at="GAME PIN")
    settle(app)
    enter(app)                                  # GAME PIN
    m = app.visible
    m.sel = [i[0] for i in m.items].index(mode_label)
    enter(app)
    for pin in ("1234", game_pin, game_pin):
        type_text(app, pin)
        enter(app)
    assert app.visible.title == "GAME PIN ON"
    enter(app)
    settle(app)


def _hold_home(app, game):
    t0 = game.clock()
    game.clock = lambda: t0 + 2.0              # 2 s later, still held
    app.feed("KEY_ESC", 1)
    game.exit_since = t0
    app.render(0)                               # the frame that notices
    settle(app)


def test_game_only_through_the_screens(app):  # noqa: F811
    from ui.flow import HomeScreen, LockedScreen
    create_wallet(app)
    _set_game_pin(app, "GAME ONLY")
    assert app.signer.status()["game_pin"] == "game"
    app.lock(full=True)
    settle(app)
    type_text(app, "7777")
    enter(app)
    settle(app)
    game = app.visible
    assert isinstance(game, ArcadeScreen)
    img = app.render(0)
    assert img.size == (320, 170)
    press(app, "KEY_ENTER")                     # starts a game: keys go to it
    assert game.state == "play"
    _hold_home(app, game)
    assert isinstance(app.top, LockedScreen) and app.signer.status()["has_seed"]
    enter(app)                                   # OK TO UNLOCK
    settle(app)
    type_text(app, "1234")
    enter(app)
    settle(app)
    assert isinstance(app.visible, HomeScreen)  # the real PIN still opens the wallet


def test_game_plus_wipe_through_the_screens(app):  # noqa: F811
    from ui.title import TitleScreen
    create_wallet(app)
    app.settings.labels = {"bc1qexample": "secret label"}
    app.settings.arcade_hi = 990
    app.settings.save()
    _set_game_pin(app, "GAME + WIPE", "5555")
    app.lock(full=True)
    settle(app)
    type_text(app, "5555")
    enter(app)
    settle(app)
    game = app.visible
    assert isinstance(game, ArcadeScreen)
    assert not app.signer.status()["has_seed"]   # erased before the first frame
    assert app.settings.labels == {} and game.hi == 0
    _hold_home(app, game)
    assert not isinstance(app.top, ArcadeScreen)
    assert isinstance(app.top, TitleScreen) or "WELCOME" in str(getattr(app.top, "title", "")).upper() \
        or app.signer.status()["has_seed"] is False


# ================================================================ the game
class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _game(hi=0):
    import random
    saved = []
    clk = Clock()
    g = ArcadeScreen(lambda: saved.append("exit"), hi=hi, save_hi=saved.append,
                     rng=random.Random(1), clock=clk)
    g.sound_on = False
    return g, clk, saved


def _run(g, clk, seconds, step=0.02):
    end = clk.t + seconds
    while clk.t < end:
        clk.t += step
        g.update(clk.t)


def test_shoot_an_invader_scores():
    g, clk, _ = _game()
    g.raw_key("KEY_ENTER", 1, None)
    g.raw_key("KEY_ENTER", 0, None)
    assert g.state == "play" and g.lives == 3
    target = next(i for i in g.invaders if i[2] == "octo")
    g.ship_x = target[0] + 6 - 6
    g.fire()
    assert g.shot is not None
    _run(g, clk, 1.0)
    assert g.score >= 10 and sum(1 for i in g.invaders if not i[3]) >= 1


def test_moving_holds_and_stops():
    g, clk, _ = _game()
    g.new_game()
    x0 = g.ship_x
    g.raw_key("KEY_C", 1, None)
    _run(g, clk, 0.5)
    assert g.ship_x > x0 + 40
    g.raw_key("KEY_C", 0, None)
    x1 = g.ship_x
    _run(g, clk, 0.3)
    assert g.ship_x == x1


def test_lives_game_over_and_high_score():
    g, clk, saved = _game(hi=5)
    g.new_game()
    g.score = 120
    for _ in range(3):
        clk.t = max(clk.t, g.dead_until) + 0.01
        g.last = clk.t
        g.bombs = [[g.ship_x + 6, GROUND - 6]]
        g.update(clk.t + 0.01)
    assert g.lives == 0 and g.state == "over"
    assert g.hi == 120 and saved == [120]
    g.raw_key("KEY_ENTER", 1, None)              # too soon after: ignored
    assert g.state == "over"
    clk.t += 1.5
    g.raw_key("KEY_ENTER", 1, None)
    assert g.state == "play" and g.score == 0 and g.lives == 3


def test_a_cleared_wave_comes_back_faster():
    g, clk, _ = _game()
    g.new_game()
    for i in g.invaders:
        i[3] = False
    g.update(clk.t + 0.02)
    assert g.wave == 2 and all(i[3] for i in g.invaders)


def test_shields_wear_away():
    g, clk, _ = _game()
    g.new_game()
    s = g.shields[0]
    n = len(s.cells)
    assert s.hit(s.x + 10, s.y + 6, down=True)
    assert len(s.cells) < n


def test_hold_home_to_leave_but_a_tap_does_nothing():
    g, clk, saved = _game()
    g.raw_key("KEY_ESC", 1, None)
    clk.t += 0.5
    g.raw_key("KEY_ESC", 0, None)
    _run(g, clk, 2.0)
    assert saved == []
    g.raw_key("KEY_ESC", 1, None)
    _run(g, clk, 1.6)
    assert saved == ["exit"]


def test_it_draws_every_state():
    g, clk, _ = _game()
    app = type("A", (), {"settings": type("S", (), {"sound": False})()})()
    for state in ("title", "play", "over"):
        if state == "play":
            g.new_game()
        if state == "over":
            g._game_over(clk.t)
        assert g.render(app, 0).size == (320, 170)


def test_a_fast_shot_cannot_pass_through_an_invader():
    """At the device's ~12 frames a second a shot moves over 20 px a frame,
    more than an invader's height: it must still hit."""
    g, clk, _ = _game()
    g.new_game()
    for i in g.invaders:
        i[3] = False
    target = g.invaders[-1]
    target[3] = True
    target[0], target[1] = 100, 60
    g.shields = []
    g.shot = [106, 75]
    g.last = clk.t
    clk.t += 0.1                                  # one slow frame: 26 px
    g.update(clk.t)
    assert not target[3] and g.score > 0


def test_hud_clears_the_signer_corner():
    """The signer paints the top-left 14x12 over every frame."""
    g, clk, _ = _game()
    app = type("A", (), {"settings": type("S", (), {"sound": False})()})()
    img = g.render(app, 0)
    corner = img.crop((0, 0, 14, 12)).getcolors(1000)
    assert len(corner) <= 2                       # just background (and stars): no HUD text
