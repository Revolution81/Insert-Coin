"""
Reusable screens: menus, messages, yes/no, text and PIN entry, word entry,
busy spinner. Flow code (ui/flow.py) strings these together.

Every screen has:
  mode                Mode.MENU or Mode.TEXT (the app sets the translator)
  handle(ev, app)     react to one input event
  render(app, frame)  return a 320x170 image
"""
from __future__ import annotations

import time
from typing import Callable

from . import theme as t
from .input import Event, Kind, Mode, TextBuffer
from .theme import BIG, BODY, H, PAL, TINY, W, WORD


class Screen:
    mode = Mode.MENU
    title = ""

    def handle(self, ev: Event, app) -> None:
        if ev.kind is Kind.BACK:
            app.pop()
        elif ev.kind is Kind.HOME:
            app.home()

    def render(self, app, frame: int):
        img, d = t.new_canvas()
        t.sky(img, d)
        app.status_bar(img, d, self)
        return img

    def on_show(self, app) -> None:
        pass


def groups(s: str, n: int = 4) -> list[str]:
    return [s[i:i + n] for i in range(0, len(s), n)]


def short_addr(a: str) -> str:
    return a if len(a) <= 18 else f"{a[:8]}...{a[-6:]}"


def fmt_sats(n: int, sign: bool = False) -> str:
    s = f"{abs(n):,}"
    if sign:
        s = ("+" if n >= 0 else "-") + s
    elif n < 0:
        s = "-" + s
    return s


def fmt_btc(n: int) -> str:
    return f"{n / 1e8:.8f}"


