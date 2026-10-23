import time
import sys
hz = sys.modules["__main__"]
HAVE = "none"


def run(h):
    w = h.win
    for k in hz.LATENCY:
        hz.LATENCY[k] *= 0.2
    h.settle()
    w.set_library_view("grid"); h.pump(1500)
    h.unfocus()
    h.cursor = h.at(w.start) 
    h.record()
    h.pump(700)
    t = time.time()
    h.click(h.button("Get Artwork"), ms=900)
    h.pump(600)
    h.speed(1.6)
    h.pump(40000, until=lambda: not w.busy)
    h.speed(1.0)
    print("fill took", time.time() - t)
    h.pump(1200)
    h.click(h.button("Apply"), ms=800)
    h.pump(15000, until=lambda: not w.busy)
    h.pump(2200)
    h.save_anim("get-artwork")
