"""
Keyboard input for Insert Coin (the Cardputer Zero's keyboard).

Two modes, picked by the current screen (never toggled by hand):

MENU (default)
    F / Z / X / C   up / left / down / right   (no fn needed; the arrows
                    printed on the Cardputer Zero's keys: F▲ Z◀ X▼ C▶)
    OK (Enter)      select
    DEL             back
    HOME button     home screen (ESC on a PC keyboard)
    other letters   hotkeys (e.g. S = send, R = receive)
    fn + L / M      page up / page down in long lists

TEXT (passphrase, labels, Wi-Fi password)
    every key types; shift and sym give capitals and the printed symbols
    fn + F/Z/X/C    move the text cursor
    fn + K / N      cursor to start / end
    OK              submit, DEL backspace, ESC cancel

Modifiers (shift, sym, fn, ctrl, alt) work held down or tapped: a tap arms
it for the next key only, which is easier on a keyboard this small.

The translator works on Linux key *names* ("KEY_A"), so it runs and tests
anywhere. KeyboardDevice feeds it from evdev on the device.
"""
from __future__ import annotations

import enum
import os
from dataclasses import dataclass


class Mode(enum.Enum):
    MENU = "menu"
    TEXT = "text"


class Kind(enum.Enum):
    NAV = "nav"          # UP DOWN LEFT RIGHT PAGE_UP PAGE_DOWN (menu mode)
    SELECT = "select"
    BACK = "back"
    HOME = "home"
    HOTKEY = "hotkey"    # value: upper-case letter or digit
    TAB = "tab"
    CHAR = "char"        # value: the character typed (text mode)
    CURSOR = "cursor"    # LEFT RIGHT UP DOWN START END (text mode)
    BACKSPACE = "backspace"
    SUBMIT = "submit"
    CANCEL = "cancel"


@dataclass(frozen=True)
class Event:
    kind: Kind
    value: str = ""


# ---------------------------------------------------------------------------
# Key names. If the device's driver reports different codes, adjust these
# after running tools/keytest.py (or override with CZ_KEY_<ROLE>=KEY_NAME).
# ---------------------------------------------------------------------------
def _key(role: str, default: str) -> str:
    return os.environ.get(f"CZ_KEY_{role}", default)


KEY_SHIFT = {_key("SHIFT", "KEY_LEFTSHIFT"), "KEY_RIGHTSHIFT"}
KEY_FN = {_key("FN", "KEY_FN")}
KEY_SYM = {_key("SYM", "KEY_RIGHTALT")}   # simulator; on the device the driver does sym (see DRIVER_SYMBOLS)
KEY_CTRL = {"KEY_LEFTCTRL", "KEY_RIGHTCTRL"}
KEY_ALT = {"KEY_LEFTALT"}
KEY_OK = {"KEY_ENTER", "KEY_KPENTER", "KEY_OK"}
KEY_DEL = {"KEY_BACKSPACE"}
KEY_ESC = {"KEY_ESC", "KEY_HOMEPAGE"}
KEY_TAB = {"KEY_TAB"}

# Menu arrows: the inverted T printed on the keys.
MENU_ARROWS = {"KEY_F": "UP", "KEY_Z": "LEFT", "KEY_X": "DOWN", "KEY_C": "RIGHT"}
# If the kernel driver already turns fn+F into KEY_UP, accept that too.
REAL_ARROWS = {"KEY_UP": "UP", "KEY_LEFT": "LEFT", "KEY_DOWN": "DOWN",
               "KEY_RIGHT": "RIGHT", "KEY_HOME": "START", "KEY_END": "END",
               "KEY_PAGEUP": "PAGE_UP", "KEY_PAGEDOWN": "PAGE_DOWN"}
FN_LAYER = {"KEY_F": "UP", "KEY_Z": "LEFT", "KEY_X": "DOWN", "KEY_C": "RIGHT",
            "KEY_K": "START", "KEY_N": "END", "KEY_L": "PAGE_UP", "KEY_M": "PAGE_DOWN"}

LETTERS = {f"KEY_{c}": c.lower() for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}
DIGITS = {f"KEY_{d}": d for d in "1234567890"}

