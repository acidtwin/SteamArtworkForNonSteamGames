"""The README's still pictures, from your own library (REAL=1)."""
import sys

import realkit as k

hz = sys.modules["__main__"]
appmod = hz.appmod
HAVE = "full"


def run(h):
    w = h.win
    h.settle()
    w.set_library_view("grid"); w.set_library_sort("az"); h.pump(5000)
    h.shot("library")
    w.set_library_view("details"); h.pump(2000)
    h.shot("library-details")
    w.set_library_view("grid"); h.pump(1500)
    w.game_list.open_filter_menu(); h.shot("menu-filter")
    for p in h.popups():
        p.close()
    h.pump(400)
    w.open_game(k.idx(h)); k.loaded(h)
    h.shot("game-page")
    w.show_get_menu(w.page.card.get_arrow); h.pump(900)
    h.shot("get-all-menu")
    for p in h.popups():
        p.close()
    h.pump(400)
    w.open_tray(appmod.SLOTS["grid"]); h.pump(15000)
    h.shot("tray")
    w.tray.close_tray(); h.pump(1000)
    k.stage_animated(h, ("grid", "hero", "logo")); h.pump(4000)
    h.shot("staged-game")
    w.go_home(); h.pump(2500)
    h.shot("staged-library")
    w.show_steam_popover(); h.pump(1000)
    h.shot("steam-popover")