def wrap(text: str, cols: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > cols:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        lines.append(cur)
    return lines


def wrap_px(text: str, max_px: int, font=BODY) -> list[str]:
    """Wrap by real pixel width (the font isn't monospaced). A word too long
    for a whole line (an address, a key) is cut where it overflows."""
    lines, cur = [], ""
    for word in text.split():
        while font.width(word) > max_px:              # a very long word: cut it
            if cur:
                lines.append(cur)
                cur = ""
            n = len(word)
            while n > 1 and font.width(word[:n]) > max_px:
                n -= 1
            lines.append(word[:n])
            word = word[n:]
        trial = f"{cur} {word}" if cur else word
        if cur and font.width(trial) > max_px:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def paginate(lines: list[str], max_px: int, rows: int, font=BODY) -> list[list[str]]:
    """Lines wrapped to max_px, cut into pages of at most rows rows. A page
    break goes at a blank line when one is near, so a paragraph isn't split
    in the middle, and no page starts with a blank row."""
    # A paragraph (lines between blank ones) with a line too long for the
    # window is reflowed as one text, so it doesn't leave one-word orphans;
    # paragraphs that fit keep their line breaks as written.
    flat: list[str] = []
    para: list[str] = []

    def flush():
        if any(font.width(x) > max_px for x in para):
            flat.extend(wrap_px(" ".join(para), max_px, font))
        else:
            flat.extend(para)
        para.clear()
    for line in lines:
        if line.strip():
            para.append(line)
        else:
            flush()
            flat.append("")
    flush()
    rows = max(1, rows)
    pages: list[list[str]] = []
    while flat:
        while flat and flat[0] == "" and pages:      # no blank row at a page's top
            flat.pop(0)
        if not flat:
            break
        if len(flat) <= rows:
            pages.append(flat)
            break
        cut = rows
        for i in range(rows, rows // 2, -1):         # the last blank line in the bottom half
            if flat[i] == "" if i < len(flat) else False:
                cut = i
                break
        pages.append(flat[:cut])
        flat = flat[cut:]
    while pages and pages[-1] and pages[-1][-1] == "":
        pages[-1].pop()
    return pages or [[]]


def page_hint(d, x: int, y: int, page: int, pages: int, keys: str = "< >") -> None:
    if pages > 1:
        t.text(d, (x, y), f"{keys} PAGE {page + 1}/{pages}", TINY, "gold")


# ---------------------------------------------------------------------------
class MenuScreen(Screen):
    """List of (label, action[, right_text, right_color[, adjust]]) items.

    adjust(app, step) makes an item a setting you change in place: LEFT and
    RIGHT call it with -1/+1 (OK with +1 when there's no action), the cursor
    stays put, and the menu rebuilds its items with refresh() to show the new
    value. Items without adjust ignore LEFT/RIGHT."""

    def __init__(self, title: str, items, info: list[str] | None = None,
                 back: bool = True, selected: int = 0):
        self.title = title
        self.refresh = items if callable(items) else None
        self.items = items() if callable(items) else items
        self.info = info or []
        self.back = back
        self.sel = selected

    def on_show(self, app):
        if self.refresh is not None:       # coming back here: show current values
            self.items = self.refresh()

    def quiet_for(self, ev) -> bool:
        """No menu click when LEFT/RIGHT changes a setting that plays its own
        sound (SETUP › SOUND plays the coin sound you land on)."""
        if ev.kind is not Kind.NAV or ev.value not in ("LEFT", "RIGHT") or not self.items:
            return False
        it = self.items[min(max(self.sel, 0), len(self.items) - 1)]
        return len(it) > 4 and bool(getattr(it[4], "plays_sound", False))

    def _adjust(self, app, step):
        it = self.items[self.sel]
        it[4](app, step)
        if self.refresh is not None and app.top is self:
            self.items = self.refresh()

    def handle(self, ev, app):
        n = len(self.items)
        self.sel = min(max(self.sel, 0), max(n - 1, 0))   # the list may have shrunk
        adjustable = n and len(self.items[self.sel]) > 4 and self.items[self.sel][4]
        if ev.kind is Kind.NAV and adjustable and ev.value in ("LEFT", "RIGHT"):
            self._adjust(app, 1 if ev.value == "RIGHT" else -1)
        elif ev.kind is Kind.SELECT and adjustable and not self.items[self.sel][1]:
            self._adjust(app, 1)
        elif ev.kind is Kind.NAV and n:
            if ev.value == "UP":                    # LEFT/RIGHT only change settings
                self.sel = (self.sel - 1) % n
            elif ev.value == "DOWN":
                self.sel = (self.sel + 1) % n
            elif ev.value == "PAGE_UP":
                self.sel = max(0, self.sel - 6)
            elif ev.value == "PAGE_DOWN":
                self.sel = min(n - 1, self.sel + 6)
        elif ev.kind is Kind.SELECT and n:
            action = self.items[self.sel][1]
            if action:
                action(app)
        elif ev.kind is Kind.HOTKEY:
            for i, it in enumerate(self.items):
                if it[0].upper().startswith(ev.value):
                    self.sel = i
                    break
        elif ev.kind is Kind.BACK and not self.back:
            return
        else:
            super().handle(ev, app)

    def render(self, app, frame):
        img, d = t.new_canvas()
        t.sky(img, d)
        app.status_bar(img, d, self)
        top = 22
        info = [p for line in self.info for p in (wrap_px(line, W - 18) or [""])]
        for i, line in enumerate(info):
            t.text(d, (10, 18 + i * 11), line, BODY, "light")
        if info:
            top = 22 + len(info) * 11
        rows = []
        for i, it in enumerate(self.items):
            right = it[2] if len(it) > 2 else ""
            if i == self.sel and len(it) > 4 and it[4]:
                right = f"< {right} >"        # LEFT/RIGHT change this one
            rows.append((it[0], right, it[3] if len(it) > 3 else None))
        t.list_view(img, d, (6, top, W - 7, H - 6), rows, self.sel, frame, self.title)
        return img


class MessageScreen(Screen):
    """A message in a window. Too long for one window, it turns into pages:
    LEFT/RIGHT (or UP/DOWN) flip them, OK goes to the next page and closes
    on the last one."""
    X0, Y0, LINE = 16, 36, 12

    def __init__(self, title: str, lines: list[str], then: Callable | None = None,
                 border: str = "light", big: str | None = None):
        self.title = title
        self.lines = lines
        self.then = then
        self.border = border
        self.big = big
        self.page = 0
        self._pages = None

    def pages(self) -> list[list[str]]:
        if self._pages is None:
            top = self.Y0 + (26 if self.big else 0)
            rows = (H - 26 - top) // self.LINE
            self._pages = paginate(self.lines, W - 7 - self.X0 - 8, rows)
        return self._pages

    def handle(self, ev, app):
        n = len(self.pages())
        if ev.kind is Kind.NAV and n > 1:
            step = -1 if ev.value in ("LEFT", "UP") else 1
            self.page = min(max(0, self.page + step), n - 1)
        elif ev.kind is Kind.SELECT and self.page < n - 1:
            self.page += 1
        elif ev.kind in (Kind.SELECT, Kind.BACK, Kind.HOME):
            app.pop()
            if self.then:
                self.then(app)

    def render(self, app, frame):
        img, d = t.new_canvas()
        t.sky(img, d)
        app.status_bar(img, d, self)
        t.window(d, (6, 22, W - 7, H - 8), self.title, self.border)
        y = self.Y0
        if self.big:
            t.text(d, (W // 2, y), self.big, BIG, "gold", align="center")
            y += 26
        pages = self.pages()
        self.page = min(self.page, len(pages) - 1)
        for part in pages[self.page]:
            t.text(d, (self.X0, y), part, BODY, "light")
            y += self.LINE
        page_hint(d, self.X0, H - 22, self.page, len(pages))
        if frame % 2 == 0:
            last = self.page == len(pages) - 1
            t.text(d, (W - 18, H - 22), "OK" if last else "OK NEXT", TINY, "gold", align="right")
        return img


class ConfirmScreen(Screen):
    """Yes/No with the cursor starting on NO."""

    def __init__(self, title: str, lines: list[str], on_yes: Callable,
                 danger: bool = False, yes: str = "YES", no: str = "NO"):
        self.title = title
        self.lines = lines
        self.on_yes = on_yes
        self.danger = danger
        self.labels = [yes, no]
        self.sel = 1

    def handle(self, ev, app):
        if ev.kind is Kind.NAV and ev.value in ("UP", "DOWN"):
            self.page = min(max(0, getattr(self, "page", 0) + (-1 if ev.value == "UP" else 1)),
                            getattr(self, "_npages", 1) - 1)
        elif ev.kind is Kind.NAV:
            self.sel ^= 1
        elif ev.kind is Kind.SELECT:
            if self.sel == 0:
                app.pop()
                self.on_yes(app)
            else:
                app.pop()
        else:
            super().handle(ev, app)

    def render(self, app, frame):
        img, d = t.new_canvas()
        t.sky(img, d)
        app.status_bar(img, d, self)
        t.window(d, (6, 22, W - 7, 118), self.title, "red" if self.danger else "gold")
        pages = paginate(self.lines, W - 7 - 16 - 8, (118 - 36 - 4) // 12)
        self.page = min(getattr(self, "page", 0), len(pages) - 1)
        y = 36
        if len(pages) > 1:                           # room for the page hint
            pages = paginate(self.lines, W - 7 - 16 - 8, (118 - 36 - 14) // 12)
            self.page = min(self.page, len(pages) - 1)
            page_hint(d, 16, 104, self.page, len(pages), "^ v")
        self._npages = len(pages)
        for part in pages[self.page]:
            t.text(d, (16, y), part, BODY, "light")
            y += 12
        lw = max(len(x) for x in self.labels)
        bw = max(113, 2 * (lw * 8 + 28) + 16)
        t.menu(img, d, (W - 7 - bw, 124, W - 7, H - 4), self.labels, self.sel,
               cols=2, frame=frame)
        return img


class BusyScreen(Screen):
    """Shown while a background job runs; ignores input."""

    def __init__(self, label: str):
        self.title = label

    def handle(self, ev, app):
        pass

    def render(self, app, frame):
        img, d = t.new_canvas()
        t.sky(img, d)
        app.status_bar(img, d, self)
        t.window(d, (40, 50, W - 41, 120))
        t.text(d, (W // 2, 66), self.title, BODY, "white", align="center")
        # marching bar
        x0, x1 = 60, W - 61
        pos = int(time.monotonic() * 120) % (x1 - x0 + 40) - 40
        d.rectangle((x0, 88, x1, 98), fill=PAL["ink"])
        d.rectangle((x0 + 1, 89, x1 - 1, 97), fill=PAL["bar_bg"])
        a, b = max(x0 + 1, x0 + pos), min(x1 - 1, x0 + pos + 40)
        if b > a:
            d.rectangle((a, 89, b, 97), fill=PAL["gold"])
        return img


class TextInputScreen(Screen):
    mode = Mode.TEXT

    def __init__(self, title: str, prompt: str, on_submit: Callable,
                 masked: bool = False, allowed: str | None = None, max_len: int = 100,
                 initial: str = "", info: Callable | None = None,
                 tab: tuple[str, Callable] | None = None, empty_ok: bool = False):
        self.title = title
        self.prompt = prompt
        self.on_submit = on_submit      # (app, text) -> None, or an error string
        self.masked = masked
        self.can_mask = masked
        self.allowed = allowed
        self.buf = TextBuffer(initial, max_len)
        self.info = info                # (text) -> str shown under the field
        self.tab = tab                  # (label, fn(app, screen))
        self.empty_ok = empty_ok
        self.error = ""
        self.cancellable = True

    def handle(self, ev, app):
        if ev.kind is Kind.CHAR:
            if self.allowed is not None and ev.value not in self.allowed:
                return
            self.error = ""
            self.buf.apply(ev)
        elif ev.kind in (Kind.BACKSPACE, Kind.CURSOR):
            self.error = ""
            self.buf.apply(ev)
        elif ev.kind is Kind.TAB:
            if self.tab:
                self.tab[1](app, self)
            elif self.can_mask:
                self.masked = not self.masked
        elif ev.kind is Kind.SUBMIT:
            if not self.buf.text and not self.empty_ok:
                return
            err = self.on_submit(app, self.buf.text)
            if err:
                self.error = err
        elif ev.kind is Kind.CANCEL and self.cancellable:
            self.buf.wipe()
            app.pop()

    def render(self, app, frame):
        img, d = t.new_canvas()
        t.sky(img, d, hills=False)
        app.status_bar(img, d, self)
        t.window(d, (6, 22, W - 7, 122), self.title)
        t.text(d, (18, 36), self.prompt, BODY, "light")
        t.text_field(d, (16, 50, W - 17, 68), self.buf.text, self.buf.pos, frame,
                     masked=self.masked, peek=True)
        y = 78
        if self.info:
            for part in wrap(self.info(self.buf.text), 36)[:2]:
                t.text(d, (18, y), part, BODY, "gold")
                y += 12
        if self.error:
            t.text(d, (18, 106), self.error[:40], TINY, "red")
        hints = [("OK", "DONE"), ("HOME", "CANCEL")]
        if self.tab:
            hints.append(("TAB", self.tab[0]))
        elif self.can_mask:
            hints.append(("TAB", "SHOW" if self.masked else "HIDE"))
        x = 10
        for key, label in hints:
            x = t.text(d, (x, 132), key, TINY, "gold") + 4
            x = t.text(d, (x, 132), label, TINY, "grey") + 14
        return img


class PinScreen(TextInputScreen):
    """PIN entry: gems instead of a text field, hearts for tries left."""

    def __init__(self, title: str, on_pin: Callable, prompt: str = "ENTER PIN",
                 tries: tuple[int, int] | None = None, min_len: int = 4):
        super().__init__(title, prompt, self._submit, masked=True, max_len=16)
        self.on_pin = on_pin
        self.tries = tries               # (left, max)
        self.min_len = min_len

    def _submit(self, app, text):
        if len(text) < self.min_len:
            return f"At least {self.min_len} characters"
        pin = text
        self.buf.wipe()
        return self.on_pin(app, pin)

    def render(self, app, frame):
        img, d = t.new_canvas()
        t.sky(img, d)
        app.status_bar(img, d, self)
        t.window(d, (40, 22, W - 41, 128), self.title)
        t.text(d, (W // 2, 36), self.prompt, BIG if len(self.prompt) <= 12 else BODY,
               "white", align="center")
        n = len(self.buf.text)
        slots = max(6, min(8, n + (1 if n >= 6 else 0)))
        gx = W // 2 - (slots * 20 - 4) // 2
        t.gems(img, (gx, 60), slots, min(n, slots))
        if n > 8:
            t.text(d, (W - 54, 64), f"+{n - 8}", BODY, "light")
        if self.tries:
            left, mx = self.tries
            t.text(d, (60, 90), "TRIES", BODY, "grey")
            t.hearts(img, (106, 89), mx, left)
        if self.error:
            t.text(d, (W // 2, 110), self.error[:40], TINY, "red", align="center")
        t.text(d, (W // 2, 142), "OK DONE   DEL ERASE   HOME BACK", TINY, "grey",
               align="center")
        return img


class WordEntryScreen(Screen):
    """Type BIP39 words with autocomplete. positions: 1-based word numbers."""
    mode = Mode.TEXT

    # Typing 12 or 24 words takes a while (looking at the paper, checking
    # spelling): a prompt left alone cancels itself, but not after 2 minutes
    idle_s = 600

    def __init__(self, title: str, positions: list[int], on_done: Callable,
                 initial: list[str] | None = None, complete: Callable | None = None):
        """complete: prefix -> matching words (default: the BIP39 list)."""
        if complete is None:
            from core.seed import completions as complete
        self._complete = complete
        self.title = title
        self.positions = positions
        self.words = list(initial or [])
        self.on_done = on_done            # (app, words) -> None or error str
        self.buf = TextBuffer(max_len=8)
        self.error = ""

    @property
    def idx(self):
        return len(self.words)

    def handle(self, ev, app):
        # A word is taken automatically only when it's typed in full and no
        # longer word starts with it ("act" waits: "action" might follow).
        # Otherwise SPACE, OK or TAB takes the exact word or the only match.
        # The last word always waits, so a stray key can't hit the next screen.
        if ev.kind is Kind.CHAR and ev.value.isalpha():
            self.buf.apply(Event(Kind.CHAR, ev.value.lower()))
            self.error = ""
            opts = self._complete(self.buf.text, 2)
            last = self.idx == len(self.positions) - 1
            if opts == [self.buf.text] and not last:
                self._accept(app, self.buf.text)
            return
        if ev.kind is Kind.BACKSPACE:
            if not self.buf.text and self.words:
                prev = self.words.pop()
                self.buf = TextBuffer(prev, 8)
            else:
                self.buf.apply(ev)
            self.error = ""
        elif ev.kind in (Kind.SUBMIT, Kind.TAB) or (ev.kind is Kind.CHAR and ev.value == " "):
            if not self.buf.text:
                return                          # e.g. a space after an auto-taken word
            opts = self._complete(self.buf.text)
            if self.buf.text in opts:
                self._accept(app, self.buf.text)
            elif len(opts) == 1 or (opts and ev.kind is Kind.TAB):
                self._accept(app, opts[0])
            else:
                self.error = "Not a seed word" if not opts else "More letters, or TAB"
        elif ev.kind is Kind.CANCEL:
            self.words.clear()
            app.pop()

    def _accept(self, app, word):
        self.words.append(word)
        self.buf = TextBuffer(max_len=8)
        if len(self.words) == len(self.positions):
            err = self.on_done(app, list(self.words))
            if err:
                self.error = err
                self.words.pop()

    def render(self, app, frame):
        img, d = t.new_canvas()
        t.sky(img, d, hills=False)
        app.status_bar(img, d, self)
        t.window(d, (6, 22, W - 7, H - 6), self.title)
        i = min(self.idx, len(self.positions) - 1)
        t.text(d, (18, 36), f"WORD #{self.positions[i]}", BIG, "white")
        t.text(d, (W - 18, 42), f"{self.idx + 1}/{len(self.positions)}", BODY, "grey",
               align="right")
        t.text_field(d, (16, 58, 170, 76), self.buf.text, self.buf.pos, frame)
        opts = self._complete(self.buf.text) if self.buf.text else []
        for n, w in enumerate(opts[:4]):
            t.text(d, (184 + (n % 2) * 64, 58 + (n // 2) * 12), w, BODY,
                   "gold" if n == 0 else "grey")
        t.bar(d, (18, 86, W - 19, 92), self.idx / len(self.positions), "gold",
              segments=False)
        if self.words:
            prev = " ".join(self.words[-3:])
            t.text(d, (18, 100), ("... " if len(self.words) > 3 else "") + prev, TINY, "grey")
        if self.error:
            t.text(d, (18, 112), self.error, TINY, "red")
        t.text(d, (18, 136), "SPACE NEXT  TAB 1ST MATCH  DEL BACK", TINY, "grey")
        return img


class ShowWordsScreen(Screen):
    """Seed words, four per page. Left/right flip pages; OK on the last page
    continues. Nothing else about the words is ever shown or stored."""

    def __init__(self, words: list[str], on_done: Callable | None, title="WRITE DOWN"):
        self.words = words
        self.on_done = on_done
        self.title = title
        self.page = 0

    @property
    def pages(self):
        return (len(self.words) + 3) // 4

    def handle(self, ev, app):
        if ev.kind is Kind.NAV and ev.value in ("RIGHT", "DOWN"):
            self.page = min(self.pages - 1, self.page + 1)
        elif ev.kind is Kind.NAV and ev.value in ("LEFT", "UP"):
            self.page = max(0, self.page - 1)
        elif ev.kind is Kind.SELECT:
            if self.page < self.pages - 1:
                self.page += 1
            else:
                app.pop()
                if self.on_done:
                    self.on_done(app)
        elif ev.kind in (Kind.BACK, Kind.HOME):
            if self.page > 0 and ev.kind is Kind.BACK:
                self.page -= 1
            else:
                self.words = []
                app.pop()

    def render(self, app, frame):
        img, d = t.new_canvas()
        t.sky(img, d, hills=False)
        app.status_bar(img, d, self)
        t.window(d, (6, 22, W - 7, 128), f"{self.title} {self.page + 1}/{self.pages}", "gold")
        for k in range(4):
            n = self.page * 4 + k
            if n >= len(self.words):
                break
            x = 22 + (k % 2) * 146
            y = 40 + (k // 2) * 40
            t.text(d, (x, y + 4), f"{n + 1:>2}", BODY, "gold")
            t.text(d, (x + 24, y), self.words[n].upper(), WORD, "white")
        t.text(d, (W // 2, 138), "Paper only. Never photo or type it.", TINY, "red",
               align="center")
        last = self.page == self.pages - 1
        t.text(d, (W // 2, 152), "ZC FLIP   OK " + ("DONE" if last else "NEXT"), TINY,
               "grey", align="center")
        return img
