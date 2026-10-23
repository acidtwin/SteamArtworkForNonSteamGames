"""Hover options on a game's page to preview them, pick an animated cover, then Apply (REAL=1)."""
import sys

import realkit as k

hz = sys.modules["__main__"]
HAVE = "full"


def run(h):
    w = h.win
    h.settle()
    w.open_game(k.idx(h)); k.loaded(h)
    h.unfocus()
    row = w.page.rows["grid"]
    vis = [v for v in row.thumbs if v.isVisible()]
    anim = [v for v, a in k.animated_options(row)]
    h.cursor = h.at(row.art) + hz.QPointF(120, 140)
    h.record(fps=20, scale=0.5)
    h.pump(700)
    for v in [x for x in vis if x not in anim][:2]:
        k.hover(h, v, 550, 1000)
        k.leave(v)
    pick = anim[0] if anim else vis[0]
    k.hover(h, pick, 450, 2600)  # an animated one plays in place
    h.click(pick, ms=150)
    h.pump(2500)
    h.click(h.button("Apply"), ms=800)
    h.pump(15000, until=lambda: not w.busy)
    h.pump(2500)
    h.save_anim("pick-artwork", quality=82, hold_last=200)
