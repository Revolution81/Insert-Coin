"""Builds secure-chip.kicad_pcb: a 24 x 16 mm backpack for the Cardputer Zero's
EXT header. Parts on the front (outside), the 2x7 header on the back, its
pins plugging into the device. Coordinates are mm from the board's top-left,
seen from the front; "top" is the keyboard side.
"""
import pcbnew

FP = "/usr/share/kicad/footprints/"
OX, OY = 100.0, 100.0          # where the board sits on KiCad's canvas
W, H = 24.0, 16.0


def P(x, y):
    return pcbnew.VECTOR2I(pcbnew.FromMM(OX + x), pcbnew.FromMM(OY + y))


b = pcbnew.BOARD()
ds = b.GetDesignSettings()
ds.m_TrackMinWidth = pcbnew.FromMM(0.2)
ds.m_MinClearance = pcbnew.FromMM(0.2)
ds.m_ViasMinSize = pcbnew.FromMM(0.6)
ds.m_MinThroughDrill = pcbnew.FromMM(0.3)
ds.m_CopperEdgeClearance = pcbnew.FromMM(0.4)

nets = {}
for name in ["GND", "+5V", "+3V3", "SDA", "SCL"]:
    n = pcbnew.NETINFO_ITEM(b, name)
    b.Add(n)
    nets[name] = n

# ref -> (library, footprint, value, x, y, rotation, flip, {pad: net})
PARTS = {
    "J1": ("Connector_PinHeader_2.54mm", "PinHeader_2x07_P2.54mm_Vertical",
           "EXT 2x7", 6.61, 2.6, 270, True, {"4": "GND", "6": "+5V", "8": "SDA", "10": "SCL"}),
    # rotated 90: top row 8,7,6,5 left to right, so SDA (5) sits under header pin 8
    "U2": ("Package_SO", "SOIC-8_3.9x4.9mm_P1.27mm", "ATECC608B-SSHDA", 12.325, 11.0, 90, False,
           {"4": "GND", "8": "+3V3", "5": "SDA", "6": "SCL"}),
    "U1": ("Package_TO_SOT_SMD", "SOT-23", "MCP1700T-3302E/TT", 5.0, 12.0, 0, False,
           {"1": "GND", "2": "+3V3", "3": "+5V"}),
    "C1": ("Capacitor_SMD", "C_0603_1608Metric", "1uF", 6.6, 8.775, None, False, {"1": "+5V", "2": "GND"}),
    "C2": ("Capacitor_SMD", "C_0603_1608Metric", "1uF", 2.0, 14.9, 0, False, {"1": "+3V3", "2": "GND"}),
    "C3": ("Capacitor_SMD", "C_0603_1608Metric", "100nF", 8.3, 9.275, None, False, {"1": "+3V3", "2": "GND"}),
    "R1": ("Resistor_SMD", "R_0603_1608Metric", "4.7k", 16.6, 7.3, None, False, {"1": "+3V3", "2": "SDA"}),
    "R2": ("Resistor_SMD", "R_0603_1608Metric", "4.7k", 16.6, 9.0, None, False, {"1": "+3V3", "2": "SCL"}),
}

fps = {}
for ref, (lib, name, value, x, y, rot, flip, padnets) in PARTS.items():
    f = pcbnew.FootprintLoad(FP + lib + ".pretty", name)
    b.Add(f)
    f.SetReference(ref)
    f.SetValue(value)
    f.SetFPID(pcbnew.LIB_ID(lib, name))
    f.SetPosition(P(x, y))
    if flip:
        f.Flip(f.GetPosition(), False)
    fps[ref] = f
    f.Value().SetVisible(False)
    if rot is not None:
        f.SetOrientationDegrees(rot)
    for pad in f.Pads():
        if pad.GetNumber() in padnets:
            pad.SetNet(nets[padnets[pad.GetNumber()]])


def pad_xy(ref, num):
    p = [q for q in fps[ref].Pads() if q.GetNumber() == num][0].GetPosition()
    return round(pcbnew.ToMM(p.x) - OX, 3), round(pcbnew.ToMM(p.y) - OY, 3)


def orient(ref, want):
    """Try rotations until pad positions satisfy want(pad_xy)."""
    for r in (0, 90, 180, 270):
        fps[ref].SetOrientationDegrees(r)
        if want(lambda n: pad_xy(ref, n)):
            return r
    raise SystemExit(f"no rotation fits {ref}")


