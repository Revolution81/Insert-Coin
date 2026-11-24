"""Text always fits its window: wrapped by real pixel width, reflowed when a
line is too long, and cut into pages (LEFT/RIGHT) when there's too much."""
from test_app import app, enter, press  # noqa: F401
from ui.theme import BODY
from ui.widgets import MessageScreen, paginate, wrap_px


def test_wrap_by_pixels_and_cut_long_words():
    for line in wrap_px("Say it to the sender by phone, not with the QR. " * 3, 140):
        assert BODY.width(line) <= 140
    addr = "tb1qx9w0vx6gh0ugzh7pdts5wavcd7440c6zclfd84"
    assert "".join(wrap_px(addr, 100)) == addr and all(BODY.width(x) <= 100 for x in wrap_px(addr, 100))


def test_paragraphs_reflow_and_pages_break_at_blank_lines():
    pages = paginate(["Tell the sender these", "digits another way (a call)", "", "Then scan theirs."], 150, 4)
    assert all(BODY.width(x) <= 150 for p in pages for x in p)
    assert "these" not in [x.strip() for p in pages for x in p]       # no one-word orphan
    assert all(p and p[0] != "" for p in pages)
    short = paginate(["ONE", "TWO"], 300, 8)
    assert short == [["ONE", "TWO"]]                                    # lines that fit stay as written


def test_long_message_pages(app):  # noqa: F811
    closed = []
    lines = [f"Line number {i} of a long message" for i in range(20)]
    app.push(MessageScreen("LONG", lines, then=lambda a: closed.append(1)))
    scr = app.visible
    n = len(scr.pages())
    assert n >= 2 and sum(len(p) for p in scr.pages()) == 20
    app.render(0)
    press(app, "KEY_C")                              # RIGHT: next page
    assert scr.page == 1
    press(app, "KEY_Z")                              # LEFT: back
    assert scr.page == 0
    for _ in range(n - 1):
        enter(app)                                   # OK: next page
    assert scr.page == n - 1 and not closed
    enter(app)                                       # OK on the last page closes
    assert closed == [1]
