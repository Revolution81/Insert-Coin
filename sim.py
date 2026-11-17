"""
Insert Coin desktop simulator: the real app in a window on your PC.

    pip install -r requirements.txt pygame
    python3 sim.py                     # signet by default, data in ./simdata

It runs the same code as the device with three stand-ins:
  - the signer runs in-process (same checks, same vault file format), and
    its screens (gold shield, top left) show in the same window
  - "radios" are simulated: Vault/Online only flips a flag. The simulator
    never touches your PC's real network settings.
  - camera: your webcam if OpenCV is installed (pip install opencv-python),
    otherwise QR images (.png/.jpg) you drop in simdata/qr/

Keys (the Cardputer layout on a PC keyboard):
  letters, digits, Enter (OK), Backspace (DEL), Esc, Tab, Space
  Left Alt = fn      Right Alt = sym      Shift = shift
  D/Z/X/C or the arrow keys move in menus
  Ctrl+V   paste text into a typing screen (addresses on a PC are tedious)
  F12      save a screenshot to simdata/
  F2       hold = your finger on the (simulated) fingerprint sensor
  F3       toggle = a tap card on the (simulated) NFC reader

Pretend add-ons:  python3 sim.py --with-chip --with-finger --with-satscard (or --with-tapsigner)

Mainnet is switched off in the simulator: a seed typed into a PC is a hot
seed. Use signet (free test coins from a signet faucet) or testnet4.
"""
from __future__ import annotations

import glob
import os
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

if os.name == "nt" and not sys.flags.utf8_mode and __name__ == "__main__":
    # Windows reads and writes text files as cp1252 by default; the wallet's
    # files are UTF-8 (labels can hold any character). Restart in UTF-8 mode.
    import subprocess
    sys.exit(subprocess.call([sys.executable, "-X", "utf8", os.path.abspath(__file__)] + sys.argv[1:]))
os.environ.setdefault("CZ_DATA", os.path.join(HERE, "simdata"))
os.environ["CZ_SIM"] = "1"
os.environ["CZ_SIGNER_INPROC"] = "1"
os.environ.setdefault("CZ_FORCE_ONLINE", "0")          # boots in Vault mode, like the device
os.environ.setdefault("CZ_SE", "sim" if "--with-chip" in sys.argv else "none")
os.environ.setdefault("CZ_FINGER", "sim" if "--with-finger" in sys.argv else "none")
os.environ.setdefault("CZ_NFC", "sim-tapsigner" if "--with-tapsigner" in sys.argv else
                      "sim-satscard" if "--with-satscard" in sys.argv else "none")
sys.path.insert(0, HERE)

SCALE = 3
W, H = 320, 170
FOOTER = 44


def key_name(key: int, pygame) -> str | None:
    """pygame key -> Linux evdev name, matching what the device's keyboard sends."""
    k = pygame
    special = {
        k.K_RETURN: "KEY_ENTER", k.K_KP_ENTER: "KEY_ENTER", k.K_BACKSPACE: "KEY_BACKSPACE",
        k.K_ESCAPE: "KEY_ESC", k.K_TAB: "KEY_TAB", k.K_SPACE: "KEY_SPACE",
        k.K_LSHIFT: "KEY_LEFTSHIFT", k.K_RSHIFT: "KEY_LEFTSHIFT",
        k.K_LALT: "KEY_FN", k.K_RALT: "KEY_RIGHTALT",
        k.K_LCTRL: "KEY_LEFTCTRL", k.K_RCTRL: "KEY_LEFTCTRL",
        k.K_UP: "KEY_UP", k.K_DOWN: "KEY_DOWN", k.K_LEFT: "KEY_LEFT", k.K_RIGHT: "KEY_RIGHT",
        k.K_HOME: "KEY_HOME", k.K_END: "KEY_END",
        k.K_PAGEUP: "KEY_PAGEUP", k.K_PAGEDOWN: "KEY_PAGEDOWN",
        k.K_COMMA: "KEY_COMMA", k.K_PERIOD: "KEY_DOT", k.K_SLASH: "KEY_SLASH",
        k.K_MINUS: "KEY_MINUS", k.K_EQUALS: "KEY_EQUAL", k.K_SEMICOLON: "KEY_SEMICOLON",
        k.K_QUOTE: "KEY_APOSTROPHE", k.K_LEFTBRACKET: "KEY_LEFTBRACE",
        k.K_RIGHTBRACKET: "KEY_RIGHTBRACE", k.K_BACKSLASH: "KEY_BACKSLASH",
        k.K_BACKQUOTE: "KEY_GRAVE",
    }
    if key in special:
        return special[key]
    if k.K_a <= key <= k.K_z:
        return "KEY_" + chr(key).upper()
    if k.K_0 <= key <= k.K_9:
        return "KEY_" + chr(key)
    if k.K_KP0 <= key <= k.K_KP9:
        return "KEY_" + str(key - k.K_KP0)
    return None


class WebcamCamera:
    def __init__(self):
        import cv2
        self.cv2 = cv2
        self.cap = cv2.VideoCapture(0)
        if not self.cap.isOpened():
            raise RuntimeError("no webcam")

    def grab(self):
        from PIL import Image
        ok, frame = self.cap.read()
        if not ok:
            raise RuntimeError("webcam read failed")
        gray = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2GRAY)
        return Image.fromarray(gray, "L").resize((640, 480))

    def close(self):
        self.cap.release()


