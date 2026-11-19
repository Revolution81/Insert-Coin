"""
The signer's own screen: the trusted path.

The signer process owns the display and the keyboard. Most of the time it
passes key presses through to the app and shows the app's frames. When the
signer needs you (your PIN, a payment to confirm, seed words to show) it
takes over: keys stop reaching the app, the app's frames stop being shown,
and the signer draws its own screens until it's done.

So the app never sees your PIN, your passphrase or your seed words, and
can't draw the screen you say YES on.

Two marks the app can't fake:
  - the top-left corner of every frame is drawn here, last: a gold shield
    while the signer has the screen, blank otherwise;
  - PIN screens show your two security words, which live in the signer's
    folder where the app can't read them.

A Console has the same small interface as the App (push, pop, job,
status_bar...), so the ordinary widgets from ui/widgets.py run on it.
"""
from __future__ import annotations

import glob
import queue
import threading
import time
import traceback
from typing import Callable

from . import theme as t
from .input import Mode, Translator
from .widgets import BusyScreen, MessageScreen, Screen


class Cancelled(Exception):
    """You backed out on the signer's screen."""


class Busy(Exception):
    """The signer's screen is already showing something."""


def battery_level() -> float:
    for p in glob.glob("/sys/class/power_supply/*/capacity"):
        try:
            with open(p) as f:
                return int(f.read()) / 100
        except (OSError, ValueError):
            pass
    return 1.0


def _scrub(screen):
    """Drop what a closed screen held: typed text, shown words. (Python
    can't truly wipe memory, but it needn't keep extra references.)"""
    buf = getattr(screen, "buf", None)
    if buf is not None and hasattr(buf, "wipe"):
        buf.wipe()
    if isinstance(getattr(screen, "words", None), list) and not callable(screen.words):
        screen.words = []


