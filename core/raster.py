"""
The two raster page formats every driverless printer must take:

- PWG Raster (image/pwg-raster), required by IPP Everywhere (PWG 5102.4);
- Apple Raster, "URF" (image/urf), required by AirPrint.

Printers that take neither JPEG nor PDF over IPP (many inkjets, like
Canon's PIXMA range) still take one of these. Both are the same idea: a
header, then the page line by line, each line packed with the same simple
run-length code (a line-repeat count, then runs of one colour or stretches
of literal pixels). Pure Python, no CUPS.

The paper wallet page is drawn at 150 dpi; printers want 300 or 600. The
page is scaled up by whole pixels while it's packed (runs just get longer,
lines repeat), so a 600 dpi page never sits in memory unpacked.
"""
from __future__ import annotations

import itertools
import struct

PWG, URF = "image/pwg-raster", "image/urf"
LETTER_IN = (8.5, 11.0)


def _rows(img, mode: str, k: int):
    """(row bytes at the page's own size, how many times it repeats after
    scaling), with identical neighbouring rows merged."""
    img = img.convert(mode)
    w, h = img.size
    bpp = len(mode) if mode == "RGB" else 1
    data = img.tobytes()
    stride = w * bpp
    rows = (data[y * stride:(y + 1) * stride] for y in range(h))
    for row, same in itertools.groupby(rows):
        yield row, sum(1 for _ in same) * k


def _pack_line(row: bytes, bpp: int, k: int) -> bytes:
    """One line in the shared run-length code, each pixel widened k times.
    0..127: the next pixel repeats n+1 times; 129..255: 257-n literal
    pixels follow. (128, "fill to the end with white", isn't used.)"""
    px = [row[i:i + bpp] for i in range(0, len(row), bpp)]
    runs = [(p, sum(1 for _ in g) * k) for p, g in itertools.groupby(px)]
    out = bytearray()
    lit: list[bytes] = []

    def flush():
        while lit:
            chunk = lit[:128]
            del lit[:128]
            out.append(257 - len(chunk) if len(chunk) > 1 else 0)
            out.extend(b"".join(chunk))

    for p, n in runs:
        if n == 1:
            lit.append(p)
            continue
        flush()
        while n:
            take = min(n, 128)
            if take == 1:                      # a lone leftover pixel
                out.append(0)
            else:
                out.append(take - 1)
            out += p
            n -= take
    flush()
    return bytes(out)


def _pack_page(img, mode: str, k: int) -> bytes:
    bpp = 3 if mode == "RGB" else 1
    out = bytearray()
    for row, count in _rows(img, mode, k):
        line = _pack_line(row, bpp, k)
        while count:
            take = min(count, 256)
            out.append(take - 1)
            out += line
            count -= take
    return bytes(out)


def _scale(img, dpi: int) -> int:
    """Whole-number scale from the page's dpi to the printer's."""
    src = round(img.width / LETTER_IN[0])
    if dpi % src:
        raise ValueError(f"{dpi} dpi isn't a whole multiple of {src}")
    return dpi // src


def pwg(img, dpi: int = 300, color: bool = False) -> bytes:
    """A one-page PWG Raster file (sgray_8, or srgb_8 with color)."""
    k = _scale(img, dpi)
    mode = "RGB" if color else "L"
    w, h = img.width * k, img.height * k
    bpp = 3 if color else 1
    hdr = bytearray(1796)

    def s(off, text):
        b = text.encode()[:63]
        hdr[off:off + len(b)] = b

    def u(off, *vals):
        struct.pack_into(">" + "I" * len(vals), hdr, off, *vals)

    s(0, "PwgRaster")                          # MediaClass
    u(276, dpi, dpi)                           # HWResolution
    u(340, 1)                                  # NumCopies
    u(352, 612, 792)                           # PageSize, points
    u(372, w, h)                               # cupsWidth, cupsHeight
    u(384, 8, 8 * bpp, w * bpp)                # BitsPerColor, BitsPerPixel, BytesPerLine
    u(396, 0, 19 if color else 18)             # chunky; sRGB / sGray
    u(420, bpp)                                # NumColors
    u(452, 1, 1, 1)                            # TotalPageCount, Cross/FeedTransform
    u(452 + 4 * 7, 0x00FFFFFF)                 # AlternatePrimary
    s(1732, "na_letter_8.5x11in")              # PageSizeName
    return b"RaS2" + bytes(hdr) + _pack_page(img, mode, k)


def urf(img, dpi: int = 300, color: bool = False) -> bytes:
    """A one-page Apple Raster (URF) file (W8 gray, or SRGB24)."""
    k = _scale(img, dpi)
    mode = "RGB" if color else "L"
    w, h = img.width * k, img.height * k
    hdr = bytearray(32)
    hdr[0] = 24 if color else 8                # bits per pixel
    hdr[1] = 1 if color else 0                 # sRGB / sGray
    hdr[2] = 1                                 # one-sided
    hdr[3] = 4                                 # normal quality
    struct.pack_into(">III", hdr, 12, w, h, dpi)
    return b"UNIRAST\0" + struct.pack(">I", 1) + bytes(hdr) + _pack_page(img, mode, k)


# ------------------------------------------------- what the printer accepts
def pwg_choice(resolutions: list, types: list) -> dict | None:
    """From pwg-raster-document-resolution-supported (raw 9-byte values)
    and -type-supported: the dpi and colour to use, or None."""
    dpis = []
    for r in resolutions:
        if isinstance(r, (bytes, bytearray)) and len(r) == 9 and r[8] == 3:   # per inch
            x, y = struct.unpack(">ii", r[:8])
            if x == y:
                dpis.append(x)
    dpi = _pick_dpi(dpis)
    if dpi is None:
        return None
    if "sgray_8" in types:
        return {"dpi": dpi, "color": False}
    if "srgb_8" in types:
        return {"dpi": dpi, "color": True}
    return None


def urf_choice(supported: list) -> dict | None:
    """From urf-supported (e.g. V1.4, W8, SRGB24, RS300-600): dpi and colour."""
    words = []
    for v in supported:
        if isinstance(v, str):
            words += v.split(",")
    dpis = []
    for w in words:
        if w.startswith("RS"):
            for part in w[2:].split("-"):
                if part.isdigit():
                    dpis.append(int(part))
    dpi = _pick_dpi(dpis)
    if dpi is None:
        return None
    if "W8" in words:
        return {"dpi": dpi, "color": False}
    if "SRGB24" in words:
        return {"dpi": dpi, "color": True}
    return None


def _pick_dpi(dpis: list[int]) -> int | None:
    """300 if offered, else the lowest multiple of 150 (sharp, small)."""
    ok = sorted(d for d in set(dpis) if d > 0 and d % 150 == 0 and d <= 1200)
    if 300 in ok:
        return 300
    return ok[0] if ok else None