# Symbols printed on the keys (sym layer), read off the keycaps.
SYM_LAYER = {
    "KEY_1": "!", "KEY_2": "@", "KEY_3": "#", "KEY_4": "$", "KEY_5": "%",
    "KEY_6": "^", "KEY_7": "&", "KEY_8": "*", "KEY_9": "(", "KEY_0": ")",
    "KEY_Q": "~", "KEY_W": "`", "KEY_E": "_", "KEY_R": "-", "KEY_T": "+",
    "KEY_Y": "=", "KEY_U": "[", "KEY_I": "]", "KEY_O": "{", "KEY_P": "}",
    "KEY_A": ";", "KEY_S": ":", "KEY_D": "'", "KEY_G": '"', "KEY_H": "<",
    "KEY_J": ">", "KEY_K": "\\", "KEY_L": "|",
    "KEY_V": ",", "KEY_B": ".", "KEY_N": "/", "KEY_M": "?",
    # F, Z, X, C carry the arrows instead of symbols. Read off a production
    # Cardputer Zero (November 2026).
}
# Shift on a digit gives the same symbol as sym, like a normal keyboard.
SHIFT_DIGITS = {k: SYM_LAYER[k] for k in DIGITS}
# Punctuation keys, in case the driver reports them directly.
PUNCT = {"KEY_SPACE": " ", "KEY_COMMA": ",", "KEY_DOT": ".", "KEY_SLASH": "/",
         "KEY_MINUS": "-", "KEY_EQUAL": "=", "KEY_SEMICOLON": ";",
         "KEY_APOSTROPHE": "'", "KEY_LEFTBRACE": "[", "KEY_RIGHTBRACE": "]",
         "KEY_BACKSLASH": "\\", "KEY_GRAVE": "`"}

# The Cardputer Zero's keyboard driver does the sym layer itself: sym + a
# key arrives as its own key code (sym + S = code 80 = ":"), and sym alone
# as KEY_CD. M5Stack's launcher reads the same table from this file; the
# defaults below are the ones in its source (cp0_keyboard_keymap.c, MIT).
DRIVER_KEYMAP = "/usr/share/keymaps/tca8418_keypad_m5stack_keymap.map"
DRIVER_SYMBOLS = {
    26: "!", 27: "@", 39: "#", 40: "$", 41: "%", 43: "^", 51: "&", 52: "*",
    53: "(", 94: ")", 55: "~", 69: "`", 70: "_", 71: "-", 72: "+", 73: "=",
    74: "[", 75: "]", 76: "{", 77: "}", 79: ";", 80: ":", 81: "'", 82: '"',
    83: "<", 85: ">", 86: "\\", 89: "|", 90: ",", 91: ".", 92: "/", 93: "?",
}
_KEYSYM_CHARS = {
    "exclam": "!", "at": "@", "numbersign": "#", "dollar": "$", "percent": "%",
    "asciicircum": "^", "ampersand": "&", "asterisk": "*", "parenleft": "(",
    "parenright": ")", "asciitilde": "~", "grave": "`", "underscore": "_",
    "minus": "-", "plus": "+", "equal": "=", "bracketleft": "[",
    "bracketright": "]", "braceleft": "{", "braceright": "}", "semicolon": ";",
    "colon": ":", "apostrophe": "'", "quotedbl": '"', "less": "<",
    "greater": ">", "backslash": "\\", "bar": "|", "comma": ",", "period": ".",
    "slash": "/", "question": "?",
}
# What the driver sends for the sym and fn keys themselves: nothing to type.
DRIVER_IGNORED = {"KEY_CD", "KEY_DVD"}
CHR = "CHR:"          # name prefix for a finished character from the driver


def load_driver_symbols(path: str = DRIVER_KEYMAP) -> dict[int, str]:
    """{key code: character}, from the device's keymap file if it has one
    (lines like "keycode 80 = colon"), else the defaults."""
    try:
        with open(path) as f:
            lines = f.read(64 * 1024).splitlines()
    except OSError:
        return dict(DRIVER_SYMBOLS)
    out = {}
    for line in lines:
        parts = line.split("#", 1)[0].split()
        if len(parts) >= 4 and parts[0] == "keycode" and parts[2] == "=" and parts[1].isdigit():
            ch = _KEYSYM_CHARS.get(parts[3])
            if ch:
                out[int(parts[1])] = ch
    return out or dict(DRIVER_SYMBOLS)