class Console:
    def __init__(self, signer, words: tuple[str, str] = ("", ""), sync_jobs: bool = False,
                 idle_s: float = 180):
        self.signer = signer              # the real Signer (wrapped in a lock)
        self.words = words                # security words, shown on PIN screens
        self.tr = Translator(Mode.MENU)
        self.stack: list[Screen] = []
        self.sync_jobs = sync_jobs
        self.idle_s = idle_s              # a prompt left alone this long cancels itself
        self.listeners: list[Callable[[bool], None]] = []   # told when the signer takes/returns the screen
        self._session = threading.Lock()
        self._done: threading.Event | None = None
        self._outcome: tuple[str, object] = ("err", Cancelled())
        self._results: queue.Queue = queue.Queue()
        self._posted: queue.Queue = queue.Queue()
        self.jobs = 0
        self.last_input = time.monotonic()

    # ------------------------------------------------------------ sessions
    @property
    def active(self) -> bool:
        return bool(self.stack) or self._done is not None

    def session(self, start: Callable[["Console"], None]):
        """Run one prompt: start(console) pushes the first screen; some screen
        later calls finish(value) or fail(error). Blocks the calling thread
        (a request from the app) until then, and returns value or raises."""
        if not self._session.acquire(blocking=False):
            raise Busy("the signer's screen is already in use")
        try:
            done = threading.Event()
            self._outcome = ("err", Cancelled("cancelled"))
            self._done = done
            # Only if this session is still the live one when the console gets
            # to it: one cancelled before it began never opens a stray screen.
            self.post(lambda c: start(c) if c._done is done else None)
            done.wait()
            kind, value = self._outcome
            self._outcome = ("err", Cancelled())
            if kind == "err":
                raise value
            return value
        finally:
            self._session.release()

    def finish(self, value=None):
        self._end("ok", value)

    def fail(self, error: Exception):
        self._end("err", error)

    def cancel(self):
        self.fail(Cancelled("cancelled on the device"))

    def _end(self, kind, value):
        if self._done is None:
            return
        done, self._done = self._done, None
        screens, self.stack = self.stack, []
        for s in reversed(screens):
            hook = getattr(s, "on_hide", None)
            if hook:
                hook(self)
            _scrub(s)
        self.tr.reset()
        self.tr.set_mode(Mode.MENU)
        self._outcome = (kind, value)
        self._notify(False)
        done.set()

    def _notify(self, active: bool):
        for fn in self.listeners:
            try:
                fn(active)
            except Exception:  # noqa: BLE001 - a dead link mustn't break the signer
                traceback.print_exc()

    # ---------------------------------------------------- the App interface
    @property
    def top(self) -> Screen:
        return self.stack[-1]

    def _shown(self):
        self.tr.set_mode(self.top.mode)
        self.top.on_show(self)

    def push(self, s: Screen):
        if not self.stack:
            self.tr.reset()
            self.last_input = time.monotonic()
            self._notify(True)
        self.stack.append(s)
        self._shown()

    def pop(self):
        """Backing out of the first screen cancels the whole prompt (unless
        that screen's on_hide ends it some other way)."""
        if not self.stack:
            return
        s = self.stack.pop()
        hook = getattr(s, "on_hide", None)
        if hook:
            hook(self)
        _scrub(s)
        if self._done is None:
            return
        if not self.stack:
            # Widgets often pop themselves and then push the next screen
            # (ConfirmScreen's YES, MessageScreen's `then`): only cancel if
            # nothing came next. Runs before the next frame is drawn.
            done = self._done
            self.post(lambda c: c.cancel() if not c.stack and c._done is done else None)
            return
        self._shown()

    def replace(self, s: Screen):
        for old in self.stack:
            hook = getattr(old, "on_hide", None)
            if hook:
                hook(self)
        self.stack = [s]
        self._shown()

    def home(self):
        self.cancel()

    def status_bar(self, img, d, screen):
        typing = self.tr.indicators if screen.mode is Mode.TEXT else None
        from core import power
        level, charging = power.battery()
        now = time.monotonic()
        if now - getattr(self, "_clock_at", -9.0) > 2.0:     # a file read, not every frame
            self._clock_at = now
            try:
                self._clock12 = self.signer.clock_12h()
            except Exception:  # noqa: BLE001
                self._clock12 = False
        t.status_bar(img, d, mode="SIGNER", battery_frac=level, charging=charging,
                     clock=t.clock_text(self._clock12), typing=typing)

    def post(self, fn):
        self._posted.put(fn)

    def job(self, label, fn, on_done, on_error=None):
        busy = BusyScreen(label)
        self.push(busy)
        self.jobs += 1
        if self.sync_jobs:
            self._finish_job(busy, *self._run(fn), on_done, on_error)
            return

        def work():
            self._results.put((busy, *self._run(fn), on_done, on_error))
        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _run(fn):
        try:
            return fn(), None
        except Exception as e:  # noqa: BLE001
            return None, e

    def _finish_job(self, busy, result, err, on_done, on_error):
        self.jobs -= 1
        if self._done is None:             # the prompt ended meanwhile
            return
        if busy in self.stack:
            self.stack.remove(busy)
            if self.stack:
                self._shown()
        if err is None:
            on_done(self, result)
        elif on_error:
            on_error(self, err)
        else:
            self.push(MessageScreen("OOPS", [str(err)[:120]], border="red",
                                    then=lambda c: c.fail(err)))

    # ------------------------------------------------------ the main loop
    def feed(self, name: str, value: int) -> bool:
        """A raw key event. True if the signer took it (it has the screen).
        Every key, the app's included, keeps the seed from timing out: the
        signer reads the keyboard itself, so this needs no word from the app."""
        try:
            self.signer.keepalive()
        except Exception:  # noqa: BLE001
            pass
        if not self.stack:
            return False
        self.last_input = time.monotonic()
        for ev in self.tr.feed(name, value):
            if not self.stack:
                break
            self.top.handle(ev, self)
        return True

    def pump(self) -> bool:
        changed = False
        while not self._posted.empty():
            self._posted.get()(self)
            changed = True
        while not self._results.empty():
            self._finish_job(*self._results.get())
            changed = True
        if self.stack:
            poll = getattr(self.top, "poll", None)
            if poll and poll(self):
                changed = True
            if not self.jobs and time.monotonic() - self.last_input > self.idle_s:
                self.cancel()
                changed = True
        return changed

    def idle(self) -> bool:
        """Nothing running here: either no prompt, or one waiting for keys."""
        return self._posted.empty() and self._results.empty() and self.jobs == 0

    def render(self, frame: int):
        with t.classic_boxes():          # the signer's screens never take a custom color
            return self.top.render(self, frame)

    def compose(self, app_frame, frame: int):
        """The frame that goes on the panel."""
        if self.stack:
            img = self.render(frame)
        elif app_frame is not None:
            img = app_frame.copy()
        else:
            img, _ = t.new_canvas()
        t.stamp(img, bool(self.stack))
        return img

    @property
    def animated(self) -> bool:
        return bool(self.stack) and (isinstance(self.top, BusyScreen)
                                     or getattr(self.top, "animated", False))
