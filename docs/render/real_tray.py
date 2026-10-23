"""The See all panel: every cover for the game, animated ones playing; hover to preview, click to pick (REAL=1)."""
import sys

from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

import realkit as k
from steam_artwork_finder.gui.tray import ROLE_ASSET

hz = sys.modules["__main__"]
appmod = hz.appmod
HAVE = "full"


def run(h):
    w = h.win
    h.settle()
    w.open_game(k.idx(h)); k.loaded(h)
    h.unfocus()
    h.cursor = h.at(w.page.rows["grid"].art) + QPointF(140, 60)
    w.open_tray(appmod.SLOTS["grid"]); h.pump(15000)  # let the full-size animations load first
    w.tray.close_tray(); h.pump(1200)
    h.record(fps=20, scale=0.5)
    h.pump(500)
    w.open_tray(appmod.SLOTS["grid"]); h.pump(2500)
    lst = w.tray.list
    items = [lst.item(i) for i in range(lst.count()) if not lst.item(i).isHidden()]
    anim = [it for it in items if it.data(ROLE_ASSET).animated] or items
    for it in anim[:3]:
        r = lst.visualItemRect(it)
        h.move_to(QPointF(lst.viewport().mapTo(w, r.center())), 600)
        w.tray._hover(it)
        h.pump(2200)
    it = anim[0]
    r = lst.visualItemRect(it)
    h.move_to(QPointF(lst.viewport().mapTo(w, r.center())), 500)
    h.pressed = True; h.pump(90)
    QTest.mouseClick(lst.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, r.center())
    h.pressed = False
    h.pump(3000)
    h.save_anim("see-all", quality=82, hold_last=300)
