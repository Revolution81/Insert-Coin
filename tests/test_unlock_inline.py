"""Unlock checks the PIN on the PIN screen itself: no busy window pops up
over it, keys wait while it works, and a wrong PIN leaves the screen as it
was with the message."""
import threading

from ui.console import Console
from ui.widgets import BusyScreen, PinScreen


def _console():
    c = Console(signer=None, sync_jobs=False)
    c._done = threading.Event()
    return c


def test_inline_job_keeps_the_screen_and_blocks_keys():
    c = _console()
    scr = PinScreen("UNLOCK", lambda c, pin: None)
    c.stack.append(scr)
    gate = threading.Event()
    got = []
    c.job("UNLOCKING...", lambda: gate.wait(5) and "ok", lambda c, r: got.append(r), inline=scr)
    assert c.stack == [scr] and not any(isinstance(s, BusyScreen) for s in c.stack)
    assert scr.working == "UNLOCKING..." and c.animated
    scr.handle(type("E", (), {"kind": None, "value": None})(), c)   # ignored, no error
    img = scr.render(type("A", (), {"status_bar": lambda *a: None})(), 0)
    assert img.size == (320, 170)
    gate.set()
    busy, result, err, done, on_err = c._results.get(timeout=5)
    c._finish_job(busy, result, err, done, on_err)
    assert got == ["ok"] and scr.working is None and c.stack == [scr]


def test_inline_job_error_leaves_the_pin_screen():
    c = _console()
    scr = PinScreen("UNLOCK", lambda c, pin: None)
    c.stack.append(scr)
    errs = []

    def boom():
        raise ValueError("wrong")
    c.job("UNLOCKING...", boom, lambda c, r: None, lambda c, e: errs.append(e), inline=scr)
    c._finish_job(*c._results.get(timeout=5))
    assert errs and scr.working is None and c.stack == [scr]


def test_plain_job_still_shows_the_busy_window():
    c = _console()
    c.job("SAVING...", lambda: 1, lambda c, r: None)
    assert isinstance(c.stack[-1], BusyScreen)
    c._finish_job(*c._results.get(timeout=5))
