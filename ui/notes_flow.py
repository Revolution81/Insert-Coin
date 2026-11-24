"""
SETUP › TOOLS › NOTES & PASSWORDS, on the signer's screens only.

The app asks for the prompt and gets nothing back: every title, note and
password is read, typed and shown here, on Sir Signer's side. BIP85
PASSWORD derives a password from the seed instead (same number, same
password, from the seed words alone).
"""
from __future__ import annotations

from core import notes as nl

from . import theme as t
from .input import Kind
from .theme import BODY, H, TINY, W
from .widgets import ConfirmScreen, MenuScreen, MessageScreen, Screen, TextInputScreen, wrap

def notes(c):
    """The main list. Rebuilt from the signer every time it's shown."""
    def items():
        rows = []
        for i, e in enumerate(c.signer.notes()):
            kind = "PW" if e["kind"] == "password" else "NOTE"
            rows.append((e["title"][:20], (lambda i: lambda c: open_entry(c, i))(i), kind,
                         "gold" if kind == "PW" else "cyan"))
        rows += [("+ NEW NOTE", lambda c: edit_note(c)),
                 ("+ NEW PASSWORD", lambda c: edit_password(c)),
                 ("BIP85 PASSWORD", bip85_menu, "FROM SEED", "grey"),
                 ("DONE", lambda c: c.finish(None))]
        return rows
    c.push(MenuScreen("NOTES & PASSWORDS", items,
                      info=["Kept by Sir Signer, encrypted", "with your seed. In card backups."]))


def open_entry(c, index):
    e = c.signer.notes()[index]

    def delete(c):
        def yes(c):
            c.signer.note_delete(index)
            c.pop()                                   # the entry's menu
        c.push(ConfirmScreen("DELETE?", [f"\"{e['title'][:24]}\"", "", "Gone for good (card backups",
                                         "made before keep a copy)."], yes, danger=True,
                             yes="DELETE"))

    view = (lambda c: c.push(PasswordScreen(e))) if e["kind"] == "password" else \
        (lambda c: c.push(NoteScreen(e)))
    edit = (lambda c: edit_password(c, index, e)) if e["kind"] == "password" else \
        (lambda c: edit_note(c, index, e))
    c.push(MenuScreen(e["title"][:22], [("VIEW", view), ("EDIT", edit), ("DELETE", delete, "", "red")]))
    view(c)


def _save(c, entry, index, pops):
    try:
        c.signer.note_save(entry=entry, index=index)
    except Exception as ex:  # noqa: BLE001 - shown, nothing saved
        return str(ex)[:40]
    for _ in range(pops):
        c.pop()
    c.push(MessageScreen("SAVED", [f"\"{entry['title'][:24]}\"", "", "Encrypted with your seed."]))
    return None


def edit_note(c, index=None, old=None):
    old = old or {}

    def got_title(c, title):
        if not title.strip():
            return "Give it a title"

        def got_text(c, text):
            # back to the list: the two inputs, and the entry's menu when editing
            return _save(c, {"kind": "note", "title": title.strip(), "text": text,
                             "made": old.get("made", 0)}, index, 2 + (index is not None))
        c.push(TextInputScreen("NOTE", "The note:", got_text, max_len=nl.LIMITS["text"],
                               initial=old.get("text", ""), empty_ok=True,
                               info=lambda s: f"{len(s)}/{nl.LIMITS['text']}"))
        return None
    c.push(TextInputScreen("NOTE", "Title:", got_title, max_len=nl.LIMITS["title"],
                           initial=old.get("title", "")))


def edit_password(c, index=None, old=None):
    old = old or {}
    entry = {"kind": "password", "made": old.get("made", 0)}

    def step_title(c, v):
        if not v.strip():
            return "Give it a title"
        entry["title"] = v.strip()
        c.push(TextInputScreen("PASSWORD", "User name (optional):", step_user,
                               max_len=nl.LIMITS["user"], initial=old.get("user", ""), empty_ok=True))
        return None

    def step_user(c, v):
        entry["user"] = v
        c.push(TextInputScreen("PASSWORD", "Website (optional):", step_site,
                               max_len=nl.LIMITS["site"], initial=old.get("site", ""), empty_ok=True))
        return None

    def step_site(c, v):
        entry["site"] = v

        def typed(c, pw):
            if not pw:
                return "Type a password"
            entry["password"] = pw
            return _save(c, entry, index, 5 + (index is not None))

        def generate(c):
            entry["password"] = nl.random_password(20)
            err = _save(c, entry, index, 4 + (index is not None))
            if err:
                c.push(MessageScreen("NOT SAVED", [err], border="red"))

        rows = [("GENERATE ONE", generate, "20 CHARS", "gold"),
                ("TYPE IT", lambda c: c.push(TextInputScreen(
                    "PASSWORD", "Password:", typed, masked=True, max_len=nl.LIMITS["password"],
                    initial=old.get("password", ""))))]
        c.push(MenuScreen("THE PASSWORD", rows, info=["A generated one comes from", "the kernel's random source."]))
        return None

    c.push(TextInputScreen("PASSWORD", "Title (e.g. Email):", step_title,
                           max_len=nl.LIMITS["title"], initial=old.get("title", "")))


