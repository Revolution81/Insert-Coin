"""Checks the schematic's exported netlist against the intended design."""
import re
import sys

import sexp

EXPECT = {
    "+5V": {("J1", "6"), ("U1", "3"), ("C1", "1")},
    "GND": {("J1", "4"), ("U1", "1"), ("C1", "2"), ("C2", "2"), ("C3", "2"), ("U2", "4")},
    "+3V3": {("U1", "2"), ("C2", "1"), ("C3", "1"), ("U2", "8"), ("R1", "1"), ("R2", "1")},
    "SDA": {("J1", "8"), ("U2", "5"), ("R1", "2")},
    "SCL": {("J1", "10"), ("U2", "6"), ("R2", "2")},
}


def read(path):
    text = open(path).read()
    nets = {}
    i = text.find("(nets")
    for m in re.finditer(r'\(net \(code "?\d+"?\) \(name "([^"]+)"\)', text[i:]):
        b = sexp.block(text, i + m.start())
        nodes = set(re.findall(r'\(node \(ref "([^"]+)"\) \(pin "([^"]+)"', b))
        nets[m.group(1).lstrip("/")] = nodes
    return nets


def main():
    nets = read(sys.argv[1])
    ok = True
    for name, want in EXPECT.items():
        got = nets.get(name, set())
        if got != want:
            ok = False
            print(f"{name}: expected {sorted(want)} got {sorted(got)}")
    extra = [n for n, nodes in nets.items() if n not in EXPECT and len(nodes) > 1]
    if extra:
        ok = False
        print("unexpected multi-pin nets:", extra)
    print("NETLIST OK" if ok else "NETLIST MISMATCH")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
