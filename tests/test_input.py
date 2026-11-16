import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ui.input import Event, Kind, Mode, TextBuffer, Translator  # noqa: E402


def tap(tr, name):
    return tr.feed(name, 1) + tr.feed(name, 0)


def test_menu_arrows_need_no_fn():
    tr = Translator(Mode.MENU)
    assert tap(tr, "KEY_D") == [Event(Kind.NAV, "UP")]
    assert tap(tr, "KEY_F") == [Event(Kind.HOTKEY, "F")]           # F is just a letter now
    assert tap(tr, "KEY_Z") == [Event(Kind.NAV, "LEFT")]
    assert tap(tr, "KEY_X") == [Event(Kind.NAV, "DOWN")]
    assert tap(tr, "KEY_C") == [Event(Kind.NAV, "RIGHT")]


def test_menu_keys():
    tr = Translator(Mode.MENU)
    assert tap(tr, "KEY_ENTER") == [Event(Kind.SELECT)]
    assert tap(tr, "KEY_BACKSPACE") == [Event(Kind.BACK)]
    assert tap(tr, "KEY_ESC") == [Event(Kind.HOME)]
    assert tap(tr, "KEY_S") == [Event(Kind.HOTKEY, "S")]


def test_menu_arrow_autorepeat_but_not_select():
    tr = Translator(Mode.MENU)
    tr.feed("KEY_X", 1)
    assert tr.feed("KEY_X", 2) == [Event(Kind.NAV, "DOWN")]
    tr.feed("KEY_ENTER", 1)
    assert tr.feed("KEY_ENTER", 2) == []


def test_text_mode_types_arrow_letters():
    tr = Translator(Mode.TEXT)
    out = []
    for k in ["KEY_F", "KEY_O", "KEY_X", "KEY_SPACE", "KEY_Z", "KEY_C"]:
        out += tap(tr, k)
    assert "".join(e.value for e in out) == "fox zc"


def test_text_mode_fn_moves_cursor_held():
    tr = Translator(Mode.TEXT)
    tr.feed("KEY_FN", 1)
    assert tap(tr, "KEY_Z") == [Event(Kind.CURSOR, "LEFT")]
    assert tap(tr, "KEY_C") == [Event(Kind.CURSOR, "RIGHT")]
    tr.feed("KEY_FN", 0)
    # fn was used while held, so releasing it must not arm it
    assert tap(tr, "KEY_Z") == [Event(Kind.CHAR, "z")]


def test_tapped_modifier_applies_to_next_key_only():
    tr = Translator(Mode.TEXT)
    tap(tr, "KEY_LEFTSHIFT")
    assert tap(tr, "KEY_A") == [Event(Kind.CHAR, "A")]
    assert tap(tr, "KEY_A") == [Event(Kind.CHAR, "a")]
    tap(tr, "KEY_FN")
    assert tap(tr, "KEY_X") == [Event(Kind.CURSOR, "DOWN")]
    assert tap(tr, "KEY_X") == [Event(Kind.CHAR, "x")]


def test_double_tap_disarms():
    tr = Translator(Mode.TEXT)
    tap(tr, "KEY_LEFTSHIFT")
    tap(tr, "KEY_LEFTSHIFT")
    assert tap(tr, "KEY_A") == [Event(Kind.CHAR, "a")]


def test_sym_and_shift_digits():
    tr = Translator(Mode.TEXT)
    tap(tr, "KEY_RIGHTALT")  # sym
    assert tap(tr, "KEY_K") == [Event(Kind.CHAR, "_")]       # as printed: K_
    tr.feed("KEY_LEFTSHIFT", 1)
    assert tap(tr, "KEY_2") == [Event(Kind.CHAR, "@")]
    tr.feed("KEY_LEFTSHIFT", 0)


def test_driver_level_arrows_accepted_in_both_modes():
    assert tap(Translator(Mode.MENU), "KEY_UP") == [Event(Kind.NAV, "UP")]
    assert tap(Translator(Mode.TEXT), "KEY_LEFT") == [Event(Kind.CURSOR, "LEFT")]


def test_mode_switch_clears_armed_modifier():
    tr = Translator(Mode.MENU)
    tap(tr, "KEY_LEFTSHIFT")
    tr.set_mode(Mode.TEXT)
    assert tap(tr, "KEY_Q") == [Event(Kind.CHAR, "q")]


def test_text_buffer_editing():
    b = TextBuffer()
    for ch in "bitcoin":
        b.apply(Event(Kind.CHAR, ch))
    b.apply(Event(Kind.CURSOR, "START"))
    b.apply(Event(Kind.CHAR, "#"))
    b.apply(Event(Kind.CURSOR, "END"))
    b.apply(Event(Kind.BACKSPACE))
    assert b.text == "#bitcoi" and b.pos == 7
    b.wipe()
    assert b.text == ""


def test_symbols_match_the_keycaps():
    """Read off a Cardputer Zero: Q~ W` E+ R- T/ Y\\ U{ I} O[ P] F| G= H: J; K_ L?
    V< B> N' M" and , gives . ; D Z X C carry the arrows."""
    printed = {"Q": "~", "W": "`", "E": "+", "R": "-", "T": "/", "Y": "\\", "U": "{",
               "I": "}", "O": "[", "P": "]", "F": "|", "G": "=", "H": ":", "J": ";",
               "K": "_", "L": "?", "V": "<", "B": ">", "N": "'", "M": '"', "COMMA": "."}
    for key, sym in printed.items():
        tr = Translator(Mode.TEXT)
        tap(tr, "KEY_RIGHTALT")
        assert tap(tr, f"KEY_{key}") == [Event(Kind.CHAR, sym)], key
    tr = Translator(Mode.TEXT)
    tr.feed("KEY_FN", 1)
    assert tap(tr, "KEY_D") == [Event(Kind.CURSOR, "UP")]
