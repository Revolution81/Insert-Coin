"""
Run this on the Cardputer Zero first: press every key (and fn/sym/shift
combos) and note what the kernel reports. If anything differs from the
names in ui/input.py, set the matching CZ_KEY_* variable or edit the table.

    sudo python3 tools/keytest.py            # auto-detect the keyboard
    sudo python3 tools/keytest.py /dev/input/event1

Things to check:
  - Which name does the sym key send? (ui/input.py guesses KEY_RIGHTALT)
  - Does fn send its own code (KEY_FN), or does the driver hide it and turn
    fn+F into KEY_UP itself? Both are handled, but it's good to know.
  - Ctrl+C will not quit while the keyboard is grabbed: press ESC 3 times.
"""
import sys

sys.path.insert(0, __file__.rsplit("/tools/", 1)[0])

import evdev  # noqa: E402

from ui.input import KeyboardDevice, Mode, Translator  # noqa: E402


def main():
    if len(sys.argv) == 1:
        print("Input devices:")
        for p in evdev.list_devices():
            print(f"  {p}  {evdev.InputDevice(p).name}")
    kb = KeyboardDevice(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"\nReading {kb.dev.path} ({kb.dev.name}). ESC x3 to quit.\n", flush=True)
    menu, text = Translator(Mode.MENU), Translator(Mode.TEXT)
    escapes = 0
    try:
        while escapes < 3:
            for name, value in kb.poll(1.0):
                state = {0: "up", 1: "down", 2: "repeat"}[value]
                m, t = menu.feed(name, value), text.feed(name, value)
                if value == 1:
                    print(f"{name:<18} {state:<6}  menu: {m}   text: {t}", flush=True)
                    escapes = escapes + 1 if name == "KEY_ESC" else 0
    finally:
        kb.close()


if __name__ == "__main__":
    main()
