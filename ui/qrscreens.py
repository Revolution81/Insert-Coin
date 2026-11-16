"""
Camera viewfinder and animated QR screens.
"""
from __future__ import annotations

import queue
import time
from typing import Callable

from PIL import Image, ImageOps

from core.qr import Collector, Scanned
from . import theme as t
from .camera import CameraError, Scanner  # noqa: F401 - Scanner: type hint
from .input import Kind
from .theme import BODY, H, PAL, TINY, W
from .widgets import Screen, wrap


class ScanScreen(Screen):
    """Point the camera at a QR (or an animated BBQr). Calls
    on_result(app, Scanned) once something complete is read."""
    animated = True

    def __init__(self, title: str, on_result: Callable, accept: tuple[str, ...] = (),
                 hint: str = "POINT AT A QR CODE", tab: tuple[str, Callable] | None = None):
        self.tab = tab                   # (label, fn(app)): TAB offers another way in
        self.title = title
        self.on_result = on_result
        self.accept = accept
        self.hint = hint
        self.collector = Collector()
        self.scanner: Scanner | None = None
        self._texts: queue.Queue = queue.Queue()
        self.error = ""
        self._done = False

    # ---------------------------------------------------------- lifecycle
    def on_show(self, app):
        if self.scanner is not None or self._done:
            return
        try:
            self.scanner = app.open_scanner(self._texts.put)
        except CameraError as e:
            self.error = str(e)[:60]
            return
        if not app.sync_jobs:
            self.scanner.start()

    def on_hide(self, app):
        if self.scanner:
            self.scanner.stop()
            self.scanner = None

    def poll(self, app) -> bool:
        """Called by the app loop: move decoded texts into the collector."""
        if self._done or self.scanner is None:
            return False
        if app.sync_jobs:                       # tests: scan one frame per poll
            self.scanner.step()
        changed = False
        if self.scanner.error and self.scanner.error != self.error:
            self.error = self.scanner.error
            changed = True
        while not self._texts.empty():
            changed |= self.collector.add(self._texts.get())
        res = self.collector.result
        if res is not None:
            if self.accept and res.kind not in self.accept:
                self.error = f"That's a {res.kind}, not a {' or '.join(self.accept)}"
                self.collector = Collector()
                return True
            self._done = True
            app.pop()                           # stops the camera via on_hide
            self.on_result(app, res)
            return True
        if self.collector.error:
            self.error = self.collector.error[:60]
            self.collector.error = ""
        return changed

    def handle(self, ev, app):
        if ev.kind in (Kind.BACK, Kind.HOME):
            app.pop()
        elif ev.kind is Kind.TAB and self.tab:
            app.pop()                           # camera off
            self.tab[1](app)

    # -------------------------------------------------------------- draw
    def render(self, app, frame):
        img, d = t.new_canvas()
        t.sky(img, d, hills=False)
        app.status_bar(img, d, self)
        t.window(d, (4, 18, 150, H - 6), self.title)
        vx, vy, vw, vh = 11, 30, 132, 99
        d.rectangle((vx - 1, vy - 1, vx + vw, vy + vh), fill=PAL["ink"])
        prev = self.scanner.preview if self.scanner else None
        if prev is not None:
            # posterized, blue-tinted feed: reads as "game camera" and hides detail
            g = prev.convert("L").point(lambda v: (v // 64) * 85)
            tinted = ImageOps.colorize(g, black=(10, 12, 40), white=(200, 220, 255))
            img.paste(tinted, (vx, vy))
        # gold corner brackets
        for (cx, cy, sx, sy) in ((vx + 8, vy + 8, 1, 1), (vx + vw - 9, vy + 8, -1, 1),
                                 (vx + 8, vy + vh - 9, 1, -1), (vx + vw - 9, vy + vh - 9, -1, -1)):
            d.line((cx, cy, cx + 12 * sx, cy), fill=PAL["gold"], width=2)
            d.line((cx, cy, cx, cy + 12 * sy), fill=PAL["gold"], width=2)
        if frame % 2 == 0 and prev is not None:
            d.line((vx + 4, vy + vh // 2, vx + vw - 5, vy + vh // 2), fill=PAL["red"])

        t.window(d, (156, 18, W - 5, H - 6))
        x = 166
        if self.error:
            for i, line in enumerate(wrap(self.error, 18)[:4]):
                t.text(d, (x, 32 + i * 11), line, TINY, "red")
        else:
            for i, line in enumerate(wrap(self.hint, 16)[:3]):
                t.text(d, (x, 32 + i * 12), line, BODY, "light")
        got, total = self.collector.progress
        if total > 1:
            t.text(d, (x, 86), f"PART {got}/{total}", BODY, "gold")
            t.bar(d, (x, 100, W - 16, 108), got / total, "gold")
        elif self.scanner and not self.error:
            t.text(d, (x, 86), "SCANNING" + "." * (int(time.monotonic() * 3) % 4), BODY, "cyan")
        if self.tab:
            t.text(d, (x, 128), f"TAB {self.tab[0]}", TINY, "gold")
        t.text(d, (x, 140), "HOME CANCEL", TINY, "grey")
        return img


class AnimatedQRScreen(Screen):
    """Shows one QR, or loops through BBQr frames for another app to scan."""
    animated = True

    def __init__(self, title: str, frames: list[str], lines: list[str] | None = None,
                 then: Callable | None = None, fps: float = 3.0,
                 views: list[tuple] | None = None):
        """views: optional [(tab_label, title, frames, lines), ...] that TAB
        cycles through (e.g. descriptor vs xpub)."""
        self.views = views or [("", title, frames, lines or [])]
        self.view = 0
        self.then = then
        self.fps = fps
        self._cache: dict[int, Image.Image] = {}
        self._load()

    def _load(self):
        _, self.title, self.frames, self.lines = self.views[self.view]
        self._cache.clear()

    def _qr_img(self, i: int) -> Image.Image:
        if i not in self._cache:
            import qrcode
            q = qrcode.QRCode(border=0, error_correction=qrcode.constants.ERROR_CORRECT_L)
            q.add_data(self.frames[i])
            q.make(fit=True)
            m = q.get_matrix()
            n = len(m)
            mod = max(1, min(4, 150 // (n + 4)))
            size = (n + 4) * mod
            im = Image.new("RGB", (size, size), PAL["white"])
            px = im.load()
            for y, row in enumerate(m):
                for x, on in enumerate(row):
                    if on:
                        for dy in range(mod):
                            for dx in range(mod):
                                px[(x + 2) * mod + dx, (y + 2) * mod + dy] = PAL["ink"]
            self._cache[i] = im
        return self._cache[i]

    @property
    def index(self) -> int:
        return int(time.monotonic() * self.fps) % len(self.frames)

    def handle(self, ev, app):
        if ev.kind is Kind.TAB or (ev.kind is Kind.NAV and ev.value in ("LEFT", "RIGHT")):
            if len(self.views) > 1:
                self.view = (self.view + 1) % len(self.views)
                self._load()
        elif ev.kind in (Kind.SELECT, Kind.BACK, Kind.HOME):
            app.pop()
            if self.then:
                self.then(app)

    def render(self, app, frame):
        img, d = t.new_canvas()
        t.sky(img, d, hills=False)
        app.status_bar(img, d, self)
        i = self.index
        q = self._qr_img(i)
        top = 14 + max(0, (H - 14 - q.height) // 2)
        img.paste(q, (6, top))
        x0 = q.width + 12
        t.window(d, (x0, 18, W - 5, H - 6), self.title)
        x = x0 + 10
        y = 32
        for line in self.lines:
            for part in wrap(line, max(10, (W - 5 - x - 8) // 8)):
                t.text(d, (x, y), part, BODY, "light")
                y += 11
        if len(self.frames) > 1:
            t.text(d, (x, 118), f"PART {i + 1}/{len(self.frames)}", BODY, "gold")
            t.bar(d, (x, 130, W - 16, 136), (i + 1) / len(self.frames), "gold", segments=False)
        nxt = self.views[(self.view + 1) % len(self.views)][0]
        hint = getattr(self, "done_hint", "OK DONE") + (
            f"  < > {nxt}" if len(self.views) > 1 else "")
        t.text(d, (x, 146), hint, TINY, "grey")
        return img


__all__ = ["ScanScreen", "AnimatedQRScreen", "Scanned"]


class SeedQRScreen(Screen):
    """The seed as a SeedQR, big, with a guide line every 5 modules so it
    can be copied by hand onto a SeedQR template (or graph paper)."""

    def __init__(self, digits: str, words: int):
        import qrcode
        q = qrcode.QRCode(border=0, error_correction=qrcode.constants.ERROR_CORRECT_L,
                          version=2 if words == 12 else 3)
        q.add_data(digits)
        q.make(fit=False)
        self.matrix = q.get_matrix()
        self.words = words
        self.title = "SEEDQR"

    def handle(self, ev, app):
        if ev.kind in (Kind.SELECT, Kind.BACK, Kind.HOME):
            self.matrix = []
            app.pop()

    def render(self, app, frame):
        img, d = t.new_canvas()
        t.sky(img, d, hills=False)
        app.status_bar(img, d, self)
        n = len(self.matrix)
        mod = min(5, (H - 18) // (n + 2))
        x0, y0 = 8, 14 + ((H - 14) - (n + 2) * mod) // 2
        size = (n + 2) * mod
        d.rectangle((x0, y0, x0 + size - 1, y0 + size - 1), fill=PAL["white"])
        for y, row in enumerate(self.matrix):
            for x, on in enumerate(row):
                if on:
                    px, py = x0 + (x + 1) * mod, y0 + (y + 1) * mod
                    d.rectangle((px, py, px + mod - 1, py + mod - 1), fill=PAL["ink"])
        for k in range(0, n + 1, 5):           # 5x5 guide grid
            gx = x0 + (k + 1) * mod
            d.line((gx, y0 + mod, gx, y0 + (n + 1) * mod), fill=PAL["red"])
            d.line((x0 + mod, gx - x0 + y0, x0 + (n + 1) * mod, gx - x0 + y0), fill=PAL["red"])
        bx = x0 + size + 6
        t.window(d, (bx, 18, W - 5, H - 6), "SEEDQR", "red")
        for i, line in enumerate([f"{self.words} words,", f"{n}x{n} grid.", "", "Copy it onto a",
                                  "SeedQR card. It", "IS your seed:", "keep it secret.", "",
                                  "Red lines = every", "5 squares."]):
            t.text(d, (bx + 10, 32 + i * 11), line, TINY if i > 7 else BODY,
                   "red" if i in (5, 6) else "light")
        t.text(d, (bx + 10, H - 22), "OK DONE", TINY, "grey")
        return img
