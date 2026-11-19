"""Writes secure-chip.kicad_sch: an ATECC608B on the Cardputer Zero's EXT header.

Every pin gets a short wire and a net label, so the drawing reads as a
netlist and stays easy to check: run `kicad-cli sch erc` and
`kicad-cli sch export netlist` on the result.
"""
import uuid

import sexp

ROOT = str(uuid.uuid5(uuid.NAMESPACE_URL, "insert-coin-secure-chip"))
_n = 0


def uid():
    global _n
    _n += 1
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ic-sc-{_n}"))


LIBS = {
    "Device:R_Small": ("Device", "R_Small"),
    "Device:C_Small": ("Device", "C_Small"),
    "Security:ATECC608A-SSHDA": ("Security", "ATECC608A-SSHDA"),
    "Regulator_Linear:MCP1700x-300xxTT": ("Regulator_Linear", "MCP1700x-300xxTT"),
    "Connector_Generic:Conn_02x07_Odd_Even": ("Connector_Generic", "Conn_02x07_Odd_Even"),
    "power:PWR_FLAG": ("power", "PWR_FLAG"),
}

# ref, lib_id, value, footprint, MPN, (x, y), {pin: net or None (no connect)}
J1_NETS = {str(p): None for p in range(1, 15)}
J1_NETS.update({"4": "GND", "6": "+5V", "8": "SDA", "10": "SCL"})
PARTS = [
    ("J1", "Connector_Generic:Conn_02x07_Odd_Even", "EXT 2x7 to Cardputer Zero",
     "Connector_PinHeader_2.54mm:PinHeader_2x07_P2.54mm_Vertical", "2x7 2.54mm male header",
     (50.8, 101.6), J1_NETS),
    ("U1", "Regulator_Linear:MCP1700x-300xxTT", "MCP1700T-3302E/TT",
     "Package_TO_SOT_SMD:SOT-23", "MCP1700T-3302E/TT", (101.6, 76.2),
     {"3": "+5V", "2": "+3V3", "1": "GND"}),
    ("C1", "Device:C_Small", "1uF", "Capacitor_SMD:C_0603_1608Metric",
     "1uF 16V X7R 0603", (83.82, 76.2), {"1": "+5V", "2": "GND"}),
    ("C2", "Device:C_Small", "1uF", "Capacitor_SMD:C_0603_1608Metric",
     "1uF 16V X7R 0603", (121.92, 76.2), {"1": "+3V3", "2": "GND"}),
    ("U2", "Security:ATECC608A-SSHDA", "ATECC608B-SSHDA (blank)",
     "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "ATECC608B-SSHDA-T", (160.02, 116.84),
     {"1": None, "2": None, "3": None, "7": None, "4": "GND", "8": "+3V3",
      "5": "SDA", "6": "SCL"}),
    ("C3", "Device:C_Small", "100nF", "Capacitor_SMD:C_0603_1608Metric",
     "100nF 16V X7R 0603", (139.7, 116.84), {"1": "+3V3", "2": "GND"}),
    ("R1", "Device:R_Small", "4.7k", "Resistor_SMD:R_0603_1608Metric",
     "4.7k 1% 0603", (185.42, 101.6), {"1": "+3V3", "2": "SDA"}),
    ("R2", "Device:R_Small", "4.7k", "Resistor_SMD:R_0603_1608Metric",
     "4.7k 1% 0603", (198.12, 101.6), {"1": "+3V3", "2": "SCL"}),
    ("#FLG01", "power:PWR_FLAG", "PWR_FLAG", "", "", (63.5, 127.0), {"1": "+5V"}),
    ("#FLG02", "power:PWR_FLAG", "PWR_FLAG", "", "", (76.2, 127.0), {"1": "GND"}),
]

OUT = {0: (-1, 0), 180: (1, 0), 90: (0, 1), 270: (0, -1)}   # outward, schematic y down


def fmt(v):
    return f"{v:.2f}".rstrip("0").rstrip(".")


def prop(name, value, x, y, hide=False):
    h = " hide" if hide else ""
    return (f'    (property "{name}" "{value}" (at {fmt(x)} {fmt(y)} 0)\n'
            f'      (effects (font (size 1.27 1.27)){h}))\n')


