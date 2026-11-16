"""
Screenshots of the real app for the instruction booklet: drives a fresh
signet wallet (simulated chip and card) through each screen and saves
PNGs at 2x, crisp pixels.

    python3 tools/shots.py [out_dir]
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import monkey  # noqa: E402,F401  (simulator environment)
from monkey import settle  # noqa: E402

from PIL import Image  # noqa: E402

PIN = "1234"
OUT = sys.argv[1] if len(sys.argv) > 1 else "shots"


def save(app_or_img, name, frame=0):
    img = app_or_img if isinstance(app_or_img, Image.Image) else app_or_img.render(frame)
    img.resize((img.width * 2, img.height * 2), Image.NEAREST).save(os.path.join(OUT, name + ".png"))
    print("saved", name)


def make_app():
    import core.config
    from app import App
    from core.signer_service import SignerClient
    import test_app
    test_app.settle = settle
    tmp = tempfile.mkdtemp(prefix="czshots-")
    core.config.APP_DIR = os.path.join(tmp, "app")
    a = App(signer=SignerClient(inproc=True, data_dir=os.path.join(tmp, "signer")),
            sync_jobs=True)
    a.settings.network = "signet"
    a.outbox_dir = os.path.join(tmp, "outbox")
    return a


def main():
    os.makedirs(OUT, exist_ok=True)
    from test_app import create_wallet, enter, press, type_text
    from test_core import DEST, fund
    from ui import title

    # ---- starting out
    save(title.draw(0, now=0.0), "01-title")
    app = make_app()
    app.start()
    save(app, "02-welcome")
    enter(app)                                   # CREATE NEW WALLET -> the signer's menu
    save(app, "03-new-wallet")

    # ---- a wallet with coins
    app = make_app()
    app.start()
    create_wallet(app, PIN)
    for amt in (1_500_000, 500_000, 150_000):
        fund(app.wallet, amt)
    app.refresh_balance()
    save(app, "04-home")

    press(app, "KEY_R")                          # receive
    save(app, "05-receive")
    press(app, "KEY_V")                          # verify on the signer
    save(app, "06-verify")
    press(app, "KEY_ESC")
    settle(app)
    app.home()

    app.visible.sel = 2                          # COINS
    enter(app)
    enter(app)                                   # pick the biggest coin
    save(app, "07-coins")
    app.home()

    # send -> the signer's confirm screen
    press(app, "KEY_S")
    type_text(app, DEST)
    enter(app)
    type_text(app, "250000")
    enter(app)
    enter(app)                                   # NORMAL fee
    save(app, "08-confirm")
    press(app, "KEY_ESC")
    settle(app)
    app.home()

    from ui.flow import history_screen, settings_screen
    history_screen(app)
    settle(app)
    save(app, "09-history")
    app.home()

    settings_screen(app)
    settle(app)
    save(app, "10-setup")
    app.home()

    press(app, "KEY_W")
    save(app, "11-wallets")
    app.home()

    from ui.flow import ModeScreen
    app.push(ModeScreen())
    settle(app)
    save(app, "12-mode")
    app.home()

    from ui.hardware_flow import hardware_menu
    hardware_menu(app)
    settle(app)
    save(app, "13-hardware")
    app.home()

    # tap a SATSCARD
    from core import cktap
    from core.hw import nfc
    card = cktap.SimSatscard()
    reader = nfc.SimReader(card)
    app.nfc = reader
    app.hw["nfc"] = {"state": "ready", "kind": "simulated", "subghz": {"state": "asleep"}}
    app.wallet.foreign_utxos = lambda script: [{"txid": "ab" * 32, "vout": 0,
                                                "value": 50_000, "height": 800}]
    press(app, "KEY_T")
    save(app, "14-tap")
    reader.present = True
    settle(app)
    save(app, "15-satscard")
    app.home()

    # locked: a wrong PIN first, so a heart is gone
    press(app, "KEY_L")
    settle(app)
    save(app, "16-locked")
    enter(app)
    for c in "9999":
        press(app, f"KEY_{c}")
    enter(app)
    settle(app)
    if app.console is None or not app.console.stack:
        enter(app)
    for c in "12":
        press(app, f"KEY_{c}")
    save(app, "17-pin")


if __name__ == "__main__":
    main()