MODIFIERS = {"shift": KEY_SHIFT, "fn": KEY_FN, "sym": KEY_SYM,
             "ctrl": KEY_CTRL, "alt": KEY_ALT}


class Translator:
    """Turns raw key presses into wallet events for the current mode."""

    def __init__(self, mode: Mode = Mode.MENU):
        self.mode = mode
        self.held: set[str] = set()       # modifiers physically down
        self.armed: set[str] = set()      # tapped modifiers, for the next key
        self._used_while_held: set[str] = set()

    def set_mode(self, mode: Mode) -> None:
        self.mode = mode
        self.armed.clear()   # never carry a stray shift into a new field

    def reset(self) -> None:
        """Forget modifier state: the keyboard was just handed over between
        the app and the signer, so half a shift tap means nothing."""
        self.held.clear()
        self.armed.clear()
        self._used_while_held.clear()

    # value: 1 = press, 0 = release, 2 = auto-repeat (evdev convention)
    def feed(self, name: str, value: int) -> list[Event]:
        mod = self._modifier_of(name)
        if mod:
            if value == 1:
                self.held.add(mod)
                self._used_while_held.discard(mod)
            elif value == 0:
                self.held.discard(mod)
                if mod not in self._used_while_held:
                    # a tap: toggle it armed for the next key
                    self.armed ^= {mod}
            return []
        if value == 0:
            return []

        active = self.held | self.armed
        self._used_while_held |= self.held
        if value == 1:
            self.armed.clear()
        repeat = value == 2

        if self.mode is Mode.MENU:
            return self._menu(name, active, repeat)
        return self._text(name, active, repeat)

    # ----------------------------------------------------------------- menu
    def _menu(self, name, active, repeat):
        if name in MENU_ARROWS and "fn" not in active:
            return [Event(Kind.NAV, MENU_ARROWS[name])]
        if "fn" in active and name in FN_LAYER:
            return [Event(Kind.NAV, FN_LAYER[name])]
        if name in REAL_ARROWS:
            v = REAL_ARROWS[name]
            return [Event(Kind.NAV, {"START": "PAGE_UP", "END": "PAGE_DOWN"}.get(v, v))]
        if repeat:
            return []   # only arrows auto-repeat in menus
        if name in KEY_OK:
            return [Event(Kind.SELECT)]
        if name in KEY_DEL:
            return [Event(Kind.BACK)]
        if name in KEY_ESC:
            return [Event(Kind.HOME)]
        if name in KEY_TAB:
            return [Event(Kind.TAB)]
        if name in LETTERS:
            return [Event(Kind.HOTKEY, LETTERS[name].upper())]
        if name in DIGITS:
            return [Event(Kind.HOTKEY, DIGITS[name])]
        return []

    # ----------------------------------------------------------------- text
    def _text(self, name, active, repeat):
        if "fn" in active and name in FN_LAYER:
            return [Event(Kind.CURSOR, FN_LAYER[name])]
        if name in REAL_ARROWS:
            return [Event(Kind.CURSOR, REAL_ARROWS[name])]
        if name in KEY_DEL:
            return [Event(Kind.BACKSPACE)]
        if repeat and name not in LETTERS and name not in DIGITS and name != "KEY_SPACE":
            return []
        if name in KEY_OK:
            return [] if repeat else [Event(Kind.SUBMIT)]
        if name in KEY_ESC:
            return [] if repeat else [Event(Kind.CANCEL)]
        if name in KEY_TAB:
            return [] if repeat else [Event(Kind.TAB)]   # screens use it to show/hide
        if name.startswith(CHR):
            return [] if repeat else [Event(Kind.CHAR, name[len(CHR):])]
        if "fn" in active:
            return []   # unused fn combos type nothing
        ch = self._char(name, active)
        return [Event(Kind.CHAR, ch)] if ch else []

    @staticmethod
    def _char(name, active):
        if "sym" in active and name in SYM_LAYER:
            return SYM_LAYER[name]
        if name in LETTERS:
            c = LETTERS[name]
            return c.upper() if "shift" in active else c
        if name in DIGITS:
            return SHIFT_DIGITS[name] if "shift" in active else DIGITS[name]
        return PUNCT.get(name)

    @staticmethod
    def _modifier_of(name):
        for mod, names in MODIFIERS.items():
            if name in names:
                return mod
        return None

    @property
    def indicators(self) -> dict[str, bool]:
        """For the status bar and the Shift/Fn/Sym LEDs."""
        on = self.held | self.armed
        return {m: m in on for m in ("shift", "fn", "sym")}


