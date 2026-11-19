"""Draws secure-chip-assembly.png from a front render of the board (asm.png,
1440 px wide = 60 px/mm): pin names on the header pads, rings round the
parts, and a parts key."""
import sys
import textwrap

from PIL import Image, ImageDraw, ImageFont

F = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FB = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
f = lambda s, b=False: ImageFont.truetype(FB if b else F, s)
board = Image.open(sys.argv[1]).convert("RGBA")
S, BX, BY = 60.0, 70, 190
W, H = BX + board.width + 70 + 880, BY + board.height + 170
im = Image.new("RGBA", (W, H), "white")
im.paste(board, (BX, BY))
ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
od = ImageDraw.Draw(ov)
mm = lambda x, y: (BX + x * S, BY + y * S)


def tag(x, y, txt, bg, size=26):
    px, py = mm(x, y)
    w = od.textlength(txt, font=f(size, True))
    h = size + 12
    od.rounded_rectangle([px - w / 2 - 10, py - h / 2, px + w / 2 + 10, py + h / 2], radius=8,
                         fill=bg, outline="white", width=2)
    od.text((px - w / 2, py - h / 2 + 4), txt, font=f(size, True), fill="white")


cols = [6.61 + 2.54 * i for i in range(7)]
grey = (110, 110, 110, 210)
bottom = [("5V IN", grey), ("GND", (20, 20, 20, 240)), ("5V OUT", (200, 30, 30, 240)),
          ("SDA", (30, 90, 200, 240)), ("SCL", (230, 150, 0, 240)), ("–", grey), ("–", grey)]
for x, (t, bg) in zip(cols, bottom):
    tag(x, 5.14, t, bg, size=24 if len(t) > 3 else 26)
for i, x in enumerate(cols):
    tag(x, 2.6, "pin 1" if i == 0 else "–", grey, size=22 if i == 0 else 26)
for x, y0, r in [(12.325, 11.0, 3.0), (5.0, 12.0, 1.6), (6.6, 8.775, 1.0), (2.0, 14.9, 1.0),
                 (8.3, 9.275, 1.0), (16.6, 7.3, 1.0), (16.6, 9.0, 1.0)]:
    cx, cy = mm(x, y0)
    R = r * S
    od.ellipse([cx - R, cy - R, cx + R, cy + R], outline=(30, 144, 255, 255), width=4)
im = Image.alpha_composite(im, ov).convert("RGB")
d = ImageDraw.Draw(im)
d.text((70, 28), "Insert Coin secure chip v1: assembly (front, actual size 24 x 16 mm)", font=f(40, True), fill="black")
d.text((70, 86), "Small parts go on this side. The 2x7 header goes on the BACK (long pins toward the Cardputer); solder its pins on this side.", font=f(25), fill="#333")
d.text((70, 124), "Header pads are labelled with the Cardputer pin each one meets. Only GND, 5V OUT, SDA and SCL are wired; – = not used.", font=f(25), fill="#333")
LX, y = BX + board.width + 70, BY
rows = [("J1", "2x7 header, 2.54 mm, male", "BACK side, plastic body against the board; the square pad is pin 1"),
        ("U2", "ATECC608B-SSHDA, SOIC-8", "the secure chip; pin 1 is the corner by the triangle (bottom left)"),
        ("U1", "MCP1700T-3302E/TT, SOT-23", "3.3 V regulator; fits one way only (2 legs left, 1 right)"),
        ("C1", "1 µF, 0603", "regulator input; either way round"),
        ("C2", "1 µF, 0603", "regulator output; either way round"),
        ("C3", "100 nF, 0603", "by the chip's power pin; either way round"),
        ("R1", "4.7 kΩ, 0603", "SDA pull-up; either way round"),
        ("R2", "4.7 kΩ, 0603", "SCL pull-up; either way round")]
for ref, part, note in rows:
    d.text((LX, y), ref, font=f(34, True), fill="#b8860b")
    d.text((LX + 80, y + 2), part, font=f(28, True), fill="black")
    y += 40
    for line in textwrap.wrap(note, 52):
        d.text((LX + 80, y), line, font=f(24), fill="#444")
        y += 30
    y += 18
d.text((LX, y + 10), "Solder order: U2, U1, the small parts, J1 last.", font=f(24, True), fill="black")
d.text((LX, y + 44), "Power the Cardputer off before plugging it in.", font=f(24, True), fill="#c0392b")
d.text((70, BY + board.height + 30), "Blue circles: the seven small parts; their labels (C1, U2 ...) are printed on the board beside them.", font=f(25), fill="#333")
d.text((70, BY + board.height + 68), "Chip address 0x60. 5V OUT is switched on by the Cardputer (HAT_EN); check it with a meter first.", font=f(25), fill="#333")
im.save("secure-chip-assembly.png")