def bip85_menu(c):
    """A password derived from the seed: BIP85 PWD BASE64, 21 characters by
    default. The same number always gives the same password."""
    def got_index(c, text):
        index = int(text)
        pw = c.signer.bip85_password(length=21, index=index)
        c.pop()
        e = {"kind": "password", "title": f"BIP85 #{index}", "password": pw}

        def save(c):
            err = _save(c, dict(e), None, 2)
            if err:
                c.push(MessageScreen("NOT SAVED", [err], border="red"))
        c.push(MenuScreen(f"BIP85 #{index}", [("SHOW", lambda c: c.push(PasswordScreen(e))),
                                             ("SAVE IN THE LIST", save)],
                          info=["21 characters (BIP85 base64).", "Any BIP85 tool gives the same",
                                "from your seed and number."]))
        c.push(PasswordScreen(e))
        return None
    c.push(TextInputScreen("BIP85 PASSWORD", "Password number (0, 1, 2...):", got_index,
                           allowed="0123456789", max_len=6, initial="0"))


class NoteScreen(Screen):
    """A note, wrapped; UP/DOWN scroll."""

    def __init__(self, e: dict):
        self.title = e["title"][:22]
        self.lines = []
        for para in (e.get("text") or "(empty)").split("\n"):
            self.lines += wrap(para, 36) or [""]
        self.top = 0

    def handle(self, ev, app):
        if ev.kind is Kind.NAV and ev.value in ("UP", "DOWN"):
            step = -1 if ev.value == "UP" else 1
            self.top = min(max(0, self.top + step), max(0, len(self.lines) - 8))
        elif ev.kind in (Kind.SELECT, Kind.BACK, Kind.HOME):
            self.lines = []
            app.pop()

    def render(self, app, frame):
        img, d = t.new_canvas()
        t.sky(img, d, hills=False)
        app.status_bar(img, d, self)
        t.window(d, (5, 22, W - 5, H - 8), self.title)
        for i, line in enumerate(self.lines[self.top:self.top + 8]):
            t.text(d, (16, 36 + i * 13), line, BODY, "light")
        if len(self.lines) > 8:
            t.text(d, (W - 60, H - 20), f"{self.top + 1}-{min(self.top + 8, len(self.lines))}/"
                   f"{len(self.lines)}", TINY, "grey")
        return img


class PasswordScreen(Screen):
    """A password entry: user, site and the password; LEFT/RIGHT shows the
    password as a QR (to scan into a phone)."""

    def __init__(self, e: dict):
        self.e = dict(e)
        self.qr = False

    def handle(self, ev, app):
        if ev.kind is Kind.NAV and ev.value in ("LEFT", "RIGHT"):
            self.qr = not self.qr
        elif ev.kind in (Kind.SELECT, Kind.BACK, Kind.HOME):
            self.e = {}
            app.pop()

    def render(self, app, frame):
        img, d = t.new_canvas()
        t.sky(img, d, hills=False)
        app.status_bar(img, d, self)
        pw = self.e.get("password", "")
        if self.qr:
            size = t.qr(img, pw, (8, 18), module=3, border=2)
            t.window(d, (size + 16, 22, W - 5, H - 8), self.e.get("title", "")[:12])
            t.text(d, (size + 26, 40), "The password", TINY, "light")
            t.text(d, (size + 26, 54), "as a QR.", TINY, "light")
            t.text(d, (size + 26, 82), "LEFT/RIGHT: TEXT", TINY, "grey")
            return img
        t.window(d, (5, 22, W - 5, H - 8), self.e.get("title", "")[:24])
        y = 38
        for label, key in (("USER", "user"), ("SITE", "site")):
            if self.e.get(key):
                t.text(d, (16, y), label, TINY, "grey")
                t.text(d, (60, y), self.e[key][:30], BODY, "light")
                y += 14
        t.text(d, (16, y + 4), "PASSWORD", TINY, "grey")
        for i in range(0, min(len(pw), 96), 32):
            t.text(d, (16, y + 18 + (i // 32) * 13), pw[i:i + 32], BODY, "gold")
        t.text(d, (16, H - 22), "LEFT/RIGHT: QR   OK: DONE", TINY, "grey")
        return img
