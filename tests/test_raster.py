"""PWG and Apple (URF) raster: decoded back here and compared pixel for pixel."""
import struct

import pytest
from PIL import Image, ImageDraw

from core import ipp, raster
from tools.fake_printer import FakePrinter


def unpack(data: bytes, w: int, h: int, bpp: int) -> bytes:
    """The decoder side of the shared run-length code."""
    out, i = bytearray(), 0
    rows = 0
    while rows < h:
        rep = data[i] + 1
        i += 1
        line = bytearray()
        while len(line) < w * bpp:
            n = data[i]
            i += 1
            if n < 128:
                line += data[i:i + bpp] * (n + 1)
                i += bpp
            elif n > 128:
                line += data[i:i + (257 - n) * bpp]
                i += (257 - n) * bpp
            else:
                line += b"\xff" * (w * bpp - len(line))
        assert len(line) == w * bpp
        out += bytes(line) * rep
        rows += rep
    assert rows == h and i == len(data)
    return bytes(out)


def sample():
    img = Image.new("RGB", (1275, 1650), "white")
    d = ImageDraw.Draw(img)
    d.rectangle((100, 100, 700, 700), fill=(17, 17, 17))
    d.text((120, 900), "bc1q paper", fill=(184, 40, 48))
    for x in range(200, 1200, 3):               # stretches of differing pixels
        img.putpixel((x, 1200), (x % 256, 0, 0))
    return img


@pytest.mark.parametrize("dpi", [150, 300, 600])
@pytest.mark.parametrize("color", [False, True])
def test_pwg_round_trip(dpi, color):
    img = sample()
    data = raster.pwg(img, dpi, color)
    assert data[:4] == b"RaS2"
    h = data[4:4 + 1796]
    k = dpi // 150
    assert struct.unpack(">II", h[276:284]) == (dpi, dpi)
    assert struct.unpack(">II", h[352:360]) == (612, 792)
    w, ht = struct.unpack(">II", h[372:380])
    assert (w, ht) == (1275 * k, 1650 * k)
    bpp = 3 if color else 1
    assert struct.unpack(">III", h[384:396]) == (8, 8 * bpp, w * bpp)
    assert struct.unpack(">I", h[400:404])[0] == (19 if color else 18)
    assert h[1732:1750] == b"na_letter_8.5x11in"
    pix = unpack(data[4 + 1796:], w, ht, bpp)
    want = img.convert("RGB" if color else "L").resize((w, ht), Image.NEAREST).tobytes()
    assert pix == want


@pytest.mark.parametrize("color", [False, True])
def test_urf_round_trip(color):
    img = sample()
    data = raster.urf(img, 300, color)
    assert data[:8] == b"UNIRAST\0" and struct.unpack(">I", data[8:12])[0] == 1
    h = data[12:44]
    assert h[0] == (24 if color else 8) and h[1] == (1 if color else 0)
    w, ht, dpi = struct.unpack(">III", h[12:24])
    assert (w, ht, dpi) == (2550, 3300, 300)
    bpp = 3 if color else 1
    want = img.convert("RGB" if color else "L").resize((w, ht), Image.NEAREST).tobytes()
    assert unpack(data[44:], w, ht, bpp) == want


def test_choices():
    res = [struct.pack(">iib", 600, 600, 3), struct.pack(">iib", 300, 300, 3)]
    assert raster.pwg_choice(res, ["sgray_8", "srgb_8"]) == {"dpi": 300, "color": False}
    assert raster.pwg_choice(res[:1], ["srgb_8"]) == {"dpi": 600, "color": True}
    assert raster.pwg_choice(res, ["black_1"]) is None
    assert raster.pwg_choice([struct.pack(">iib", 360, 360, 3)], ["sgray_8"]) is None
    assert raster.urf_choice(["V1.4", "W8", "RS300-600"]) == {"dpi": 300, "color": False}
    assert raster.urf_choice(["V1.4,SRGB24,RS600"]) == {"dpi": 600, "color": True}
    assert raster.urf_choice(["W8"]) is None
    # JPEG still wins; a raster format only when nothing better is offered
    attrs = {"urf-supported": ["W8", "RS300"]}
    assert ipp.choose_format(["image/urf", "image/jpeg"], attrs) == ("image/jpeg", None)
    assert ipp.choose_format(["image/urf"], attrs) == ("image/urf", {"dpi": 300, "color": False})
    assert ipp.choose_format(["image/urf"], {}) == (None, None)


