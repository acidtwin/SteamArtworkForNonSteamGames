from steam_artwork_finder.gui import app as appmod
HAVE = "full"


def idx(h, name):
    return next(i for i, s in enumerate(h.win.sfile.shortcuts) if s.name == name)


def loaded(h, wait=12000):
    def ok():
        rows = h.win.page.rows.values()
        return all(r.thumbs and all(not v.pixmap.isNull() for v in r.thumbs) for r in rows)
    h.pump(wait, until=ok)
    h.pump(2500)


def run(h):
    w = h.win
    h.settle()
    w.set_library_view("grid"); h.pump(1500)
    h.shot("library")
    w.set_library_view("details"); h.pump(1200)
    h.shot("library-details")
    w.set_library_view("list"); h.pump(1200)
    h.shot("library-list")
    w.set_library_view("grid"); h.pump(800)
    w.game_list.open_filter_menu(); h.shot("menu-filter")
    for p in h.popups(): p.close()
    h.pump(300)
    w.open_game(idx(h, "Lumen Drift")); loaded(h)
    h.shot("game-page")
    w.open_tray(appmod.SLOTS["grid"]); h.pump(9000)
    h.shot("tray")
    w.tray.close_tray(); h.pump(800)
    w.open_game(idx(h, "Launcher")); loaded(h)
    h.shot("rename")
    # stage a few picks
    for name in ("Neon Requiem", "Starward Bound"):
        w.open_game(idx(h, name)); loaded(h, 8000)
        for key in ("grid", "hero"):
            r = w.page.rows[key]
            if r.assets:
                w.stage_pick(appmod.SLOTS[key], r.assets[2])
        h.pump(1500)
    h.shot("staged-game")
    w.go_home(); h.pump(1500)
    w.game_list.findChild(__import__("PySide6.QtWidgets", fromlist=["QAbstractScrollArea"]).QAbstractScrollArea).verticalScrollBar().setValue(0)
    h.shot("staged-library")
    w.show_steam_popover(); h.pump(900)
    h.shot("steam-popover")
