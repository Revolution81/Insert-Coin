"""
Paper wallets, like COLDCARD's (Advanced/Tools > Paper Wallets): a brand-new
private key that has nothing to do with your seed, saved as

  <address>.txt   the deposit address, the private key (WIF and hex), a
                  Bitcoin Core import command and both as QR codes drawn
                  with text characters (needs UTF-8 and a white background)
  <address>.pdf   a printable page with the same, ready to fold (optional)

Made inside the signer: the key never passes through the app. Randomness is
the kernel's generator plus the secure chip's (if one is connected), hashed
together, or, with dice, SHA-256 of 99+ rolls exactly as typed so anyone can
check it with another tool.

For small amounts only: anyone who sees the paper can take the coins, and
paper burns, fades and gets thrown away.
"""
from __future__ import annotations

import hashlib
import io
import os
import time

from embit import ec, script
from embit.descriptor.checksum import add_checksum
from embit.networks import NETWORKS as EMBIT

from . import config

KINDS = {"segwit": "SEGWIT", "classic": "CLASSIC"}
DICE_MIN = 99                       # 99 rolls of a fair die = 256 bits
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141   # secp256k1 order


class PaperError(ValueError):
    pass


# ------------------------------------------------------------ the key
def random_key(chip: bytes = b"") -> bytes:
    """32 bytes: the kernel's generator and the chip's, each under a label."""
    while True:
        h = hashlib.sha256(b"insertcoin-paper-v1")
        h.update(os.urandom(32))
        h.update(b"chip" + hashlib.sha256(chip).digest())
        k = h.digest()
        if 0 < int.from_bytes(k, "big") < N:
            return k


def dice_key(rolls: str) -> bytes:
    """SHA-256 of the rolls as typed (ASCII 1-6). Check elsewhere with:
    echo -n 1234... | sha256sum"""
    if any(c not in "123456" for c in rolls):
        raise PaperError("dice rolls are 1 to 6")
    if len(rolls) < DICE_MIN:
        raise PaperError(f"need at least {DICE_MIN} rolls")
    k = hashlib.sha256(rolls.encode()).digest()
    if not 0 < int.from_bytes(k, "big") < N:
        raise PaperError("unlucky rolls: add one more")
    return k


# ------------------------------------------------------------ the wallet
def build(secret: bytes, network: str, kind: str = "segwit") -> dict:
    if kind not in KINDS:
        raise PaperError("unknown address type")
    net = EMBIT[config.NETWORKS[network][1]]
    pk = ec.PrivateKey(secret)
    pub = pk.get_public_key()
    wif = pk.wif(net)
    if kind == "segwit":
        address = script.p2wpkh(pub).address(net)
        desc = add_checksum(f"wpkh({wif})")
    else:
        address = script.p2pkh(pub).address(net)
        desc = add_checksum(f"pkh({wif})")
    return {"address": address, "wif": wif, "hex": secret.hex(), "descriptor": desc,
            "kind": kind, "network": network, "created": int(time.time())}


def qr_block(data: str) -> str:
    """A QR code drawn with Unicode half blocks: dark modules print dark on
    a white background, two module rows per text line."""
    import qrcode
    q = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(data)
    q.make(fit=True)
    m = q.get_matrix()
    if len(m) % 2:
        m.append([False] * len(m[0]))
    lines = []
    for y in range(0, len(m), 2):
        top, bot = m[y], m[y + 1]
        lines.append("".join({(True, True): "█", (True, False): "▀",
                              (False, True): "▄", (False, False): " "}[(a, b)]
                             for a, b in zip(top, bot)))
    return "\n".join(lines)


def text_file(w: dict) -> str:
    label = config.NETWORKS[w["network"]][0]
    test = "" if w["network"] == "bitcoin" else f"  ({label}: TEST COINS ONLY)"
    created = time.strftime("%Y-%m-%d %H:%M", time.localtime(w["created"]))
    return f"""Insert Coin Generated Paper Wallet{test}

Made {created}. This key has nothing to do with your seed words:
lose this paper and these coins are gone. For SMALL AMOUNTS only.
Anyone who sees the private key can take the coins.

Deposit address ({KINDS[w['kind']]}):

  {w['address']}

Private key (WIF=Wallet Import Format):

  {w['wif']}

Private key (Hex, 32 bytes):

  {w['hex']}

To spend: sweep the private key into a wallet you use (in Sparrow:
Tools > Sweep Private Key), or import it into Bitcoin Core:

  bitcoin-cli importdescriptors '[{{"desc": "{w['descriptor']}", "timestamp": {w['created']}}}]'

QR Codes (requires UTF-8, Unicode and a white background):

{_indent(qr_block(w['address']))}

  {w['address']}

{_indent(qr_block(w['wif']))}

  {w['wif']}
"""


def _indent(block: str) -> str:
    return "\n".join("  " + line for line in block.splitlines())


# ------------------------------------------------------------ printable page
def pdf(w: dict) -> bytes:
    """One US Letter page at 150 dpi, as a PDF."""
    out = io.BytesIO()
    page(w).save(out, "PDF", resolution=150)
    return out.getvalue()