def sim_camera():
    from PIL import Image
    from ui.camera import CameraError, FakeCamera
    try:
        return WebcamCamera()
    except Exception:  # noqa: BLE001 - no OpenCV or no webcam
        pass
    folder = os.path.join(os.environ["CZ_DATA"], "qr")
    os.makedirs(folder, exist_ok=True)
    files = sorted(glob.glob(os.path.join(folder, "*.png")) + glob.glob(os.path.join(folder, "*.jpg")))
    if not files:
        raise CameraError(f"No webcam. Put QR images in {folder}")
    return FakeCamera([Image.open(f) for f in files], delay=0.15)


def tor_running(proxy: str) -> bool:
    host, port = proxy.rsplit(":", 1)
    try:
        socket.create_connection((host, int(port)), timeout=0.5).close()
        return True
    except OSError:
        return False


def paste_text(pygame) -> str:
    try:
        pygame.scrap.init()
        raw = pygame.scrap.get(pygame.SCRAP_TEXT) or b""
        return raw.decode("utf-8", "ignore").replace("\x00", "").strip()
    except Exception:  # noqa: BLE001 - clipboard support varies by platform
        return ""


def main():
    import pygame
    from app import App, battery_level  # noqa: F401
    from ui.input import Event, Kind, Mode

    pygame.init()
    pygame.display.set_caption("Insert Coin simulator")
    win = pygame.display.set_mode((W * SCALE, H * SCALE + FOOTER))
    font = pygame.font.SysFont("monospace", 15)
    pygame.key.set_repeat(400, 45)

    app = App()
    app.camera_factory = sim_camera
    first_run = not os.path.exists(os.path.join(os.environ["CZ_DATA"], "app", "settings.json"))
    if first_run or (app.settings.network == "bitcoin"
                     and not os.environ.get("CZ_SIM_ALLOW_MAINNET")):
        app.settings.network = "signet"      # a PC is a hot environment: test coins only
    if app.settings.use_tor and not tor_running(app.settings.tor_proxy):
        print("Tor isn't running on this PC: connecting to the Electrum server directly.")
        app.settings.use_tor = False
    app.settings.save()
    try:                                     # sound through the PC's speakers
        import io
        pygame.mixer.init(frequency=22050, size=-16, channels=1)
        from ui import sound
        sounds = {}

        def play(name, data):
            if name not in sounds:
                sounds[name] = pygame.mixer.Sound(file=io.BytesIO(data))
            sounds[name].play()
        sound.set_player(play)
    except Exception:  # noqa: BLE001 - no audio device: play silently
        pass
    app.start(title=True)

    held: set[str] = set()
    ctrl = False
    frame, last_blink = 0, time.monotonic()
    help1 = "Enter=OK  Backspace=DEL  Esc  Tab  |  FZXC or arrows move  |  Left Alt=fn  Right Alt=sym"
    help2 = "Ctrl+V paste | F2 finger | F3 card | F12 screenshot | Vault/Online simulated: PC network untouched"
    shots = 0
    while True:
        changed = False
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit()
                return
            if ev.type in (pygame.KEYDOWN, pygame.KEYUP):
                if ev.key in (pygame.K_LCTRL, pygame.K_RCTRL):
                    ctrl = ev.type == pygame.KEYDOWN
                if ev.key == pygame.K_F2:
                    from core.hw.finger import simulator
                    if simulator():
                        simulator().finger = "me" if ev.type == pygame.KEYDOWN else None
                    continue
                if ev.key == pygame.K_F3:
                    from core.hw.nfc import sim_reader
                    if ev.type == pygame.KEYDOWN and sim_reader():
                        sim_reader().present = not sim_reader().present
                        print("card", "on" if sim_reader().present else "off", "the reader")
                    continue
                if ev.type == pygame.KEYDOWN and ev.key == pygame.K_F12:
                    shots += 1
                    path = os.path.join(os.environ["CZ_DATA"], f"screenshot-{shots}.png")
                    app.render(frame).resize((W * SCALE, H * SCALE), 0).save(path)
                    print("saved", path)
                    continue
                if ev.type == pygame.KEYDOWN and ctrl and ev.key == pygame.K_v:
                    if app.visible.mode is Mode.TEXT:
                        for ch in paste_text(pygame):
                            app.safely(app.handle_event, Event(Kind.CHAR, ch))
                    changed = True
                    continue
                name = key_name(ev.key, pygame)
                if not name:
                    continue
                if ev.type == pygame.KEYDOWN:
                    app.safely(app.feed, name, 2 if name in held else 1)
                    held.add(name)
                else:
                    held.discard(name)
                    app.safely(app.feed, name, 0)
                changed = True
        changed |= bool(app.safely(app.pump))
        app.safely(app.check_idle)
        now = time.monotonic()
        if now - last_blink >= 0.5:
            frame ^= 1
            last_blink = now
            changed = True
        if app.animated:
            changed = True
        if changed:
            img = app.safely(app.render, frame) or app.render(frame)
            img = img.resize((W * SCALE, H * SCALE), 0)
            surf = pygame.image.fromstring(img.tobytes(), img.size, "RGB")
            win.fill((18, 18, 26))
            win.blit(surf, (0, 0))
            win.blit(font.render(help1, True, (170, 170, 200)), (8, H * SCALE + 4))
            win.blit(font.render(help2, True, (120, 120, 150)), (8, H * SCALE + 23))
            pygame.display.flip()
        time.sleep(0.03)


if __name__ == "__main__":
    main()
