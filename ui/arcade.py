"""
COIN INVADERS: the game behind the game PIN (SECURITY › PINS › GAME PIN).

Entered at unlock, the game PIN opens this instead of the wallet, so at a
border crossing the device looks like what it's styled as: a little arcade
toy. It runs in the app, which never holds keys; the signer stays locked
(or, in game+wipe mode, has already erased everything).

One stage that repeats, a bit faster each wave: five rows of invaders
marching and dropping bombs, three shields that wear away, a bonus saucer,
three lives, score and a saved high score.

Keys: Z / C (or the arrow keys) move, OK / SPACE / F fire, OK starts.
Hold HOME (ESC) for 1.5 s to leave: back to the lock screen.
"""
from __future__ import annotations

import random
import time

from PIL import Image, ImageDraw

from . import theme as t
from .widgets import Screen

W, H = 320, 170
TOP = 14                       # the HUD row above the playfield
GROUND = 164
HOLD_TO_EXIT = 1.5

LEFT_KEYS = {"KEY_Z", "KEY_LEFT", "KEY_COMMA"}
RIGHT_KEYS = {"KEY_C", "KEY_RIGHT", "KEY_DOT", "KEY_SLASH"}
FIRE_KEYS = {"KEY_ENTER", "KEY_KPENTER", "KEY_OK", "KEY_SPACE", "KEY_F", "KEY_UP"}
EXIT_KEYS = {"KEY_ESC", "KEY_HOMEPAGE"}


def _sprite(rows: list[str], color) -> Image.Image:
    img = Image.new("RGBA", (len(rows[0]), len(rows)), (0, 0, 0, 0))
    px = img.load()
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch != ".":
                px[x, y] = color + (255,)
    return img


# 12x8 invaders, two frames each; 13x8 ship; 16x7 saucer
_INV = {
    "squid": (30, "pink", [
        ["....xxxx....", "...xxxxxx...", "..xxxxxxxx..", ".xx.xxxx.xx.",
         ".xxxxxxxxxx.", "...x....x...", "..x.xxxx.x..", ".x.x....x.x."],
        ["....xxxx....", "...xxxxxx...", "..xxxxxxxx..", ".xx.xxxx.xx.",
         ".xxxxxxxxxx.", "..x.x..x.x..", ".x........x.", "..x......x.."]]),
    "crab": (20, "cyan", [
        ["..x......x..", "...x....x...", "..xxxxxxxx..", ".xx.xxxx.xx.",
         "xxxxxxxxxxxx", "x.xxxxxxxx.x", "x.x......x.x", "...xx..xx..."],
        ["..x......x..", "x..x....x..x", "x.xxxxxxxx.x", "xxx.xxxx.xxx",
         "xxxxxxxxxxxx", ".xxxxxxxxxx.", "..x......x..", ".x........x."]]),
    "octo": (10, "green", [
        ["....xxxx....", ".xxxxxxxxxx.", "xxxxxxxxxxxx", "xxx..xx..xxx",
         "xxxxxxxxxxxx", "..xxx..xxx..", ".xx..xx..xx.", "..xx....xx.."],
        ["....xxxx....", ".xxxxxxxxxx.", "xxxxxxxxxxxx", "xxx..xx..xxx",
         "xxxxxxxxxxxx", "...xx..xx...", "..xx.xx.xx..", "xx........xx"]]),
}
_SHIP = ["......x......", ".....xxx.....", ".....xxx.....", ".xxxxxxxxxxx.",
         "xxxxxxxxxxxxx", "xxxxxxxxxxxxx", "xxxxxxxxxxxxx", "xxxxxxxxxxxxx"]
_SAUCER = [".....xxxxxx.....", "...xxxxxxxxxx...", "..xxxxxxxxxxxx..", ".xx.xx.xx.xx.xx.",
           "xxxxxxxxxxxxxxxx", "..xxx..xx..xxx..", "...x........x..."]
