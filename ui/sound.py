"""
Sound, synthesized here (no audio files, nothing borrowed from any game).
One switch, SETUP › SOUND, turns all of it on or off; it's saved, so it
stays that way after a restart.

  FANFARE   the title screen's OK: a chiptune treasure fanfare,
            "da-da-da-DAAA", two square-wave voices and a triangle bass with
            noise hats, the last note holding with a wobble
  BOOP      one short, low note (35 ms) for moving, OK and BACK in menus.
            A gentle 2 ms start (no click), under half the fanfare's level.
            A 1 W speaker can't play much below ~300 Hz, so the note also
            carries its 2nd and 3rd harmonics: the ear hears the low note
            from those even where the speaker drops it.

On the device it plays through ALSA (`aplay`, the 1 W speaker on the
ES8389 codec) without blocking; the simulator plugs in pygame instead
(set_player). Failing to play is never an error: no speaker, no sound.
"""
from __future__ import annotations

import io
import math
import os
import random
import struct
import subprocess
import tempfile
import wave

RATE = 44100
_player = None          # callable(name, wav_bytes) set by the simulator
_cache: dict[str, bytes] = {}

TWO_PI = 2 * math.pi


def _blank(seconds: float) -> list[float]:
    return [0.0] * int(seconds * RATE)


def _mix(buf: list[float], at: float, samples: list[float], gain: float = 1.0) -> None:
    i0 = int(at * RATE)
    if i0 + len(samples) > len(buf):
        buf.extend([0.0] * (i0 + len(samples) - len(buf)))
    for i, s in enumerate(samples):
        buf[i0 + i] += gain * s


def _fade(out: list[float], seconds: float = 0.01) -> list[float]:
    n = min(len(out), int(seconds * RATE))
    for i in range(n):
        out[-n + i] *= 1 - i / n
    return out


# ------------------------------------------------------------ the sounds
def _highpass(x: list[float], cutoff: float) -> list[float]:
    """A 2nd-order Butterworth high-pass (one biquad)."""
    k = math.tan(math.pi * cutoff / RATE)
    norm = 1 / (1 + math.sqrt(2) * k + k * k)
    b0, b1, b2 = norm, -2 * norm, norm
    a1, a2 = 2 * (k * k - 1) * norm, (1 - math.sqrt(2) * k + k * k) * norm
    out, x1, x2, y1, y2 = [], 0.0, 0.0, 0.0, 0.0
    for v in x:
        y = b0 * v + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1, y2, y1 = x1, v, y1, y
        out.append(y)
    return out


def _voice(freq: float, dur: float, wave: str, duty: float = 0.5, vib: float = 0.0,
           decay: float = 0.0) -> list[float]:
    """One chip voice: pulse or triangle, 2 ms attack, 40 ms release, optional
    vibrato fading in after 150 ms."""
    n = int(dur * RATE)
    rel = int(0.04 * RATE)
    out, ph = [], 0.0
    for i in range(n):
        t = i / RATE
        f = freq
        if vib:
            f *= 1 + vib * math.sin(TWO_PI * 5.5 * t) * min(1.0, max(0.0, (t - 0.15) / 0.15))
        ph = (ph + f / RATE) % 1.0
        if wave == "tri":
            v = 2 * abs(2 * ph - 1) - 1
        else:
            v = 1.0 if ph < duty else -1.0
        e = min(1.0, t / 0.002) * math.exp(-t * decay)
        if i >= n - rel:
            e *= (n - 1 - i) / (rel - 1)
        out.append(v * e)
    return out


def _fanfare() -> list[float]:
    """Chiptune treasure fanfare: lead (25% pulse), harmony (50% pulse), triangle
    bass and a noise hat on each step; the last chord holds with vibrato."""
    rnd = random.Random(1)
    out = _blank(1.5)
    step = 0.1
    lead = [783.99, 783.99, 880.0, 987.77, 1046.5]          # G5 G5 A5 B5 C6
    harm = [659.26, 659.26, 698.46, 783.99, 659.26]         # E5 E5 F5 G5 E5
    bass = [196.0, 196.0, 174.61, 196.0, 130.81]            # G3 G3 F3 G3 C3
    for k in range(5):
        at = step * k
        last = k == 4
        d = 0.9 if last else step * 0.8
        dec = 1.5 if last else 0.0
        _mix(out, at, _voice(lead[k], d, "pulse", 0.25, 0.008 if last else 0.0, dec), 0.22)
        _mix(out, at, _voice(harm[k], d, "pulse", 0.5, 0.0, dec), 0.12)
        _mix(out, at, _voice(bass[k], d, "tri"), 0.35)
        hat = _highpass([rnd.uniform(-1, 1) for _ in range(int(0.05 * RATE))], 7000)
        _mix(out, at, [v * math.exp(-i / RATE * 90) for i, v in enumerate(hat)], 0.25)
    sh = _highpass([rnd.uniform(-1, 1) for _ in range(int(0.5 * RATE))], 3000)
    _mix(out, 4 * step, [v * math.exp(-i / RATE * 7) for i, v in enumerate(sh)], 0.2)
    return _fade(out, 0.15)


