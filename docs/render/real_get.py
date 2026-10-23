"""Get All Artwork (animated) fills a library that has no artwork yet, then Apply (REAL=1).

Run it with CONF='{"get_all_animated": true}' for the animated version."""
import sys
import time

hz = sys.modules["__main__"]
HAVE = "none"


def run(h):
    w = h.win
    h.settle()
    w.set_library_view("grid"); w.set_library_sort("az"); h.pump(1500)
    h.unfocus()
    h.cursor = h.at(w.start)
    h.record(fps=20, scale=0.5)
    h.pump(700)
    t = time.time()
    h.click(h.button("Get"), ms=900)
    h.pump(600)
    h.speed(float(hz.os.environ.get("GET_SPEED", 4)))  # real downloads take a while: play them faster
    h.pump(240000, until=lambda: not w.busy)
    h.pump(3000)  # covers fade in and start playing
    h.speed(1.0)
    print("fill took", time.time() - t, flush=True)
    h.pump(3000)
    h.click(h.button("Apply"), ms=800)
    h.pump(30000, until=lambda: not w.busy)
    h.pump(2500)
    h.save_anim("get-artwork", quality=82, hold_last=600)