same_x = lambda p: abs(p("2")[0] - p("1")[0]) < 0.01
same_y = lambda p: abs(p("2")[1] - p("1")[1]) < 0.01
orient("C1", lambda p: same_x(p) and p("1")[1] < p("2")[1])      # vertical, +5V on top
orient("C3", lambda p: same_x(p) and p("1")[1] < p("2")[1])      # vertical, +3V3 on top
orient("R1", lambda p: same_y(p) and p("2")[0] < p("1")[0])      # horizontal, SDA on the left
orient("R2", lambda p: same_y(p) and p("2")[0] < p("1")[0])      # horizontal, SCL on the left

# the header must match the device's EXT label seen from outside:
# odd pins 1..13 on the keyboard-side row, left to right; even 2..14 below
assert pad_xy("J1", "1") == (6.61, 2.6), pad_xy("J1", "1")
assert pad_xy("J1", "3") == (9.15, 2.6) and pad_xy("J1", "2") == (6.61, 5.14)
assert pad_xy("J1", "8") == (14.23, 5.14) and pad_xy("J1", "10") == (16.77, 5.14)
assert pad_xy("U2", "5") == (14.23, 8.525) and pad_xy("U2", "8")[0] < pad_xy("U2", "5")[0]

for ref in fps:
    print(ref, {p.GetNumber(): pad_xy(ref, p.GetNumber()) for p in fps[ref].Pads()})


def track(net, pts, w=0.25, layer=pcbnew.F_Cu):
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(P(x1, y1))
        t.SetEnd(P(x2, y2))
        t.SetWidth(pcbnew.FromMM(w))
        t.SetLayer(layer)
        t.SetNet(nets[net])
        b.Add(t)


def via(net, x, y):
    v = pcbnew.PCB_VIA(b)
    v.SetPosition(P(x, y))
    v.SetWidth(pcbnew.FromMM(0.6))
    v.SetDrill(pcbnew.FromMM(0.3))
    v.SetNet(nets[net])
    b.Add(v)


BOT = pcbnew.B_Cu
u2 = lambda n: pad_xy("U2", n)
j = lambda n: pad_xy("J1", n)
# SDA (front): header 8 straight down to U2.5; R1.2 joins on the right
track("SDA", [j("8"), u2("5")])
track("SDA", [(j("8")[0], pad_xy("R1", "2")[1]), pad_xy("R1", "2")])
# SCL (back): header 10 -> via by U2.6, and down to a via under R2.2
track("SCL", [j("10"), (j("10")[0], 6.0), (15.825, 6.9), (u2("6")[0], 6.9)], layer=BOT)
via("SCL", u2("6")[0], 6.9)
track("SCL", [(u2("6")[0], 6.9), u2("6")])
track("SCL", [(15.825, 6.9), (15.825, 10.3)], layer=BOT)
via("SCL", 15.825, 10.3)
track("SCL", [(15.825, 10.3), pad_xy("R2", "2")])
# +5V: header 6 -> along under the header row -> C1.1 -> U1.3 (VI)
c11 = pad_xy("C1", "1")
vi = pad_xy("U1", "3")
track("+5V", [j("6"), (j("6")[0], 6.3), (11.0, 6.6), (c11[0], 6.6), c11], w=0.4)
track("+5V", [c11, (5.3, c11[1] + 0.6), (5.3, vi[1] - 0.6), vi], w=0.4)
# +3V3: U1.2 (VO) -> C2.1; VO -> right -> up to U2.8 (VCC) with C3.1 on the way
vo = pad_xy("U1", "2")
c21 = pad_xy("C2", "1")
track("+3V3", [vo, (c21[0], vo[1] + 0.6), c21], w=0.4)
track("+3V3", [vo, (9.3, vo[1]), (9.3, u2("8")[1]), u2("8")], w=0.4)
track("+3V3", [pad_xy("C3", "1"), (9.3, pad_xy("C3", "1")[1])], w=0.4)
# and to R1.1 / R2.1 on the right, under the board
r11, r21 = pad_xy("R1", "1"), pad_xy("R2", "1")
track("+3V3", [r11, r21], w=0.3)
track("+3V3", [r21, (18.5, r21[1])], w=0.3)
via("+3V3", 18.5, r21[1])
via("+3V3", 9.3, vo[1])
track("+3V3", [(9.3, vo[1]), (9.3, 15.2), (18.5, 15.2), (18.5, r21[1])], w=0.4, layer=BOT)
# header GND: a solid link on the back too (its neighbours crowd out the spokes)
track("GND", [j("4"), (j("4")[0], 7.2)], w=0.5, layer=BOT)
# GND vias next to every SMD ground (the pours join them)
for (ref, num, dx, dy) in [("U2", "4", 1.2, 0.0), ("U1", "1", -1.2, 0.0), ("C1", "2", 0.0, 1.05),
                           ("C2", "2", 1.15, 0.0), ("C3", "2", 0.0, 1.05)]:
    x, y = pad_xy(ref, num)
    track("GND", [(x, y), (x + dx, y + dy)], w=0.4)
    via("GND", x + dx, y + dy)

