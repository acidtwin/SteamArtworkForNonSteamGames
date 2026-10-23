"""Shared steps for the REAL=1 scenes (your own library): find a game, wait for real downloads, point."""
import os
import sys
import time

hz = sys.modules["__main__"]
GAME = os.environ.get("GAME", "Cyberpunk")  # the game the close-ups and animations show


def idx(h, name=GAME):
    for i, s in enumerate(h.win.sfile.shortcuts):
        if name.casefold() in s.name.casefold():
            return i
    raise LookupError(f"no game called {name!r}: set GAME to one in your library")


def loaded(h, wait=25000):
    """Every visible option strip has its pictures (or the wait runs out: real downloads can stall)."""
    def ok():
        rows = h.win.page.rows.values()
        return all(r.thumbs and all(not v.pixmap.isNull() for v in r.thumbs if v.isVisible()) for r in rows)
    h.pump(wait, until=ok)
    h.pump(2500)


def animated_options(row):
    return [(v, a) for v, a in zip(row.thumbs, row.assets) if a.animated and v.isVisible()]


def hover(h, v, ms=500, dwell=900):
    h.move_to(h.at(v), ms)
    v.hovered.emit(True)
    h.pump(dwell)


def leave(v):
    v.hovered.emit(False)


def scroll_page_to(h, row_key, ms=700):
    """Glide the game page so `row_key`'s row sits near the top."""
    sb = h.win.page.scroll.verticalScrollBar()
    row = h.win.page.rows[row_key]
    target = min(sb.maximum(), max(0, row.mapTo(h.win.page.scroll.widget(), hz.QPoint(0, 0)).y() - 16))
    a, t0 = sb.value(), time.time()
    while True:
        f = min(1, (time.time() - t0) / (ms / 1000))
        sb.setValue(round(a + (target - a) * f * f * (3 - 2 * f)))
        h.pump(10)
        if f >= 1:
            break


def stage_animated(h, keys=("grid", "wide", "hero", "logo")):
    """Pick the top animated option for each piece that has one (waiting for Apply: nothing is written)."""
    w = h.win
    for key in keys:
        row = w.page.rows.get(key)
        anim = [a for a in (row.assets if row else []) if a.animated]
        if anim:
            w.stage_pick(hz.appmod.SLOTS[key], anim[0])