_BOOM = ["x..x...x..x.", ".x..x.x..x..", "..x.....x...", "xx.......xx.",
         "..x.....x...", ".x..x.x..x..", "x..x...x..x.", "............"]
ROWS = ["squid", "crab", "crab", "octo", "octo"]
COLS = 9


class _Sprites:
    def __init__(self):
        P = t.PAL
        self.inv = {k: [_sprite(f, P[c]) for f in frames] for k, (_, c, frames) in _INV.items()}
        self.ship = _sprite(_SHIP, P["gold"])
        self.saucer = _sprite(_SAUCER, P["red"])
        self.boom = _sprite(_BOOM, P["white"])
        self.boom_ship = _sprite(_BOOM, P["orange"])


_SPR: _Sprites | None = None


def sprites() -> _Sprites:
    global _SPR
    if _SPR is None:
        _SPR = _Sprites()
    return _SPR


class Shield:
    """22x12 of 2x2 blocks that bullets chip away."""
    SHAPE = ["...xxxxx...", "..xxxxxxx..", ".xxxxxxxxx.", "xxxxxxxxxxx",
             "xxxxxxxxxxx", "xxx.....xxx"]

    def __init__(self, x: int, y: int):
        self.x, self.y = x, y
        self.cells = {(c, r) for r, row in enumerate(self.SHAPE) for c, ch in enumerate(row)
                      if ch == "x"}

    def hit(self, px: float, py: float, down: bool) -> bool:
        c, r = int((px - self.x) // 2), int((py - self.y) // 2)
        if (c, r) in self.cells:
            # take a small bite around the hit, deeper in the bullet's direction
            for dc, dr in ((0, 0), (-1, 0), (1, 0), (0, 1 if down else -1)):
                if random.random() < (1.0 if (dc, dr) == (0, 0) else 0.6):
                    self.cells.discard((c + dc, r + dr))
            return True
        return False

    def draw(self, d):
        col = t.PAL["green"]
        for c, r in self.cells:
            x, y = self.x + c * 2, self.y + r * 2
            d.rectangle((x, y, x + 1, y + 1), fill=col)


class ArcadeScreen(Screen):
    """Full screen, no status bar: nothing here says "wallet"."""
    animated = True
    title = ""

    def __init__(self, on_exit, hi: int = 0, save_hi=None, rng=None, clock=time.monotonic):
        self.on_exit = on_exit
        self.save_hi = save_hi
        self.hi = int(hi or 0)
        self.rng = rng or random.Random()
        self.clock = clock
        self.held: set[str] = set()
        self.exit_since: float | None = None
        self.state = "title"
        self.last = None
        self.stars = [(self.rng.randrange(W), self.rng.randrange(TOP, H), self.rng.choice((1, 2)))
                      for _ in range(40)]
        self.score = 0
        self.lives = 3
        self.wave = 0
        self.exited = False
        self.over_at = 0.0
        try:                                     # make the sounds now, not on the first shot
            from . import sound
            for name in ("game_shot", "game_boom", "game_hit"):
                sound.prepare(name)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------- set up
    def new_game(self):
        self.score, self.lives, self.wave = 0, 3, 0
        self.new_wave()
        self.state = "play"

    def new_wave(self):
        self.wave += 1
        self.invaders = []                      # [x, y, kind, alive]
        for r, kind in enumerate(ROWS):
            for c in range(COLS):
                self.invaders.append([34 + c * 24, 26 + r * 14, kind, True])
        self.dir = 1
        self.step_t = 0.0
        self.anim = 0
        self.ship_x = W / 2 - 6
        self.shot = None                         # [x, y]
        self.bombs: list[list[float]] = []
        self.booms: list[list] = []              # [x, y, until, ship?]
        self.shields = [Shield(48 + i * 96, 132) for i in range(3)]
        self.saucer = None                       # [x, dir]
        self.saucer_t = 12.0
        self.dead_until = 0.0
        self.wave_until = 0.0

    # ------------------------------------------------------------- input
    def raw_key(self, name: str, value: int, app) -> None:
        """Every key press (1), repeat (2) and release (0) comes here, so
        moving works while a key is held."""
        if value == 0:
            self.held.discard(name)
            if name in EXIT_KEYS:
                self.exit_since = None
            return
        if value == 1:
            self.held.add(name)
            if name in EXIT_KEYS:
                self.exit_since = self.clock()
                return
            if name in FIRE_KEYS:
                if self.state in ("title", "over"):
                    if self.state == "over" and self.clock() < self.over_at + 1.0:
                        return                   # a beat before a mash restarts it
                    self.new_game()
                elif self.state == "play":
                    self.fire()

    def handle(self, ev, app):
        pass                                     # all keys come through raw_key

    def fire(self):
        if self.shot is None and self.clock() >= self.dead_until:
            self.shot = [self.ship_x + 6, GROUND - 10]
            self._sound("game_shot")

    def _sound(self, name):
        try:
            from . import sound
            sound.play(name, enabled=getattr(self, "sound_on", True))
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------- update
    def update(self, now: float):
        if self.last is None:
            self.last = now
        dt = min(0.1, max(0.0, now - self.last))
        self.last = now
        if self.exit_since is not None and now - self.exit_since >= HOLD_TO_EXIT and not self.exited:
            self.exited = True
            self.on_exit()
            return
        self.stars = [(x, y + (s * 6 * dt), s) if y + s * 6 * dt < H else (x, TOP, s)
                      for x, y, s in self.stars]
        if self.state != "play":
            return
        if self.wave_until and now < self.wave_until:
            return
        self.wave_until = 0.0
        self._move_ship(dt, now)
        self._move_shot(dt)
        self._march(dt)
        self._bombs(dt, now)
        self._saucer(dt)
        self.booms = [b for b in self.booms if b[2] > now]
        alive = [i for i in self.invaders if i[3]]
        if not alive:
            self.new_wave()
            self.wave_until = now + 1.2
        elif max(i[1] for i in alive) + 8 >= GROUND - 10:
            self._game_over(now)                   # they landed

    def _move_ship(self, dt, now):
        if now < self.dead_until:
            return
        v = 0
        if self.held & LEFT_KEYS:
            v -= 1
        if self.held & RIGHT_KEYS:
            v += 1
        self.ship_x = min(W - 17, max(4, self.ship_x + v * 130 * dt))

    def _move_shot(self, dt):
        """In small steps: at the device's frame rate one frame moved the shot
        13 px, more than an invader is tall, so it could pass straight
        through one. Now every 2 px of the way is checked."""
        if self.shot is None:
            return
        dist = 260 * dt
        while dist > 0 and self.shot is not None:
            step = min(2.0, dist)
            dist -= step
            self.shot[1] -= step
            self._shot_hits()

    def _shot_hits(self):
        x, y = self.shot
        if y < TOP:
            self.shot = None
            return
        for s in self.shields:
            if s.hit(x, y, down=False):
                self.shot = None
                return
        for inv in self.invaders:
            if inv[3] and inv[0] - 1 <= x <= inv[0] + 12 and inv[1] <= y <= inv[1] + 8:
                inv[3] = False
                self.score += _INV[inv[2]][0]
                self.booms.append([inv[0], inv[1], self.clock() + 0.25, False])
                self.shot = None
                self._sound("game_boom")
                return
        if self.saucer and self.saucer[0] <= x <= self.saucer[0] + 15 and TOP + 4 <= y <= TOP + 11:
            self.score += self.rng.choice((50, 100, 150, 300))
            self.booms.append([self.saucer[0] + 2, TOP + 4, self.clock() + 0.4, False])
            self.saucer = None
            self.shot = None
            self._sound("game_boom")

    def _march(self, dt):
        alive = [i for i in self.invaders if i[3]]
        if not alive:
            return
        # fewer invaders, faster march; each wave starts a little quicker
        frac = len(alive) / len(self.invaders)
        interval = max(0.04, (0.05 + 0.55 * frac) * max(0.55, 1 - 0.07 * (self.wave - 1)))
        self.step_t += dt
        if self.step_t < interval:
            return
        self.step_t = 0.0
        self.anim ^= 1
        lo = min(i[0] for i in alive)
        hi = max(i[0] for i in alive) + 12
        if (self.dir > 0 and hi + 4 > W - 4) or (self.dir < 0 and lo - 4 < 4):
            for i in self.invaders:
                i[1] += 6
            self.dir = -self.dir
        else:
            for i in self.invaders:
                i[0] += 4 * self.dir
        # the bottom invader of a random column drops a bomb now and then
        if len(self.bombs) < 2 + min(2, self.wave // 2) and self.rng.random() < 0.35:
            cols = {}
            for i in alive:
                k = round(i[0] / 4)
                if k not in cols or i[1] > cols[k][1]:
                    cols[k] = i
            shooter = self.rng.choice(list(cols.values()))
            self.bombs.append([shooter[0] + 6, shooter[1] + 8])

    def _bombs(self, dt, now):
        keep = []
        for b in self.bombs:
            dist = (80 + 8 * min(self.wave, 6)) * dt
            gone = False
            while dist > 0 and not gone:              # same small steps as the shot
                step = min(2.0, dist)
                dist -= step
                b[1] += step
                x, y = b
                if y > GROUND or any(s.hit(x, y, down=True) for s in self.shields):
                    gone = True
                elif (now >= self.dead_until and self.ship_x - 1 <= x <= self.ship_x + 13
                      and GROUND - 8 <= y <= GROUND):
                    self._ship_hit(now)
                    gone = True
                if self.state != "play" or not self.bombs:
                    gone = True
            if not gone:
                keep.append(b)
        if self.state == "play" and self.bombs:
            self.bombs = keep

    def _ship_hit(self, now):
        self.lives -= 1
        self.booms.append([self.ship_x, GROUND - 8, now + 0.8, True])
        self._sound("game_hit")
        self.bombs = []
        self.shot = None
        if self.lives <= 0:
            self._game_over(now)
        else:
            self.dead_until = now + 1.2

    def _saucer(self, dt):
        if self.saucer is None:
            self.saucer_t -= dt
            if self.saucer_t <= 0:
                d = self.rng.choice((-1, 1))
                self.saucer = [W if d < 0 else -16, d]
                self.saucer_t = self.rng.uniform(15, 25)
            return
        self.saucer[0] += self.saucer[1] * 60 * dt
        if self.saucer[0] < -20 or self.saucer[0] > W + 4:
            self.saucer = None

    def _game_over(self, now):
        self.state = "over"
        self.over_at = now
        if self.score > self.hi:
            self.hi = self.score
            if self.save_hi:
                try:
                    self.save_hi(self.hi)
                except Exception:  # noqa: BLE001
                    pass

    # ------------------------------------------------------------- draw
    def render(self, app, frame):
        now = self.clock()
        self.sound_on = bool(getattr(getattr(app, "settings", None), "sound", True))
        self.update(now)
        img = Image.new("RGB", (W, H), (0, 0, 8))
        d = ImageDraw.Draw(img)
        d.fontmode = "1"
        for x, y, s in self.stars:
            c = (60, 60, 96) if s == 1 else (120, 120, 170)
            d.point((x, int(y)), fill=c)
        sp = sprites()
        # HUD
        # the signer always paints the top-left 14x12 (its gold-shield corner,
        # black when the app has the screen), so the HUD starts after it
        t.text(d, (18, 2), f"SCORE {self.score:06d}", t.TINY, "white", shadow=False)
        t.text(d, (W // 2, 2), f"HI {self.hi:06d}", t.TINY, "gold", shadow=False, align="center")
        for i in range(max(0, self.lives if self.state == "play" else 0)):
            img.paste(sp.ship, (W - 16 - i * 16, 3), sp.ship)
        d.line((0, TOP - 1, W, TOP - 1), fill=(40, 40, 80))
        if self.state == "title":
            self._draw_title(img, d, now)
        else:
            self._draw_play(img, d, now)
            if self.state == "over":
                d.rectangle((70, 66, W - 71, 110), fill=(0, 0, 8), outline=t.PAL["red"])
                t.text(d, (W // 2, 74), "GAME OVER", t.BIG, "red", align="center")
                if now >= self.over_at + 1.0 and int(now * 2) % 2 == 0:
                    t.text(d, (W // 2, 96), "INSERT COIN  (OK)", t.TINY, "gold", align="center")
            elif self.wave_until:
                t.text(d, (W // 2, 80), f"WAVE {self.wave}", t.BIG, "gold", align="center")
        if self.exit_since is not None:
            frac = min(1.0, (now - self.exit_since) / HOLD_TO_EXIT)
            d.rectangle((W // 2 - 50, H - 9, W // 2 + 50, H - 4), outline=t.PAL["grey"])
            d.rectangle((W // 2 - 49, H - 8, W // 2 - 49 + int(98 * frac), H - 5),
                        fill=t.PAL["gold"])
        return img

    def _draw_title(self, img, d, now):
        sp = sprites()
        t.text(d, (W // 2, 26), "COIN", t.BIG, "gold", align="center")
        t.text(d, (W // 2, 46), "INVADERS", t.BIG, "gold", align="center")
        y = 74
        for kind in ("squid", "crab", "octo"):
            pts = _INV[kind][0]
            img.paste(sp.inv[kind][int(now * 2) % 2], (112, y), sp.inv[kind][0])
            t.text(d, (134, y), f"= {pts} PTS", t.TINY, "light", shadow=False)
            y += 14
        img.paste(sp.saucer, (110, y), sp.saucer)
        t.text(d, (134, y), "= ??? PTS", t.TINY, "light", shadow=False)
        if int(now * 2) % 2 == 0:
            t.text(d, (W // 2, 136), "PRESS OK TO START", t.TINY, "white", align="center")
        t.text(d, (W // 2, 152), "Z / C MOVE   OK FIRE", t.TINY, "grey", shadow=False, align="center")

    def _draw_play(self, img, d, now):
        sp = sprites()
        for inv in self.invaders:
            if inv[3]:
                s = sp.inv[inv[2]][self.anim]
                img.paste(s, (int(inv[0]), int(inv[1])), s)
        for s in self.shields:
            s.draw(d)
        if self.saucer:
            img.paste(sp.saucer, (int(self.saucer[0]), TOP + 4), sp.saucer)
        blink = now < self.dead_until and int(now * 8) % 2 == 0
        dying = any(b[3] for b in self.booms)
        if self.state == "play" and not dying and not blink:
            img.paste(sp.ship, (int(self.ship_x), GROUND - 8), sp.ship)
        if self.shot:
            x, y = int(self.shot[0]), int(self.shot[1])
            d.line((x, y, x, y + 4), fill=t.PAL["white"])
        for x, y in self.bombs:
            x, y = int(x), int(y)
            zig = int(y / 3) % 2
            d.line((x - zig, y, x + zig, y + 2), fill=t.PAL["orange_lt"])
            d.line((x + zig, y + 2, x - zig, y + 4), fill=t.PAL["orange_lt"])
        for x, y, _, ship in self.booms:
            b = sp.boom_ship if ship else sp.boom
            img.paste(b, (int(x), int(y)), b)
        d.line((0, GROUND + 1, W, GROUND + 1), fill=t.PAL["green_dk"])