# where each part's label sits (front silkscreen), clear of every pad
REF_AT = {"U2": (12.33, 11.0), "U1": (1.3, 12.0), "C1": (5.4, 7.0), "C2": (2.0, 13.6),
          "C3": (8.3, 7.0), "R1": (19.6, 7.3), "R2": (19.6, 9.0), "J1": (2.5, 4.4)}
for ref, (x, y) in REF_AT.items():
    t = fps[ref].Reference()
    t.SetVisible(True)
    t.SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(0.8), pcbnew.FromMM(0.8)))
    t.SetTextThickness(pcbnew.FromMM(0.12))
    t.SetPosition(P(x, y))
    # a footprint's text turns with it in KiCad 7: undo the part's rotation
    t.SetTextAngleDegrees(-fps[ref].GetOrientationDegrees())
    t.SetKeepUpright(True)

# outline
for (x1, y1), (x2, y2) in [((0, 0), (W, 0)), ((W, 0), (W, H)), ((W, H), (0, H)), ((0, H), (0, 0))]:
    s = pcbnew.PCB_SHAPE(b)
    s.SetShape(pcbnew.SHAPE_T_SEGMENT)
    s.SetStart(P(x1, y1))
    s.SetEnd(P(x2, y2))
    s.SetLayer(pcbnew.Edge_Cuts)
    s.SetWidth(pcbnew.FromMM(0.1))
    b.Add(s)

# ground pours, both sides
for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
    z = pcbnew.ZONE(b)
    z.SetLayer(layer)
    z.SetNet(nets["GND"])
    z.SetLocalClearance(pcbnew.FromMM(0.3))
    z.SetMinThickness(pcbnew.FromMM(0.25))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
    ol = z.Outline()
    ol.NewOutline()
    for x, y in [(0.3, 0.3), (W - 0.3, 0.3), (W - 0.3, H - 0.3), (0.3, H - 0.3)]:
        ol.Append(pcbnew.FromMM(OX + x), pcbnew.FromMM(OY + y))
    b.Add(z)

# words on the board
def text(s, x, y, layer=pcbnew.F_SilkS, size=0.8, mirror=False):
    t = pcbnew.PCB_TEXT(b)
    t.SetText(s)
    t.SetPosition(P(x, y))
    t.SetLayer(layer)
    t.SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(size), pcbnew.FromMM(size)))
    t.SetTextThickness(pcbnew.FromMM(size * 0.15))
    t.SetMirrored(mirror)
    b.Add(t)

text("INSERT", 20.4, 11.2, size=0.8)
text("COIN", 20.4, 12.4, size=0.8)
text("CHIP v1", 20.4, 13.6, size=0.8)
text("^ KEYS", 3.0, 1.2, size=0.8)
text("HEADER SIDE", 4.4, 8.4, layer=pcbnew.B_SilkS, size=0.8, mirror=True)
text("POWER OFF", 13.9, 11.75, layer=pcbnew.B_SilkS, size=0.8, mirror=True)
text("TO PLUG IN", 13.9, 12.85, layer=pcbnew.B_SilkS, size=0.8, mirror=True)

b.SetFileName("secure-chip.kicad_pcb")
b.Save("secure-chip.kicad_pcb")
# fill the pours on a freshly loaded copy (filling a board built in memory
# crashes KiCad 7's Python module), then check it
import os
b = pcbnew.LoadBoard(os.path.abspath("secure-chip.kicad_pcb"))
b.BuildConnectivity()
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(os.path.abspath("secure-chip.kicad_pcb"))
pcbnew.WriteDRCReport(b, os.path.abspath("drc.rpt"), pcbnew.EDA_UNITS_MILLIMETRES, True)
print(open("drc.rpt").read()[-4000:])
