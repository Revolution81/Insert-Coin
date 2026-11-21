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


def test_menu_sounds_are_short_and_soft():
    import io, wave, struct
    for name in sound.UI_SOUNDS:
        w = wave.open(io.BytesIO(sound.wav(name)))
        n = w.getnframes()
        data = struct.unpack(f"<{n}h", w.readframes(n))
        assert w.getframerate() == 44100
        assert n / 44100 <= 0.04                             # one short note
        assert 10000 < max(abs(x) for x in data) < 20000      # well under the coin sound
        assert abs(data[0]) < 200                            # no click at the front


def test_direct_fanfare_is_quieter_with_a_longer_lead_in():
    """Straight to the codec (Savings image): a lead-in long enough for the
    amp to wake (it swallowed all but the last note) and about 1/4 level."""
    import array
    def frames(b):
        with wave.open(io.BytesIO(b)) as w:
            return array.array("h", w.readframes(w.getnframes())), w.getnframes()
    loud, n1 = frames(sound.wav("fanfare"))
    quiet, n2 = frames(sound.wav("fanfare", direct=True))
    assert n1 - n2 == int(sound.LEAD_IN * sound.RATE)     # direct: the stream warms up
    assert sound.DIRECT_LEAD_IN >= 0.8
    ratio = max(abs(x) for x in quiet) / max(abs(x) for x in loud)
    assert 0.25 < ratio < 0.35


def test_direct_stream_keeps_the_amp_awake_for_boops(monkeypatch, tmp_path):
    """One aplay stays open: it starts with a warm-up silence, later sounds
    (the 35 ms boops) go into the same stream, mixed, no new aplay each."""
    import subprocess
    started = []

    class FakeProc:
        def __init__(self, cmd, **kw):
            started.append(cmd)
            self.stdin = open(tmp_path / "out.raw", "wb")
        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(subprocess, "Popen", FakeProc)
    s = sound._Stream(["aplay", "-"])
    s.IDLE_STOP = 0.05
    boop = sound._pcm(sound.wav("ui_move", direct=True))
    s.add(boop)
    s.add(boop)
    assert len(started) == 1
    assert len(s.pending) >= int(sound.DIRECT_LEAD_IN * sound.RATE)
    import time
    for _ in range(200):
        if s.proc is None:
            break
        time.sleep(0.02)
    assert s.proc is None                     # closed after going idle
    assert (tmp_path / "out.raw").stat().st_size >= 2 * int(sound.DIRECT_LEAD_IN * sound.RATE)


def test_konami_code_on_the_title_plays_the_secret(tmp_path, monkeypatch):
    """UP UP DOWN DOWN LEFT RIGHT LEFT RIGHT B A, then OK (START) on the
    title: the secret jingle and the heroes; any key back to the title, and
    a plain OK then carries on as usual."""
    from ui.egg import EggScreen
    played = []
    sound.set_player(lambda name, data: played.append(name))
    try:
        app = make(tmp_path, monkeypatch)
        app.start(title=True)
        for k in ("KEY_F", "KEY_F", "KEY_X", "KEY_X", "KEY_Z", "KEY_C", "KEY_Z", "KEY_C",
                  "KEY_B", "KEY_A"):
            press(app, k)
        assert isinstance(app.visible, TitleScreen) and not played
        enter(app)
        assert played == ["secret"] and isinstance(app.visible, EggScreen)
        app.render(0)
        press(app, "KEY_A")
        assert isinstance(app.visible, TitleScreen)
        enter(app)
        assert played == ["secret", "fanfare"]
        assert not isinstance(app.visible, TitleScreen)
    finally:
        sound.set_player(None)


def test_almost_the_konami_code_is_just_ok(tmp_path, monkeypatch):
    played = []
    sound.set_player(lambda name, data: played.append(name))
    try:
        app = make(tmp_path, monkeypatch)
        app.start(title=True)
        for k in ("KEY_F", "KEY_F", "KEY_X", "KEY_X", "KEY_Z", "KEY_C", "KEY_Z", "KEY_C", "KEY_A"):
            press(app, k)
        enter(app)
        assert played == ["fanfare"]
    finally:
        sound.set_player(None)


def test_secret_jingle_is_a_good_wav():
    with wave.open(io.BytesIO(sound.wav("secret"))) as w:
        assert w.getframerate() == sound.RATE and 0.5 < w.getnframes() / w.getframerate() < 2.0
    assert sound.wav("secret") != sound.wav("fanfare")


def test_boop_is_high_enough_for_the_little_speaker():
    """The Cardputer's speaker plays almost nothing under ~500 Hz."""
    import array
    with wave.open(io.BytesIO(sound.wav("ui_move"))) as w:
        a = array.array("h", w.readframes(w.getnframes()))
    crossings = sum(1 for x, y in zip(a, a[1:]) if x < 0 <= y)
    assert crossings / (len(a) / sound.RATE) > 500


def test_volume_steps_scale_the_sound():
    peak = lambda: max(abs(x) for x in sound._pcm_samples(sound.wav("fanfare")))
    try:
        sound.set_volume(10); full = peak()
        sound.set_volume(5); half = peak()
        sound.set_volume(0); zero = peak()
        assert abs(half / full - 0.5) < 0.02 and zero == 0
        sound.set_volume(42)
        assert sound.volume() == 10
    finally:
        sound.set_volume(None)


def test_volume_keys_change_save_and_show(tmp_path, monkeypatch):
    """Fn+D / Fn+A step the volume, Fn+S is the sound switch; saved, and the
    pop-up shows in the middle of the screen. Works from any screen."""
    from core.config import Settings
    played = []
    sound.set_player(lambda name, data: played.append(name))
    try:
        app = make(tmp_path, monkeypatch)
        app.start()
        create_wallet(app)
        sound.set_volume(3)
        app.settings.volume = 3
        before = app.render(0)
        press(app, "KEY_VOLUMEUP")
        assert sound.volume() == 4 and Settings.load().volume == 4
        assert played[-1] == "ui_move"
        shown = app.render(0)
        assert shown.getpixel((160, 85)) != before.getpixel((160, 85))
        press(app, "KEY_VOLUMEDOWN"); press(app, "KEY_VOLUMEDOWN")
        assert sound.volume() == 2
        press(app, "KEY_MUTE")
        assert app.settings.sound is False and Settings.load().sound is False
        n = len(played)
        press(app, "KEY_VOLUMEUP")                  # louder turns it back on
        assert app.settings.sound is True and sound.volume() == 3 and len(played) == n + 1
        app.osd_until = 0
        assert app.render(0).getpixel((160, 85)) == before.getpixel((160, 85))
    finally:
        sound.set_player(None)
        sound.set_volume(None)