def jpeg(w: dict) -> bytes:
    """The same page as a 300 dpi JPEG (every IPP Everywhere printer takes
    JPEG). Pixels doubled, not smoothed, so the QR edges stay sharp."""
    from PIL import Image
    img = page(w)
    img = img.resize((img.width * 2, img.height * 2), Image.NEAREST)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=95, dpi=(300, 300), subsampling=0)
    return out.getvalue()


def from_text(text: str) -> dict:
    """Rebuild a wallet from its .txt file (the print service reads these:
    the key is re-derived and must match the address on the file)."""
    import re
    hexkey = re.search(r"Private key \(Hex, 32 bytes\):\s+([0-9a-f]{64})", text)
    kind = re.search(r"Deposit address \((SEGWIT|CLASSIC)\):\s+(\S+)", text)
    when = re.search(r'"timestamp": (\d+)', text)
    if not (hexkey and kind):
        raise PaperError("not an Insert Coin paper wallet file")
    k = {v: key for key, v in KINDS.items()}[kind.group(1)]
    first = text.split("\n", 1)[0]
    # testnet and signet share address formats: the file's title names the one
    named = [n for n in config.NETWORKS if f"({config.NETWORKS[n][0]}: TEST" in first]
    for network in named + [n for n in config.NETWORKS if n not in named]:
        w = build(bytes.fromhex(hexkey.group(1)), network, k)
        if w["address"] == kind.group(2):
            if when:
                w["created"] = int(when.group(1))
            return w
    raise PaperError("the key on this file doesn't match its address")


def page(w: dict):
    """One US Letter page at 150 dpi: deposit half on top, the private key
    half below a fold line. Drawn with the wallet's own pixel fonts."""
    from PIL import Image, ImageDraw, ImageFont
    import qrcode

    fonts = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "ui", "fonts")

    def font(name, size):
        return ImageFont.truetype(os.path.join(fonts, name), size)

    W, H = 1275, 1650
    ink, red, gold, grey = (17, 17, 17), (184, 40, 48), (168, 116, 0), (110, 110, 120)
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    big, mid, small = font("PressStart2P.ttf", 34), font("PressStart2P.ttf", 18), \
        font("PressStart2P.ttf", 13)
    label = config.NETWORKS[w["network"]][0]

    def qr(data, size):
        q = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
        q.add_data(data)
        q.make(fit=True)
        return q.make_image(fill_color="black", back_color="white").get_image() \
            .convert("RGB").resize((size, size), Image.NEAREST)

    def split(s, n):
        return [s[i:i + n] for i in range(0, len(s), n)]

    # --- top half: share this
    d.rectangle((60, 60, W - 60, 140), fill=ink)
    d.text((90, 84), "INSERT COIN PAPER WALLET", font=big, fill=gold)
    if w["network"] != "bitcoin":
        d.text((W - 90, 92), f"{label}", font=mid, fill="white", anchor="ra")
    d.text((90, 180), "DEPOSIT ADDRESS", font=mid, fill=ink)
    d.text((90, 210), "Share this to receive coins.", font=small, fill=grey)
    img.paste(qr(w["address"], 400), (90, 250))
    y = 270
    for part in split(w["address"], 22):
        d.text((540, y), part, font=mid, fill=ink)
        y += 34
    d.text((540, y + 20), KINDS[w["kind"]], font=small, fill=grey)
    d.text((540, y + 46), time.strftime("Made %Y-%m-%d", time.localtime(w["created"])),
           font=small, fill=grey)

    # --- fold line
    yf = 780
    for x in range(60, W - 60, 24):
        d.line((x, yf, x + 12, yf), fill=grey, width=2)
    d.text((W // 2, yf + 14), "FOLD HERE: KEEP THE SECRET HALF HIDDEN", font=small,
           fill=grey, anchor="ma")

    # --- bottom half: keep this secret
    d.rectangle((60, 850, W - 60, 900), fill=red)
    d.text((90, 866), "PRIVATE KEY: SECRET! ANYONE WHO SEES IT CAN SPEND", font=small,
           fill="white")
    img.paste(qr(w["wif"], 400), (90, 940))
    y = 960
    d.text((540, y), "WIF", font=mid, fill=ink)
    y += 36
    for part in split(w["wif"], 22):
        d.text((540, y), part, font=mid, fill=ink)
        y += 34
    y += 20
    for line in ("To spend, sweep this key into", "a wallet you use, e.g. Sparrow:",
                 "Tools > Sweep Private Key.", "", "Not backed up by your seed words.",
                 "Small amounts only."):
        d.text((540, y), line, font=small, fill=grey)
        y += 24
    d.text((90, H - 90), "HEX " + w["hex"], font=font("PressStart2P.ttf", 11), fill=grey)
    return img


def save(directory: str, w: dict, with_pdf: bool = True) -> list[str]:
    """Write the files (owner-only); returns their names."""
    os.makedirs(directory, mode=0o700, exist_ok=True)
    files = {f"{w['address']}.txt": text_file(w).encode("utf-8")}
    if with_pdf:
        files[f"{w['address']}.pdf"] = pdf(w)
    for name, data in files.items():
        tmp = os.path.join(directory, name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0),
                     0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, os.path.join(directory, name))
    return sorted(files)
