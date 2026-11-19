"""Checks the board's pad-to-net assignments and copper connectivity against
the same intended netlist the schematic is checked with."""
import os
import sys

import pcbnew

from check_net import EXPECT

b = pcbnew.LoadBoard(os.path.abspath("secure-chip.kicad_pcb"))
got = {}
for f in b.GetFootprints():
    for p in f.Pads():
        n = p.GetNetname()
        if n:
            got.setdefault(n, set()).add((f.GetReference(), p.GetNumber()))
ok = got == EXPECT
if not ok:
    for n in sorted(set(got) | set(EXPECT)):
        if got.get(n) != EXPECT.get(n):
            print(n, "board:", sorted(got.get(n, [])), "want:", sorted(EXPECT.get(n, [])))
b.BuildConnectivity()
unrouted = b.GetConnectivity().GetUnconnectedCount(False)
print("pads-to-nets", "OK" if ok else "MISMATCH", "| unrouted connections:", unrouted)
sys.exit(0 if ok and unrouted == 0 else 1)