lib_blocks, body = [], []
for lib_id, (lib, name) in LIBS.items():
    b = sexp.lib_symbol(lib, name)
    lib_blocks.append(b.replace(f'(symbol "{name}"', f'(symbol "{lib_id}"', 1))

for ref, lib_id, value, fp, mpn, (sx, sy), nets in PARTS:
    lib, name = LIBS[lib_id]
    pins = sexp.pins(sexp.lib_symbol(lib, name))
    s = (f'  (symbol (lib_id "{lib_id}") (at {fmt(sx)} {fmt(sy)} 0) (unit 1)\n'
         f'    (in_bom {"no" if ref.startswith("#") else "yes"}) (on_board {"no" if ref.startswith("#") else "yes"}) (dnp no)\n'
         f'    (uuid {uid()})\n')
    s += prop("Reference", ref, sx + 3, sy - 10.16, hide=ref.startswith("#"))
    s += prop("Value", value, sx + 3, sy - 8.89)
    s += prop("Footprint", fp, sx, sy, hide=True)
    s += prop("Datasheet", "", sx, sy, hide=True)
    if mpn:
        s += prop("MPN", mpn, sx, sy, hide=True)
    for num in pins:
        s += f'    (pin "{num}" (uuid {uid()}))\n'
    s += (f'    (instances (project "secure-chip"\n'
          f'      (path "/{ROOT}" (reference "{ref}") (unit 1))))\n  )\n')
    body.append(s)
    for num, (px, py, ang) in pins.items():
        x, y = sx + px, sy - py
        net = nets.get(num, None)
        if net is None:
            body.append(f'  (no_connect (at {fmt(x)} {fmt(y)}) (uuid {uid()}))\n')
            continue
        dx, dy = OUT[ang]
        x2, y2 = x + dx * 2.54, y + dy * 2.54
        body.append(f'  (wire (pts (xy {fmt(x)} {fmt(y)}) (xy {fmt(x2)} {fmt(y2)}))\n'
                    f'    (stroke (width 0) (type default)) (uuid {uid()}))\n')
        just = "right" if dx < 0 else "left"
        lang = 90 if dy else 0
        body.append(f'  (label "{net}" (at {fmt(x2)} {fmt(y2)} {lang}) (fields_autoplaced)\n'
                    f'    (effects (font (size 1.27 1.27)) (justify {just} bottom)) (uuid {uid()}))\n')

notes = [
    (30.48, 30.48, "Insert Coin secure chip: blank ATECC608B on the Cardputer Zero EXT header"),
    (30.48, 35.56, "EXT pins used: 4 GND, 6 5V OUT (switched by HAT_EN), 8 G2 SDA, 10 G3 SCL. Pin 2 (5V IN) is never connected."),
    (30.48, 40.64, "The I2C bus is 3.3 V and shared with the keyboard (0x34), gauge (0x55), codec (0x10), IO expander (0x4F), RTC."),
    (30.48, 45.72, "A blank ATECC608B answers at 0x60. Its VCC must be 3.3 V so its pull-ups and logic levels match the Pi."),
]
for x, y, txt in notes:
    body.append(f'  (text "{txt}" (at {fmt(x)} {fmt(y)} 0)\n'
                f'    (effects (font (size 1.524 1.524)) (justify left bottom)) (uuid {uid()}))\n')

sch = ('(kicad_sch (version 20230121) (generator eeschema)\n'
       f'  (uuid {ROOT})\n  (paper "A4")\n'
       '  (title_block (title "Insert Coin secure chip") (date "2026-11-19") (rev "1")\n'
       '    (company "Revolution81") (comment 1 "ATECC608B + 3.3 V LDO for the M5Stack Cardputer Zero EXT header"))\n'
       '  (lib_symbols\n' + "\n".join("    " + b for b in lib_blocks) + '\n  )\n'
       + "".join(body) +
       '  (sheet_instances (path "/" (page "1")))\n)\n')
open("secure-chip.kicad_sch", "w").write(sch)
print("wrote secure-chip.kicad_sch")