def _note(freq: float, length: float = 0.035) -> list[float]:
    """One short, steady low note: no slide, no echo. 2 ms fade-in so there's
    no click at the front, a quick fall and a short tail fade."""
    out = []
    for i in range(int(length * RATE)):
        t = i / RATE
        ph = TWO_PI * freq * t
        env = min(1.0, t / 0.002) * math.exp(-t * 60)
        out.append(env * (math.sin(ph) + 0.35 * math.sin(2 * ph) + 0.12 * math.sin(3 * ph)))
    return _fade(out, 0.006)


def _ui_move() -> list[float]:
    return _note(196.0)             # G3


_ui_ok = _ui_move                   # the same single boop for everything
_ui_back = _ui_move


UI_SOUNDS = {"ui_move": _ui_move, "ui_ok": _ui_ok, "ui_back": _ui_back}
UI_LEVEL = 0.45                 # the boop sits well under the fanfare
_last_ui = 0.0

SOUNDS = {"fanfare": _fanfare}
DEFAULT = "fanfare"
# PipeWire suspends the speaker when it's idle and the amplifier takes a
# moment to wake, which clipped the start of the fanfare on the Cardputer
# Zero. A short silence in front gives it time. CZ_SOUND_LEAD overrides.
LEAD_IN = float(os.environ.get("CZ_SOUND_LEAD", "0.25"))


def wav(name: str) -> bytes:
    if name not in SOUNDS and name not in UI_SOUNDS:
        name = DEFAULT
    if name not in _cache:
        samples = (UI_SOUNDS.get(name) or SOUNDS[name])()
        if name in SOUNDS and LEAD_IN > 0:
            samples = _blank(LEAD_IN) + samples
        peak = max(1e-9, max(abs(s) for s in samples))
        k = 0.89 / peak
        if name in UI_SOUNDS:
            k = UI_LEVEL / peak
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s * k)) * 32000))
                                   for s in samples))
        _cache[name] = buf.getvalue()
    return _cache[name]


def prepare(name: str) -> None:
    """Make a sound ahead of time in the background (the title screen does
    this, so pressing OK plays at once even on the device's slower CPU)."""
    import threading
    threading.Thread(target=lambda: _safe_wav(name), daemon=True).start()


def _safe_wav(name: str) -> None:
    try:
        wav(name)
    except Exception:  # noqa: BLE001 - it'll be made when played instead
        pass


def set_player(fn) -> None:
    global _player
    _player = fn


def _player_cmd() -> list[str]:
    """The Cardputer Zero's image runs PipeWire, which holds the sound card:
    play through it when its socket is there (as M5Stack's launcher does),
    else straight to ALSA. CZ_PLAYER overrides, e.g. "aplay -D plughw:0"."""
    import shutil
    if os.environ.get("CZ_PLAYER"):
        return os.environ["CZ_PLAYER"].split()
    run = os.environ.get("XDG_RUNTIME_DIR", "")
    if run and os.path.exists(os.path.join(run, "pipewire-0")) and shutil.which("pw-play"):
        return ["pw-play"]
    if run and os.path.exists(os.path.join(run, "pulse", "native")) and shutil.which("paplay"):
        return ["paplay"]
    card = speaker_card()
    return ["aplay", "-q"] + (["-D", f"plughw:{card}"] if card is not None else [])


def speaker_card(cards: str | None = None) -> int | None:
    """The built-in speaker's ALSA card number. Without a sound server, plain
    aplay plays to card 0, which on the Cardputer Zero is HDMI (vc4-hdmi);
    the speaker is the other card (simple-card). None: leave it to ALSA."""
    if cards is None:
        try:
            with open("/proc/asound/cards") as f:
                cards = f.read()
        except OSError:
            return None
    import re
    found = [(int(m.group(1)), m.group(2).lower())
             for m in re.finditer(r"^\s*(\d+)\s+\[[^\]]*\]:\s*(.*)$", cards, re.M)]
    for n, desc in found:
        if "hdmi" not in desc:
            return n
    return None


def play(name: str, enabled: bool = True) -> None:
    if not enabled or os.environ.get("CZ_SOUND") == "0":
        return
    if name not in SOUNDS and name not in UI_SOUNDS:
        name = DEFAULT
    try:
        data = wav(name)
        if _player is not None:
            _player(name, data)
            return
        import hashlib                              # new version, new file: never a stale one
        tag = hashlib.sha256(data).hexdigest()[:8]
        path = os.path.join(tempfile.gettempdir(), f"InsertCoin-{name}-{tag}.wav")
        if not os.path.exists(path):
            with open(path, "wb") as f:
                f.write(data)
        subprocess.Popen(_player_cmd() + [path], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except Exception:  # noqa: BLE001 - no speaker, no sound; never an error
        pass


def menu(kind: str, settings) -> None:
    """The menu boop ("move", "ok" or "back"), if SOUND is on.
    Held keys repeat fast, so at most one every 40 ms."""
    global _last_ui
    if not getattr(settings, "sound", False):
        return
    import time
    now = time.monotonic()
    if now - _last_ui < 0.04:
        return
    _last_ui = now
    play("ui_" + kind)
