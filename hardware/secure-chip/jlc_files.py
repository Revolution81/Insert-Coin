"""BOM and placement (CPL) files in the format JLCPCB's assembly upload takes."""
import csv

BOM = [  # comment, designators, footprint, manufacturer part
    ("ATECC608B-SSHDA-T (blank, unprovisioned)", "U2", "SOIC-8_3.9x4.9mm_P1.27mm", "ATECC608B-SSHDA-T"),
    ("MCP1700T-3302E/TT 3.3V LDO", "U1", "SOT-23", "MCP1700T-3302E/TT"),
    ("1uF 16V X7R", "C1,C2", "0603", "GRM188R71C105KA12D"),
    ("100nF 16V X7R", "C3", "0603", "GRM188R71C104KA01D"),
    ("4.7k 1%", "R1,R2", "0603", "RC0603FR-074K7L"),
    ("2x7 2.54mm male header, straight (hand-solder, body on the BACK)", "J1",
     "PinHeader_2x07_P2.54mm_Vertical", "any 2x7 2.54mm"),
]
with open("fab/secure-chip-bom.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["Comment", "Designator", "Footprint", "Manufacturer Part", "LCSC Part #"])
    for row in BOM:
        w.writerow(list(row) + [""])

with open("fab/kicad-pos.csv") as src, open("fab/secure-chip-cpl.csv", "w", newline="") as dst:
    w = csv.writer(dst)
    w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
    for r in csv.DictReader(src):
        w.writerow([r["Ref"], f'{float(r["PosX"]):.3f}mm', f'{float(r["PosY"]):.3f}mm',
                    "Top" if r["Side"] == "top" else "Bottom", f'{float(r["Rot"]) % 360:.0f}'])
