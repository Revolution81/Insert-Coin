"""
Example screens drawn with the 16-bit theme. Each function returns a 320x170
image; pass frame=0/1 to animate cursors and the dialog arrow.

Run `python3 -m ui.screens` from the project root to write preview PNGs.
"""
from __future__ import annotations

from . import theme as t
from .theme import BODY, BIG, TINY, PAL, W, H


def home(sats=4_218_337, mode="VAULT", selected=0, frame=0):
    img, d = t.new_canvas()
    t.sky(img, d)
    t.status_bar(img, d, mode=mode, online=(mode == "ONLINE"))

    t.coin(img, (14, 22), scale=2)
    btc = t.fmt_btc(sats)
    t.text(d, (56, 24), btc[:7], BIG, "gold")
    t.text(d, (56 + BIG.width(btc[:7]) + 2, 32), btc[7:], BODY, "gold_dk")
    t.text(d, (56, 44), f"{t.fmt_sats(sats)} sats", BODY, "light")
    t.text(d, (56, 58), "NATIVE SEGWIT  ACCT 0", TINY, "grey")

    t.menu(img, d, (6, 76, W - 7, 122),
           ["SEND", "RECEIVE", "COINS", "HISTORY", "MODE", "SETUP"],
           selected=selected, cols=3, frame=frame)
    t.text(d, (W // 2, 132), "ARROWS MOVE   ENTER PICK", TINY, "grey",
           align="center")
    return img


def choose_mode(selected=1, frame=0):
    img, d = t.new_canvas()
    t.sky(img, d, top=0, hills=False)
    t.text(d, (W // 2, 8), "CHOOSE MODE", BIG, "white", align="center")

    boxes = [(8, 32, 155, 116), (164, 32, W - 9, 116)]
    info = [
        ("VAULT", t.LOCK, {"k": PAL["purple_dk"], "w": PAL["white"], "y": PAL["purple"]},
         ["Radios off", "QR + SD signing", "Cold storage"]),
        ("ONLINE", t.WIFI, {"w": PAL["cyan"]},
         ["Wi-Fi + Tor", "Send direct", "Hot wallet"]),
    ]
    for n, (box, (name, icon, colors, lines)) in enumerate(zip(boxes, info)):
        sel = n == selected
        t.window(d, box, border=("gold" if sel else "light"))
        x0, y0, x1, _ = box
        t.sprite(img, icon, (x0 + 12, y0 + 10), colors, scale=2)
        t.text(d, (x0 + 34, y0 + 14), name, BODY, "white" if sel else "grey")
        for i, ln in enumerate(lines):
            t.text(d, (x0 + 12, y0 + 36 + i * 12), ln, BODY,
                   "light" if sel else "dim")
        if sel:
            t.cursor(img, (x0 - 1 + (frame % 2), y0 + 14))

    t.dialog(img, d, ["Online keeps keys on a",
                      "networked device. Use a cap."],
             box=(4, 124, W - 5, H - 4), frame=frame, more=True)
    return img


def pin_entry(entered=3, length=6, tries_left=4, frame=0):
    img, d = t.new_canvas()
    t.sky(img, d, top=0)
    box = (50, 26, W - 51, 128)
    t.window(d, box, title="UNLOCK")
    t.text(d, (W // 2, 44), "ENTER PIN", BIG, "white", align="center")
    gx = W // 2 - (length * 20 - 4) // 2
    t.gems(img, (gx, 66), length, entered)
    t.text(d, (70, 98), "TRIES", BODY, "grey")
    t.hearts(img, (116, 97), 5, tries_left)
    t.text(d, (W // 2, 116), "ALL GONE = WIPE", TINY, "red", align="center")
    t.text(d, (W // 2, 146), "[ENTER] OK    [DEL] BACK", TINY, "grey",
           align="center")
    return img


def send_confirm(frame=0, selected=1):
    img, d = t.new_canvas()
    t.sky(img, d, hills=False)
    t.status_bar(img, d, mode="ONLINE", online=True)
    t.window(d, (4, 20, W - 5, 108), title="CONFIRM SEND", border="gold")
    rows = [
        ("TO", "bc1qxy2k...x0wlh", "light"),
        ("AMOUNT", "0.01000000 BTC", "gold"),
        ("FEE", "3 sat/vB  423 sats", "light"),
        ("TOTAL", "0.01000423 BTC", "gold"),
    ]
    for i, (k, v, c) in enumerate(rows):
        y = 34 + i * 17
        t.text(d, (16, y), k, BODY, "grey")
        t.text(d, (96, y), v, BODY, c)

    t.dialog(img, d, ["Sign and broadcast?"], box=(4, 116, 214, H - 4))
    # Cursor starts on NO so a stray keypress never sends.
    t.menu(img, d, (220, 116, W - 5, H - 4), ["YES", "NO"],
           selected=selected, frame=frame, row_h=16)
    return img


def receive(address="bc1qexamplepreviewonlynotarealaddress0000", index=7):
    # A deliberately invalid placeholder: never put a real address in code,
    # even for a preview. Someone controls every real one.
    img, d = t.new_canvas()
    t.sky(img, d, hills=False)
    t.status_bar(img, d, mode="VAULT")
    size = t.qr(img, address.upper(), (8, 18), module=3, border=3)
    t.window(d, (size + 16, 22, W - 5, H - 8), title=f"ADDR #{index}")
    groups = [address[i:i + 4] for i in range(0, len(address), 4)]
    x = size + 28
    for line in range(4):
        chunk = groups[line * 3:line * 3 + 3]
        for g_i, g in enumerate(chunk):
            color = "gold" if (line * 3 + g_i) % 2 == 0 else "light"
            t.text(d, (x + g_i * 40, 38 + line * 14), g, BODY, color)
    t.text(d, (x, 100), "Check the first and", TINY, "grey")
    t.text(d, (x, 110), "last groups match.", TINY, "grey")
    t.text(d, (x, 130), "[N] NEW  [L] LABEL", TINY, "light")
    return img


def syncing(tor=1.0, blocks=0.76, height=925_114, frame=0):
    img, d = t.new_canvas()
    t.sky(img, d)
    t.status_bar(img, d, mode="ONLINE", online=True)
    t.window(d, (20, 28, W - 21, 118), title="CONNECTING")
    stats = [("TOR", tor, "cyan"), ("SYNC", blocks, "green")]
    for i, (label, frac, color) in enumerate(stats):
        y = 44 + i * 20
        t.text(d, (34, y), label, BODY, "white")
        t.bar(d, (82, y - 1, 240, y + 8), frac, color)
        t.text(d, (284, y), f"{int(frac * 100)}%", BODY, "light", align="right")
    t.text(d, (34, 90), "HEIGHT", BODY, "grey")
    t.text(d, (284, 90), f"{height:,}", BODY, "gold", align="right")
    t.dialog(img, d, ["Fetching your coins..."], box=(4, 128, W - 5, H - 4),
             frame=frame, more=True)
    return img


def passphrase(value="satoshi nakamoto", pos=7, indicators=None, frame=0,
               masked=False):
    """Text-mode screen: the status bar shows the ABC badge."""
    img, d = t.new_canvas()
    t.sky(img, d, hills=False)
    t.status_bar(img, d, mode="VAULT",
                 typing=indicators or {"shift": False, "fn": True, "sym": False})
    t.window(d, (6, 22, W - 7, 112), title="PASSPHRASE")
    t.text(d, (18, 36), "Optional 25th word.", BODY, "light")
    t.text_field(d, (16, 52, W - 17, 70), value, pos, frame, masked=masked,
                 peek=True)
    t.text(d, (18, 80), f"{len(value)} CHARS  CASE + SPACES COUNT", TINY, "grey")
    t.text(d, (18, 94), "[TAB] " + ("SHOW" if masked else "HIDE"), TINY, "light")
    hints = [("FN+FZXC", "MOVE"), ("OK", "DONE"), ("HOME", "CANCEL")]
    x = 10
    for key, label in hints:
        x = t.text(d, (x, 124), key, TINY, "gold") + 4
        x = t.text(d, (x, 124), label, TINY, "grey") + 14
    t.dialog(img, d, ["Wrong passphrase = a different", "(empty) wallet. No error!"],
             box=(4, 134, W - 5, H - 3))
    return img


SCREENS = {
    "home": home,
    "choose_mode": choose_mode,
    "pin_entry": pin_entry,
    "send_confirm": send_confirm,
    "receive": receive,
    "syncing": syncing,
    "passphrase": passphrase,
}


def contact_sheet(scale=2, pad=12):
    """All screens side by side, scaled up with nearest-neighbour."""
    from PIL import Image, ImageDraw
    names = list(SCREENS)
    cols = 2
    rows = (len(names) + cols - 1) // cols
    cw, ch = W * scale, H * scale
    label_h = 18
    sheet = Image.new("RGB", (cols * cw + (cols + 1) * pad,
                              rows * (ch + label_h) + (rows + 1) * pad), (20, 20, 28))
    d = ImageDraw.Draw(sheet)
    d.fontmode = "1"
    for i, name in enumerate(names):
        x = pad + (i % cols) * (cw + pad)
        y = pad + (i // cols) * (ch + label_h + pad)
        d.text((x, y + 2), name.upper().replace("_", " "), font=BODY.face,
               fill=(200, 200, 220))
        sheet.paste(SCREENS[name]().resize((cw, ch), Image.NEAREST), (x, y + label_h))
    return sheet


if __name__ == "__main__":
    import os
    out = os.path.join(os.path.dirname(__file__), "..", "preview")
    os.makedirs(out, exist_ok=True)
    for name, fn in SCREENS.items():
        fn().resize((W * 3, H * 3), resample=0).save(os.path.join(out, f"{name}.png"))
    contact_sheet().save(os.path.join(out, "all_screens.png"))
    print("wrote", os.path.abspath(out))