class TextBuffer:
    """Editable line of text driven by text-mode events."""

    def __init__(self, text: str = "", max_len: int = 100):
        self.text = text
        self.pos = len(text)
        self.max_len = max_len

    def apply(self, ev: Event) -> None:
        if ev.kind is Kind.CHAR and len(self.text) < self.max_len:
            self.text = self.text[:self.pos] + ev.value + self.text[self.pos:]
            self.pos += len(ev.value)
        elif ev.kind is Kind.BACKSPACE and self.pos > 0:
            self.text = self.text[:self.pos - 1] + self.text[self.pos:]
            self.pos -= 1
        elif ev.kind is Kind.CURSOR:
            if ev.value in ("LEFT", "UP"):
                self.pos = max(0, self.pos - 1)
            elif ev.value in ("RIGHT", "DOWN"):
                self.pos = min(len(self.text), self.pos + 1)
            elif ev.value == "START":
                self.pos = 0
            elif ev.value == "END":
                self.pos = len(self.text)

    def wipe(self) -> None:
        """Best effort: Python strings can't be zeroed, so drop references."""
        self.text = ""
        self.pos = 0


# ---------------------------------------------------------------------------
# Device backend
# ---------------------------------------------------------------------------
class KeyboardDevice:
    """Reads the built-in keyboard through evdev and grabs it exclusively,
    so typed passphrases never also reach a console or another program."""

    def __init__(self, path: str | None = None):
        import evdev  # pip install evdev (on the device)
        self._evdev = evdev
        self.dev = evdev.InputDevice(path or os.environ.get("CZ_KBD") or self._find())
        self.dev.grab()
        # The Cardputer Zero's driver sends sym combos as their own codes
        self.symbols = load_driver_symbols() if "tca8418" in self.dev.name.lower() else {}

    def _find(self) -> str:
        ev = self._evdev
        # The Cardputer Zero's own udev rule names its keyboard
        if os.path.exists("/dev/input/cardputer-zero-internal"):
            return os.path.realpath("/dev/input/cardputer-zero-internal")
        for p in ev.list_devices():
            d = ev.InputDevice(p)
            name = d.name.lower()
            caps = d.capabilities().get(ev.ecodes.EV_KEY, [])
            if ("tca8418" in name or "keypad" in name or "cardputer" in name
                    or "keyboard" in name) and ev.ecodes.KEY_A in caps:
                return p
        raise RuntimeError("Built-in keyboard not found; set CZ_KBD=/dev/input/eventN")

    def name_of(self, code: int) -> str:
        if code in self.symbols:
            return CHR + self.symbols[code]
        ecodes = self._evdev.ecodes
        name = ecodes.KEY.get(code) or ecodes.BTN.get(code) or f"KEY_{code}"
        return name[0] if isinstance(name, list) else name   # some codes have aliases

    def poll(self, timeout: float) -> list[tuple[str, int]]:
        """Wait up to `timeout` seconds; return [(key_name, value), ...].
        The timeout lets the UI keep blinking cursors while idle."""
        import select
        ready, _, _ = select.select([self.dev.fd], [], [], timeout)
        if not ready:
            return []
        out = []
        try:
            for e in self.dev.read():
                if e.type == self._evdev.ecodes.EV_KEY:
                    out.append((self.name_of(e.code), e.value))
        except BlockingIOError:
            pass
        return out

    def close(self):
        try:
            self.dev.ungrab()
        except OSError:
            pass
