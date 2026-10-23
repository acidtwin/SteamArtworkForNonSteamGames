"""A game's page with its animated cover, banner and logo playing (REAL=1)."""
import realkit as k

HAVE = "full"


def run(h):
    w = h.win
    h.settle()
    w.open_game(k.idx(h)); k.loaded(h)
    k.stage_animated(h)  # the top animated options, waiting for Apply (nothing is written)
    h.pump(20000, until=lambda: False)  # full-size animations download and decode
    h.unfocus()
    h.record(fps=25, scale=0.5)
    h.pump(7000)
    h.save_anim("animated-page", crop=(300, 64, 980, 470), hold_last=40)
