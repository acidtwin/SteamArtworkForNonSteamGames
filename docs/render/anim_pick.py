import sys
hz = sys.modules["__main__"]
HAVE = "full"
appmod = hz.appmod


def idx(h, name):
    return next(i for i, s in enumerate(h.win.sfile.shortcuts) if s.name == name)


def loaded(h, wait=15000):
    ok = lambda: all(r.thumbs and all(not v.pixmap.isNull() for v in r.thumbs) for r in h.win.page.rows.values())
    h.pump(wait, until=ok)
    h.pump(1500)


def hover(h, v, ms=500, dwell=700):
    h.move_to(h.at(v), ms)
    v.hovered.emit(True)
    h.pump(dwell)


def leave(h, v):
    v.hovered.emit(False)


def scroll_to(h, value, ms=700):
    sb = h.win.page.scroll.verticalScrollBar()
    import time
    a, t0 = sb.value(), time.time()
    while True:
        f = min(1, (time.time() - t0) / (ms / 1000))
        sb.setValue(round(a + (value - a) * f * f * (3 - 2 * f)))
        h.pump(10)
        if f >= 1:
            break


def run(h):
    w = h.win
    h.settle()
    w.open_game(idx(h, "Starward Bound"))
    loaded(h)
    h.unfocus()
    row = w.page.rows["grid"]
    vis = [v for v in row.thumbs if v.isVisible()]
    h.cursor = h.at(w.page.rows["grid"].art) + hz.QPointF(120, 140)
    h.record()
    h.pump(600)
    for v in (vis[2], vis[3]):
        hover(h, v, 550, 900)
        leave(h, v)
    hover(h, vis[1], 400, 500)
    h.click(vis[1], ms=150)
    h.pump(2200)
    h.click(h.button("Apply"), ms=800)
    h.pump(8000, until=lambda: not w.busy)
    h.pump(2600)
    h.save_anim("pick-artwork", hold_last=200)
