import sys
hz = sys.modules["__main__"]
HAVE = "full"


def run(h):
    w = h.win
    h.settle()
    w.set_library_view("grid")
    w.set_library_sort("az")
    h.pump(4000)
    h.unfocus()
    h.record(fps=25, scale=0.5)
    h.pump(24 * 70 * 2)  # two loops of the 24-frame covers
    # the content area only: the cover wall
    h.save_anim("animated-covers", crop=(300, 64, 980, 700), hold_last=40)
