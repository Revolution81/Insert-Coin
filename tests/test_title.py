"""The INSERT COIN title screen and the coin chime."""
import io
import wave

from app import App
from core.signer_service import SignerClient
from ui import sound
from ui.title import TitleScreen, draw
from ui.widgets import MenuScreen

from test_app import create_wallet, enter, press, settle


def make(tmp_path, monkeypatch):
    import core.config
    monkeypatch.setattr(core.config, "APP_DIR", str(tmp_path / "app"))
    a = App(signer=SignerClient(inproc=True, data_dir=str(tmp_path / "signer")),
            sync_jobs=True)
    a.settings.network = "signet"
    a.outbox_dir = str(tmp_path / "outbox")
    return a


def test_chime_is_a_short_valid_wav():
    with wave.open(io.BytesIO(sound.wav("fanfare"))) as w:
        assert w.getnchannels() == 1 and w.getframerate() == sound.RATE
        assert 0.3 < w.getnframes() / w.getframerate() < 1.6 + sound.LEAD_IN


def test_title_draws_and_animates():
    a, b = draw(0, now=0.0), draw(0, now=4 * 0.08)        # coin facing vs edge-on
    assert a.size == (320, 170) and a.tobytes() != b.tobytes()
    assert draw(0, now=0.0).tobytes() != draw(1, now=0.0).tobytes()   # PRESS OK blinks


def test_boot_shows_title_then_chimes_and_carries_on(tmp_path, monkeypatch):
    played = []
    sound.set_player(lambda name, data: played.append(name))
    try:
        app = make(tmp_path, monkeypatch)
        app.start(title=True)
        assert isinstance(app.visible, TitleScreen) and app.animated
        app.render(0)
        press(app, "KEY_X")                                  # other keys do nothing
        assert isinstance(app.visible, TitleScreen) and not played
        enter(app)
        assert played == ["fanfare"]                     # the default
        assert not isinstance(app.visible, TitleScreen)      # on to the welcome screen
        create_wallet(app)
    finally:
        sound.set_player(None)


def test_sound_off_is_silent(tmp_path, monkeypatch):
    played = []
    sound.set_player(lambda name, data: played.append(name))
    try:
        app = make(tmp_path, monkeypatch)
        app.settings.sound = False
        app.start(title=True)
        enter(app)
        assert played == []
    finally:
        sound.set_player(None)


def test_one_sound_switch_in_setup_saved_across_restarts(tmp_path, monkeypatch):
    played = []
    sound.set_player(lambda name, data: played.append(name))
    monkeypatch.setattr(sound, "_last_ui", 0.0)
    try:
        app = make(tmp_path, monkeypatch)
        app.start()
        create_wallet(app)
        from ui.flow import settings_screen
        settings_screen(app, at="SOUND")
        settle(app)
        menu = app.visible
        assert isinstance(menu, MenuScreen)
        names = [i[0] for i in menu.items]
        assert "SOUND" in names and "MENU SOUND" not in names
        menu.sel = names.index("SOUND")

        def shown():
            return dict((i[0], i[2] if len(i) > 2 else "") for i in menu.items)["SOUND"]
        assert shown() == "ON" and app.settings.sound is True      # on by default
        played.clear()
        press(app, "KEY_RIGHT")                                    # off
        assert shown() == "OFF" and app.settings.sound is False and played == []
        sound._last_ui = 0.0
        press(app, "KEY_DOWN")                                     # menus are silent now
        press(app, "KEY_UP")
        assert played == []
        # saved: a restart (fresh Settings from disk) keeps it off
        from core.config import Settings
        assert Settings.load().sound is False
        app.replace(TitleScreen(lambda app: app.home()))
        enter(app)
        assert played == []                                        # no fanfare either
        # back on: one boop to confirm, then menus and the title play again
        settings_screen(app, at="SOUND")
        settle(app)
        menu = app.visible
        menu.sel = [i[0] for i in menu.items].index("SOUND")
        press(app, "KEY_LEFT")
        assert app.settings.sound is True and played == ["ui_move"]
        assert Settings.load().sound is True
        played.clear()
        app.replace(TitleScreen(lambda app: app.home()))
        enter(app)
        assert played == ["fanfare"]
    finally:
        sound.set_player(None)


def test_the_fanfare_is_a_good_wav():
    with wave.open(io.BytesIO(sound.wav("fanfare"))) as w:
        secs = w.getnframes() / w.getframerate()
        assert w.getframerate() == sound.RATE and 0.15 < secs < 1.6 + sound.LEAD_IN
        frames = w.readframes(w.getnframes())
    peak = max(abs(int.from_bytes(frames[i:i + 2], "little", signed=True))
               for i in range(0, len(frames), 2))
    assert 25000 < peak <= 32000
    assert sound.wav("no-such-sound") == sound.wav("fanfare")


def test_no_speaker_is_not_an_error(monkeypatch):
    monkeypatch.setattr(sound, "_player", None)
    monkeypatch.setattr(sound.subprocess, "Popen",
                        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("aplay")))
    sound.play("fanfare")                                    # silently nothing


def test_menu_boop(tmp_path, monkeypatch):
    played = []
    sound.set_player(lambda name, data: played.append(name))
    monkeypatch.setattr(sound, "_last_ui", 0.0)
    try:
        app = make(tmp_path, monkeypatch)
        app.start()
        create_wallet(app)
        from ui.flow import settings_screen
        settings_screen(app)
        settle(app)
        played.clear()
        sound._last_ui = 0.0
        press(app, "KEY_DOWN")
        assert played == ["ui_move"]
    finally:
        sound.set_player(None)


def test_menu_sounds_are_soft_and_low():
    import io, wave, struct
    for name in sound.UI_SOUNDS:
        w = wave.open(io.BytesIO(sound.wav(name)))
        n = w.getnframes()
        data = struct.unpack(f"<{n}h", w.readframes(n))
        assert w.getframerate() == 44100
        assert n / 44100 <= 0.04                             # one short note
        assert 10000 < max(abs(x) for x in data) < 16000      # under half the coin sound
        assert abs(data[0]) < 200                            # no click at the front
