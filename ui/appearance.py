"""
SETUP › APPEARANCE: how the balance digits look, and the color of the boxes.

The box color is a ready-made choice (CLASSIC, EMERALD, GRAPE...) or CUSTOM,
mixed with red/green/blue sliders. It's one RGB color (the top of each box's
gradient); the darker bottom is worked out from it. Colors too bright for white text are toned
down automatically, so menus always stay readable. Sir Signer's own screens
always keep the classic blue, so his screens stay recognisable.
"""
from __future__ import annotations

from . import theme as t
from .input import Kind
from .widgets import MenuScreen, Screen

STYLES = {"big": "ALL BIG", "split": "SMALL SATS"}
STEP = 8

# Ready-made box colors (the top of each box). All dark enough for white text.
PRESETS = [
    ("CLASSIC", t.DEFAULT_BOX),
    ("EMERALD", (24, 140, 88)),
    ("GRAPE", (128, 56, 200)),
    ("RUBY", (184, 40, 64)),
    ("SUNSET", (208, 104, 24)),
    ("TEAL", (16, 128, 144)),
    ("ROSE", (200, 64, 140)),
    ("SLATE", (80, 92, 120)),
    ("MIDNIGHT", (40, 40, 96)),
]
CUSTOM = "CUSTOM"
NAMES = [n for n, _ in PRESETS] + [CUSTOM]


def apply(settings) -> None:
    """Use the saved appearance (at start-up and after any change)."""
    t.set_box_color(tuple(settings.box_color) if settings.box_color else None)


def color_name(settings) -> str:
    rgb = tuple(settings.box_color) if settings.box_color else t.DEFAULT_BOX
    if getattr(settings, "box_choice", None) == CUSTOM:
        return CUSTOM
    return next((n for n, c in PRESETS if tuple(c) == rgb), CUSTOM)


def choose(settings, name: str) -> None:
    """Switch to a preset, or to CUSTOM (your own color from the sliders)."""
    if name == CUSTOM:
        settings.box_choice = CUSTOM
        if settings.box_custom:
            settings.box_color = list(settings.box_custom)
    else:
        settings.box_choice = None
        rgb = dict(PRESETS)[name]
        settings.box_color = None if tuple(rgb) == t.DEFAULT_BOX else list(rgb)
    settings.save()
    apply(settings)


def appearance_menu(app, selected: int = 0):
    s = app.settings

    def digits(app, step):
        s.amount_style = "split" if s.amount_style == "big" else "big"
        s.save()

    def cycle(app, step):
        i = NAMES.index(color_name(s))
        choose(s, NAMES[(i + step) % len(NAMES)])

    def color_ok(app):
        if color_name(s) == CUSTOM:
            app.push(ColorScreen())           # CUSTOM: pick it with the sliders
        else:
            cycle(app, 1)
            menu.items = items()

    def items():
        name = color_name(s)
        return [
            ("BALANCE DIGITS", None, STYLES.get(s.amount_style, "ALL BIG"), "gold", digits),
            ("BOX COLOR", color_ok, name, "cyan", cycle),
        ]

    def info():
        if color_name(s) == CUSTOM:
            return ["< > picks a color. On CUSTOM,", "OK opens the color sliders."]
        return ["< > picks a color: CUSTOM at the", "end lets you mix your own."]

    menu = MenuScreen("APPEARANCE", items, info=info(), selected=selected)
    refresh = menu.refresh

    def refresh_all():
        menu.info = info()
        return refresh()
    menu.refresh = refresh_all
    app.push(menu)


class ColorScreen(Screen):
    """Three sliders. UP/DOWN pick red, green or blue; LEFT/RIGHT move it.
    The boxes on screen change as you go. OK keeps it, DEL puts it back."""
    title = "BOX COLOR"

    def __init__(self):
        self.ch = 0
        self.rgb = None

    def on_show(self, app):
        if self.rgb is None:
            s = app.settings
            self.rgb = list(s.box_custom or s.box_color or t.DEFAULT_BOX)

    def on_hide(self, app):
        apply(app.settings)           # leaving without OK puts the old color back

    def _preview(self):
        t.set_box_color(tuple(self.rgb))

    def handle(self, ev, app):
        if ev.kind is Kind.NAV:
            if ev.value == "UP":
                self.ch = (self.ch - 1) % 3
            elif ev.value == "DOWN":
                self.ch = (self.ch + 1) % 3
            elif ev.value in ("LEFT", "RIGHT", "PAGE_UP", "PAGE_DOWN"):
                d = {"LEFT": -STEP, "RIGHT": STEP, "PAGE_UP": -32, "PAGE_DOWN": 32}[ev.value]
                self.rgb[self.ch] = max(0, min(255, self.rgb[self.ch] + d))
        elif ev.kind is Kind.SELECT:
            s = app.settings
            s.box_custom = list(self.rgb)
            s.box_choice = CUSTOM
            s.box_color = list(self.rgb)
            s.save()
            app.pop()                 # back to APPEARANCE, which shows the new value
        elif ev.kind is Kind.BACK:
            app.pop()
        elif ev.kind is Kind.HOME:
            app.home()

    def render(self, app, frame):
        self._preview()
        img, d = t.new_canvas()
        t.sky(img, d)
        app.status_bar(img, d, self)
        t.window(d, (8, 20, 311, 132), "BOX COLOR")
        names = ("RED", "GREEN", "BLUE")
        fills = ((232, 64, 64), (88, 216, 88), (88, 140, 248))
        for i in range(3):
            y = 34 + i * 22
            sel = i == self.ch
            if sel:
                t.cursor(img, (16, y), frame)
            t.text(d, (28, y), names[i], t.BODY, "white" if sel else "grey")
            x0, x1 = 84, 260
            d.rectangle((x0, y - 1, x1, y + 8), fill=t.PAL["ink"])
            d.rectangle((x0 + 1, y, x1 - 1, y + 7), fill=t.PAL["bar_bg"])
            fx = x0 + 1 + int((x1 - x0 - 2) * self.rgb[i] / 255)
            if fx > x0 + 1:
                d.rectangle((x0 + 1, y, fx - 1, y + 7), fill=fills[i])
                d.line((x0 + 1, y, fx - 1, y), fill=t.PAL["white"])
            if sel:
                d.rectangle((fx - 1, y - 3, fx + 1, y + 10), fill=t.PAL["white"])
            t.text(d, (300, y), str(self.rgb[i]), t.BODY, "white" if sel else "grey",
                   align="right")
        shown = t.PAL["win_top"]
        note = ("TONED DOWN SO TEXT STAYS READABLE" if tuple(shown) != tuple(self.rgb)
                else "SAMPLE TEXT LOOKS LIKE THIS")
        t.text(d, (160, 104), note, t.TINY, "light", align="center")
        t.text(d, (160, 116), f"#{self.rgb[0]:02X}{self.rgb[1]:02X}{self.rgb[2]:02X}", t.BODY,
               "gold", align="center")
        t.window(d, (8, 138, 311, 166))
        t.text(d, (160, 148), "UP/DOWN PICK  < > ADJUST  OK KEEP  DEL BACK",
               t.TINY, "grey", align="center")
        return img