@pytest.mark.parametrize("fmt", ["image/pwg-raster", "image/urf"])
def test_raster_only_printer_gets_a_page(fmt, tmp_path, monkeypatch):
    from tests.test_printing import svc_for
    from core import paper
    w = paper.build(paper.random_key(), "signet")
    d = tmp_path / "paper"
    paper.save(str(d), w, with_pdf=False)
    p = FakePrinter(formats=(fmt, "application/octet-stream"))
    try:
        svc = svc_for(str(d), tmp_path)
        svc.trust("127.0.0.1", p.port, svc.check("127.0.0.1", p.port)["fingerprint"])
        svc.print(w["address"], "127.0.0.1", p.port)
        job = p.jobs[0]
        assert job["format"] == fmt
        assert job["data"][:4] == (b"RaS2" if fmt.endswith("pwg-raster") else b"UNIR")
    finally:
        p.close()


def test_a_strict_printer_is_asked_properly_canon_ts6500():
    """A Canon TS6500 answered only the first of our questions, because each
    value of requested-attributes repeated the name. The list must be one
    attribute; and a printer with only PWG/URF must then get a page."""
    from core import ipp
    req = ipp.encode_request(ipp.GET_PRINTER_ATTRIBUTES, 1, "ipps://10.0.0.5:631/ipp/print",
                             ipp.one_set(ipp.KEYWORD, "requested-attributes", ipp.WANTED))
    from tools.fake_printer import requested
    assert requested(req) == ipp.WANTED
    p = FakePrinter(formats=("application/octet-stream", "image/urf", "image/pwg-raster"),
                    model="Canon TS6500 series")
    try:
        info = ipp.get_attributes("127.0.0.1", p.port)
        assert info["model"] == "Canon TS6500 series" and info["state"] == "IDLE"
        assert info["format"] == "image/pwg-raster" and info["raster"]["dpi"] == 300
    finally:
        p.close()


def test_the_old_way_of_asking_fails_on_a_strict_printer():
    """Proof the fake printer is strict enough to have caught it."""
    from core import ipp
    from tools.fake_printer import requested
    req = ipp.encode_request(ipp.GET_PRINTER_ATTRIBUTES, 1, "ipps://10.0.0.5:631/ipp/print",
                             [(ipp.KEYWORD, "requested-attributes", n) for n in ipp.WANTED])
    assert requested(req) == ["printer-make-and-model"]


def test_a_printer_that_only_answers_all_still_lists_its_formats():
    from core import ipp
    p = FakePrinter(formats=("image/urf",), only_all=True)
    try:
        info = ipp.get_attributes("127.0.0.1", p.port)
        assert info["format"] == "image/urf" and info["raster"] == {"dpi": 300, "color": False}
    finally:
        p.close()


def test_decode_survives_collections():
    """An "all" reply has collections (media-col and friends) between the
    attributes we read; they mustn't break or pollute them."""
    from core import ipp
    body = bytes([1, 1, 0, 0, 0, 0, 0, 1, ipp.PRINTER])
    body += ipp._attr(ipp.MIME, "document-format-supported", "image/urf")
    body += ipp._attr(0x34, "media-col-default", b"")             # begCollection
    body += ipp._attr(0x4A, "", "media-size")                      # memberAttrName
    body += ipp._attr(0x34, "", b"")
    body += ipp._attr(0x4A, "", "x-dimension")
    body += ipp._attr(ipp.INTEGER if hasattr(ipp, "INTEGER") else 0x21, "", 21590)
    body += ipp._attr(0x37, "", b"")                                # endCollection
    body += ipp._attr(0x37, "", b"")
    body += ipp._attr(ipp.KEYWORD, "urf-supported", "W8")
    body += ipp._attr(ipp.KEYWORD, "", "RS300")
    body += bytes([ipp.END])
    status, attrs = ipp.decode_response(body)
    assert attrs["document-format-supported"] == ["image/urf"]
    assert attrs["urf-supported"] == ["W8", "RS300"]
