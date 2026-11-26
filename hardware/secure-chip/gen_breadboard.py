W, H = 1500, 1030
P = 30
BX, BY = 560, 430            # breadboard top-left
NC = 22
def cx(c): return BX + 45 + (c - 1) * P          # columns 1-based
Y_PLUS = BY + 35                                  # +3.3 V rail holes
ROWS = {r: BY + 95 + i * P for i, r in enumerate("abcde")}
Y_GND = ROWS["e"] + 60                            # GND rail holes
BW, BH = 90 + (NC - 1) * P, (Y_GND - BY) + 60
RED, BLK, BLU, YEL, BRN = "#d62828", "#111111", "#1d4ed8", "#e0a800", "#7a5c2e"
o = []
def add(x): o.append(x)
def text(x, y, t, size=15, col="#111", anchor="start", w="400"):
    add(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{w}" fill="{col}" text-anchor="{anchor}">{t}</text>')
def hole(x, y): add(f'<rect x="{x-5}" y="{y-5}" width="10" height="10" rx="2" fill="#5b6470"/>')
def pin(x, y, col): add(f'<circle cx="{x}" cy="{y}" r="8" fill="{col}" stroke="#000" stroke-width="1.5"/>')
def path(d, col, w=6): add(f'<path d="{d}" stroke="{col}" stroke-width="{w}" fill="none" stroke-linecap="round" stroke-linejoin="round"/>')

add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="Helvetica,Arial,sans-serif">')
add(f'<rect width="{W}" height="{H}" fill="#fff"/>')
text(40, 55, "Insert Coin: blank secure chip, breadboard test", 32, w="700")
text(40, 90, "Cardputer OFF and NFC Cap removed while wiring. Before fitting the chip: pin 6 ≈ 5 V, red rail ≈ 3.3 V (meter).", 18, "#444")

# breadboard
add(f'<rect x="{BX}" y="{BY}" width="{BW}" height="{BH}" rx="12" fill="#f5f2e9" stroke="#cbc4ae" stroke-width="2"/>')
path(f"M{BX+20},{Y_PLUS-20} H{BX+BW-20}", RED, 3)
path(f"M{BX+20},{Y_GND+20} H{BX+BW-20}", BLU, 3)
for c in range(1, NC + 1):
    hole(cx(c), Y_PLUS); hole(cx(c), Y_GND)
    for r in "abcde": hole(cx(c), ROWS[r])
for c in (1, 3, 4, 5, 10, 14, 20):
    text(cx(c), Y_GND + 46, str(c), 12, "#888", "middle")
for r in "abcde":
    text(BX + 18, ROWS[r] + 5, r, 12, "#999", "middle")
text(BX + BW + 14, Y_PLUS + 6, "+3.3 V rail (red)", 18, RED, w="700")
text(BX + BW + 14, Y_GND + 6, "GND rail (blue)", 18, BLU, w="700")
text(BX + BW + 14, ROWS["b"] + 6, "Each numbered column", 14, "#777")
text(BX + BW + 14, ROWS["b"] + 26, "(holes a–e) is one", 14, "#777")
text(BX + BW + 14, ROWS["b"] + 46, "connected strip.", 14, "#777")

# regulator in columns 3 4 5 (GND IN OUT), legs in row a, body above them
G, I, O = 3, 4, 5
ra = ROWS["a"]
add(f'<path d="M{cx(G)-18},{ra-8} L{cx(G)-18},{ra-44} L{cx(O)+18},{ra-44} L{cx(O)+18},{ra-8} Z" fill="#222"/>')
for c, t in ((G, "GND"), (I, "IN"), (O, "OUT")):
    path(f"M{cx(c)},{ra-10} L{cx(c)},{ra}", "#aaa", 3)
    pin(cx(c), ra, "#c9c9c9"); text(cx(c), ra - 22, t, 10, "#fff", "middle", "700")
text(BX + 10, BY - 30, "Regulator MCP1700-3302E", 13, "#222", w="700")
text(BX + 10, BY - 12, "flat side facing you, columns 3-4-5", 12, "#555")
def jumper_d(d, col, a, b):
    path(d, col, 5); pin(*a, col); pin(*b, col)
# GND leg -> GND rail ; OUT leg's strip -> +3.3 V rail (arcs right, up to column 7)
jumper_d(f"M{cx(G)},{ROWS['e']} L{cx(G)},{Y_GND}", BLK, (cx(G), ROWS['e']), (cx(G), Y_GND))
jumper_d(f"M{cx(O)},{ROWS['c']} C{cx(7)},{ROWS['c']} {cx(7)},{ROWS['c']} {cx(7)},{Y_PLUS}", RED,
         (cx(O), ROWS['c']), (cx(7), Y_PLUS))
def cap(c, name, side):
    """A 1 µF capacitor: two legs, one in row e of column c, one straight
    below in the GND rail, with its blue ceramic body in between."""
    x = cx(c); y1, y2 = ROWS["e"], Y_GND; my = (y1 + y2) / 2
    path(f"M{x},{y1} L{x},{y2}", "#888", 3)
    add(f'<rect x="{x-9}" y="{my-11}" width="18" height="22" rx="9" fill="#e8a62a" stroke="{BRN}" stroke-width="1.5"/>')
    pin(x, y1, "#e8a62a"); pin(x, y2, "#e8a62a")
    if side == "l":                                  # left of the GND jumper, with a leader
        path(f"M{x - 104},{my + 14} L{x - 9},{my + 4}", BRN, 1.5)
        text(x - 106, my + 18, name, 12, BRN, "end", "700")
    else:
        text(x + 16, my + 5, name, 12, BRN, "start", "700")
cap(I, "C1 1µF", "l")
cap(O, "C2 1µF", "r")
# pull-ups: SDA col 10, SCL col 14 ; resistor from row a to +3.3 rail
SDA, SCL = 10, 14
def res(c, name):
    x = cx(c); y1, y2 = Y_PLUS, ROWS["a"]; m = (y1 + y2) / 2
    path(f"M{x},{y1} L{x},{y2}", BRN, 3)
    add(f'<rect x="{x-9}" y="{m-20}" width="18" height="40" rx="7" fill="#d9c28e" stroke="{BRN}" stroke-width="1.5"/>')
    for i, cc in enumerate(("#f2c200", "#7b3fa0", RED, "#c8a000")):
        add(f'<rect x="{x-9}" y="{m-14+i*8}" width="18" height="4" fill="{cc}"/>')
    pin(x, y1, "#d9c28e"); pin(x, y2, "#d9c28e")
    text(x + 16, m + 5, name, 13, BRN, w="700")
res(SDA, "R1 4.7 kΩ"); res(SCL, "R2 4.7 kΩ")
text(cx(SDA), ROWS["e"] + 40, "SDA strip", 14, BLU, "middle", "700")
text(cx(SCL), ROWS["e"] + 40, "SCL strip", 14, "#b38600", "middle", "700")

# Cardputer connector, left
HX, HY = 60, 300
add(f'<rect x="{HX}" y="{HY}" width="300" height="400" rx="14" fill="#2b2f36"/>')
text(HX + 150, HY + 362, "Cardputer top connector", 16, "#fff", "middle", "700")
text(HX + 150, HY + 382, "odd row = nearest the keyboard", 12, "#bbb", "middle")
odd = ["G26", "G23", "G22", "CLK", "MOSI", "MISO", "CS1"]
even = ["5V IN", "GND", "5V OUT", "SDA", "SCL", "RX", "TX"]
used = {4: (BLK, "GND"), 6: (RED, "5 V"), 3: (BLU, "SDA"), 1: (YEL, "SCL")}
pos = {}
for i in range(7):
    y = HY + 40 + i * 42
    xo, xe = HX + 70, HX + 230
    no = 2 * i + 1
    oc = used.get(no, (None,))[0]
    add(f'<rect x="{xo-10}" y="{y-10}" width="20" height="20" fill="#000" stroke="{oc or "#666"}" stroke-width="{3 if oc else 1}"/>')
    text(xo + 18, y + 5, f"{no} " + ((used[no][1] + " " + odd[i]) if oc else odd[i]), 12,
         "#fff" if oc else "#888", "start", "700" if oc else "400")
    pos[no] = (xo, y)
    n = 2 * i + 2
    col = used.get(n, (None,))[0]
    add(f'<rect x="{xe-10}" y="{y-10}" width="20" height="20" fill="#000" stroke="{col or "#666"}" stroke-width="{3 if col else 1}"/>')
    text(xe - 18, y + 5, f"{n} {even[i]}", 12, "#fff" if col else "#888", "end", "700" if col else "400")
    pos[n] = (xe, y)
text(HX + 150, HY + 400 + 26, "Pin 2 (5V IN) stays empty", 14, RED, "middle", "700")

# SDA and SCL from pins 3 and 1 (a bus of their own, not the keyboard's):
# up out of the header, across above everything, down into their strips
xl = {6: 420, 4: 400}
for n, ly, jog, tx, ty in ((1, HY - 22, 0, cx(SCL), ROWS["d"]), (3, HY - 44, -28, cx(SDA), ROWS["d"])):
    px, py = pos[n]; col = used[n][0]
    path(f"M{px},{py} H{px + jog} V{ly} H{tx} V{ty}", col, 6); pin(px, py, col); pin(tx, ty, col)
px, py = pos[6]
path(f"M{px},{py} H{xl[6]} V{ROWS['d']} H{cx(I)}", RED, 6); pin(px, py, RED); pin(cx(I), ROWS['d'], RED)
text(BX - 8, ROWS['d'] - 10, "pin 6 (5 V)", 13, RED, "end", "700")
px, py = pos[4]
path(f"M{px},{py} H{xl[4]} V{Y_GND + 85} H{cx(1)} V{Y_GND}", BLK, 6); pin(px, py, BLK); pin(cx(1), Y_GND, BLK)
text(cx(SDA) + 10, BY - 12, "pin 3 (SDA)", 13, BLU, w="700")
text(cx(SCL) + 10, BY - 12, "pin 1 (SCL)", 13, "#b38600", w="700")
text(xl[4] + 12, Y_GND + 104, "pin 4 (GND) → GND rail", 13, BLK, w="700")

# Adafruit breakout, top right, 4209 cable
AX, AY = 1180, 140
add(f'<rect x="{AX}" y="{AY}" width="230" height="130" rx="12" fill="#1f2a44"/>')
text(AX + 115, AY + 40, "Adafruit 4314", 18, "#fff", "middle", "700")
text(AX + 115, AY + 64, "ATECC608 (blank chip)", 13, "#cfd6e6", "middle")
text(AX + 115, AY + 112, "4209 cable in its small port", 12, "#cfd6e6", "middle")
add(f'<rect x="{AX+80}" y="{AY+128}" width="70" height="16" rx="3" fill="#ddd"/>')
cab = [(RED, cx(19), Y_PLUS, "red → +3.3 V rail"), (BLK, cx(21), Y_GND, "black → GND rail"),
       (BLU, cx(SDA), ROWS["c"], "blue → SDA strip (column 10)"), (YEL, cx(SCL), ROWS["c"], "yellow → SCL strip (column 14)")]
for i, (col, tx, ty, t) in enumerate(cab):
    sx = AX + 92 + i * 15
    ly = AY + 175 + i * 22
    if col == BLK:
        path(f"M{sx},{AY+144} V{ly} H{cx(22)+40} V{Y_GND} H{tx}", col, 5)
    else:
        path(f"M{sx},{AY+144} V{ly} H{tx} V{ty}", col, 5)
    pin(tx, ty, col)
    text(AX - 250, AY + 20 + i * 26, "■", 18, col); text(AX - 228, AY + 20 + i * 26, t, 14, "#222")

notes = ["Check the regulator's pin order on its datasheet first (usually GND, IN, OUT with the flat side facing you).",
         "R1 and R2 go to the +3.3 V rail, never to 5 V: 5 V on SDA/SCL can damage the Cardputer.",
         "If your breadboard's rails are split in the middle, bridge each with a short jumper.",
         "First set SETUP › DEVICE › HARDWARE › EXT HEADER › SECURE CHIP (0.8.41+), switch off, wire it, switch on.",
         "Then SETUP › DEVICE › HARDWARE › SECURE CHIP should say blank chip (0x60). Detecting only reads; nothing gets locked."]
for i, t in enumerate(notes):
    text(40, 880 + i * 26, f"{i+1}. {t}", 16, "#333")
add("</svg>")
open("breadboard.svg", "w").write("\n".join(o))
