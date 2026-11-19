"""Tiny helpers for KiCad s-expression files."""
import re


def block(text, start):
    """The balanced (...) starting at index start."""
    depth, i, q = 0, start, False
    while i < len(text):
        ch = text[i]
        if q:
            if ch == "\\":
                i += 1
            elif ch == '"':
                q = False
        elif ch == '"':
            q = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    raise ValueError("unbalanced")


def lib_symbol(lib, name):
    text = open(f"/usr/share/kicad/symbols/{lib}.kicad_sym").read()
    m = re.search(r'\n  \(symbol "%s"' % re.escape(name), text)
    return block(text, m.start() + 3)


def pins(sym_block):
    """{number: (x, y, angle)} in library coordinates (y up)."""
    out = {}
    for m in re.finditer(r'\(pin \w+ \w+\s*\(at ([-\d.]+) ([-\d.]+) (\d+)\)', sym_block):
        b = block(sym_block, m.start())
        num = re.search(r'\(number "([^"]+)"', b).group(1)
        out.setdefault(num, (float(m.group(1)), float(m.group(2)), int(m.group(3))))
    return out
