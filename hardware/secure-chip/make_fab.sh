#!/bin/sh
# Rebuilds everything from the scripts: schematic, board, checks, and the
# files a board house needs. Needs KiCad 7 (kicad-cli and its Python module).
set -eu
cd "$(dirname "$0")"
export KICAD7_FOOTPRINT_DIR="${KICAD7_FOOTPRINT_DIR:-/usr/share/kicad/footprints}"
python3 gen_sch.py
kicad-cli sch export netlist -o secure-chip.net secure-chip.kicad_sch
python3 check_net.py secure-chip.net
kicad-cli sch export pdf -o secure-chip-schematic.pdf secure-chip.kicad_sch
python3 gen_pcb.py > /dev/null
grep -q "Found 0 DRC violations" drc.rpt && grep -q "Found 0 unconnected pads" drc.rpt
python3 check_pcb.py
rm -rf fab && mkdir -p fab/gerbers
kicad-cli pcb export gerbers -o fab/gerbers/ --subtract-soldermask \
  -l F.Cu,B.Cu,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts secure-chip.kicad_pcb
kicad-cli pcb export drill -o fab/gerbers/ --format excellon --excellon-units mm secure-chip.kicad_pcb
kicad-cli pcb export pos -o fab/kicad-pos.csv --format csv --units mm --side front secure-chip.kicad_pcb
python3 jlc_files.py
(cd fab/gerbers && zip -q ../secure-chip-gerbers.zip ./*)
echo "fab/: secure-chip-gerbers.zip, secure-chip-bom.csv, secure-chip-cpl.csv"
