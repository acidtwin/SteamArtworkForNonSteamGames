"""Offscreen tests: the window builds against a synthetic Steam install, and artwork is staged then applied."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7V\xbd\xfa\x00\x00\x00\x00IEND\xaeB`\x82")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from steam_artwork_finder.gui import theme

    sheet = theme.stylesheet()
    if app.styleSheet() != sheet:  # setting it again repolishes every widget earlier modules left behind
        app.setStyleSheet(sheet)
    return app


def _pump(app, ms=300, until=None):
    import time

    t = time.time()
    while time.time() - t < ms / 1000:
        app.processEvents()
        if until is not None and until():
            return


@pytest.fixture
def window(qapp, steam_root, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    from steam_artwork_finder.gui import app as appmod

    monkeypatch.setattr(appmod, "steam_running", lambda root=None: False)
    win = appmod.MainWindow()
    win.steam.search = lambda term: []  # no Steam store lookups from tests unless a test sets them up
    win.steam.assets = lambda appid: {}

    def offline(url):
        raise OSError("no network in tests")

    monkeypatch.setattr(win.sgdb, "download", offline)  # nothing reaches the internet
    win.resize(1280, 800)
    win.show()
    win.detect_steam([steam_root])
    _pump(qapp)
    yield win
    win.pending.clear()
    win.close()


def test_window_shows_games_and_pages(qapp, window):
    from PySide6.QtCore import Qt

    win = window
    assert win.stage.currentWidget() is win.start
    assert win.game_list.list.count() == 3
    assert win.game_list.list.item(0).text() == "Celeste"
    assert "Celeste" in win.game_list.list.item(0).data(Qt.ItemDataRole.AccessibleTextRole)
    assert [b.text() for b in win.cbar.buttons] == ["Get Artwork (3 Games)"]
    assert win.statusbar.steam_found
    assert win.minimumSizeHint().width() <= 1280

    win.open_game(1)
    _pump(qapp)
    assert win.stage.currentWidget() is win.page
    assert win.page.card.state == "key" and win.page.card.key_btn.isVisible()  # no API key: the card asks for one
    card = win.page.card
    assert not win.cbar.isVisible() and card.title.text() == "Hollow Knight™"
    assert card.apply_btn.text() == "Apply" and not card.apply_btn.isEnabled()
    for b in [win.page.rows["grid"].see_all, card.get_btn, card.apply_btn]:
        assert b.height() >= 40, b.text()

    win.game_list.set_filter("missing")
    assert win.game_list.filter_mode == "missing"
    win.detect_steam([])
    _pump(qapp)
    assert win.stage.currentWidget() is win.nosteam and win.sfile is None
    assert not win.statusbar.steam_found


def test_picks_wait_until_apply(qapp, window):
    from steam_artwork_finder.artwork import SLOTS, existing
    from steam_artwork_finder.sgdb import Asset

    win = window
    win.sgdb.download = lambda url: PNG
    win.open_game(0)
    _pump(qapp)
    sc = win.current_shortcut()
    asset = Asset(7, "https://x/cover.png", "https://x/t.png", 600, 900, "image/png", 0, "", "someone")
    win.stage_pick(SLOTS["grid"], asset)
    assert win.pending.count(sc.appid) == 1
    assert existing(win.user.grid_dir, sc.appid, SLOTS["grid"]) is None  # nothing written yet
    assert win.page.card.apply_btn.text() == "Apply (1 Change)"

    win.go_home()
    _pump(qapp)
    assert [b.text() for b in win.cbar.buttons] == ["Get Artwork (3 Games)", "Apply (1 Game)"]
    assert win.statusbar.kind is None  # the sidebar and Apply button already say so

    win.apply_all()
    _pump(qapp, 3000, until=lambda: not win.busy)
    assert not win.pending
    path = existing(win.user.grid_dir, sc.appid, SLOTS["grid"])
    assert path is not None and path.read_bytes() == PNG
    assert win.config.get_applied(sc.appid, "grid")["id"] == 7

    win.toast.undo_now()
    assert existing(win.user.grid_dir, sc.appid, SLOTS["grid"]) is None


def test_clear_waits_until_apply(qapp, window):
    from steam_artwork_finder.artwork import SLOTS, existing

    win = window
    win.open_game(0)
    _pump(qapp)
    sc = win.current_shortcut()
    win.user.grid_dir.mkdir(parents=True, exist_ok=True)
    (win.user.grid_dir / f"{sc.appid}_hero.png").write_bytes(PNG)
    win.stage_clear(SLOTS["hero"])
    assert win.pending.get(sc.appid, "hero").clears
    assert existing(win.user.grid_dir, sc.appid, SLOTS["hero"]) is not None
    win.apply_games([sc.appid])
    _pump(qapp, 3000, until=lambda: not win.busy)
    assert existing(win.user.grid_dir, sc.appid, SLOTS["hero"]) is None


def test_games_are_matched_at_startup(qapp, window):
    from steam_artwork_finder.sgdb import Game

    win = window
    win.config.data["api_key"] = "x"
    win.sgdb.match = lambda name: (Game(9, name + " Remastered", "2020"), 4)  # never an exact name
    win.set_connection("connected")
    _pump(qapp, 3000, until=lambda: not win._matching and len(win.guesses) == 3)
    assert set(win.status.values()) == {"check"}
    assert not win.config.get_match(win.sfile.shortcuts[0].appid)  # guesses wait for the user


def test_status_bar_shows_matching_and_reloads(qapp, window, monkeypatch):
    from steam_artwork_finder.sgdb import Game

    win = window
    shown = []
    real = win.statusbar.set_message
    monkeypatch.setattr(win.statusbar, "set_message", lambda kind, text="": (shown.append(text), real(kind, text)))
    win.config.data["api_key"] = "x"
    win.sgdb.match = lambda name: (Game(9, name + " Remastered", "2020"), 4)
    win.set_connection("connected")
    _pump(qapp, 3000, until=lambda: not win._matching and len(win.guesses) == 3)
    assert "Matching games on SteamGridDB · 0 of 3" in shown
    assert "Matching games on SteamGridDB · 3 of 3" in shown
    assert win.statusbar.message.text() == ""  # quiet again once it's done

    win.sfile.shortcuts.pop()  # as if a game was added in Steam since the list was read
    win.reload_game_list()
    assert win.statusbar.message.accessibleName() == "Game list reloaded · 3 games, 1 new"
    win._note_timer.timeout.emit()
    assert win.statusbar.message.text()  # it fades out, rather than vanishing
    win.refresh_all()  # a refresh meanwhile doesn't cut the fade short
    assert win.statusbar.message.text()
    _pump(qapp, 1000, until=lambda: not win.statusbar.message.text())
    assert win.statusbar.message.text() == "" and not win.statusbar._msg_fx.isEnabled()


def test_get_artwork_result_goes_to_the_status_bar(qapp, window, monkeypatch):
    win = window
    win.config.data["api_key"] = "x"
    report = {"found": 2, "notfound": [], "gaps": 4, "errors": [], "unsure": 0, "stopped": False}
    monkeypatch.setattr(win, "_fetch_missing", lambda jobs, progress, **kw: dict(report))
    win.fill_everything()
    _pump(qapp, 3000, until=lambda: not win.busy)
    assert win.notice.isHidden()  # no banner when there's nothing to act on
    assert win.statusbar.message.accessibleName() == \
        "Found artwork for 2 games, ready to apply · 4 pieces had no options yet"
    assert win._note_timer.interval() == 8000

    report.update(found=0, gaps=0)
    win.fill_everything()
    assert win._note is None  # new work clears the old result
    _pump(qapp, 3000, until=lambda: not win.busy)
    assert win.statusbar.message.accessibleName() == "No new artwork found"
    assert not win._note_timer.isActive()  # stays until the next piece of work

    report.update(notfound=["Celeste"])
    win.fill_everything()
    _pump(qapp, 3000, until=lambda: not win.busy)
    assert not win.notice.isHidden()  # something to act on keeps the banner


def test_game_menu_opens_the_game(qapp, window):
    from PySide6.QtCore import QPoint

    win = window
    win.show_game_menu(2, QPoint(100, 100))
    _pump(qapp, 200)
    menu = win._game_menu
    assert [r.text() for r in menu.rows][-2:] == ["Open Game Page", "Remove All Artwork…"]
    next(r for r in menu.rows if r.text() == "Open Game Page").click()
    _pump(qapp, 200)
    assert win.stage.currentWidget() is win.page and win.current == 2


def test_right_click_in_the_list_keeps_the_open_page(qapp, window):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QContextMenuEvent
    from PySide6.QtTest import QTest

    win = window
    win.open_game(0)
    _pump(qapp, 200)
    lst = win.game_list.list
    target = next(lst.item(r) for r in range(lst.count()) if lst.item(r) is not lst.currentItem())
    pos = lst.visualItemRect(target).center()
    asked = []
    lst.menu_requested.connect(lambda i, p: asked.append(i))
    QTest.mousePress(lst.viewport(), Qt.MouseButton.RightButton, pos=pos)
    qapp.sendEvent(lst.viewport(), QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pos, lst.viewport().mapToGlobal(pos)))
    QTest.mouseRelease(lst.viewport(), Qt.MouseButton.RightButton, pos=pos)
    _pump(qapp, 400)
    assert win.current == 0 and win.stage.currentWidget() is win.page
    assert lst.currentItem() is not target
    assert len(asked) == 1 and asked[0] != 0
    win._game_menu.close()


def test_steam_folder_popover_searches_and_switches(qapp, window, tmp_path, steam_root):
    import shutil

    from steam_artwork_finder.gui import steamfolder

    win = window
    pop = win.folderpop
    pop.delay = 0
    flatpak = tmp_path / ".var/app/com.valvesoftware.Steam/.local/share/Steam"
    shutil.copytree(steam_root, flatpak)
    h0 = None
    win.statusbar.steam.click()
    _pump(qapp, 1000, until=lambda: pop.title.text() == "Using your chosen folder")
    assert pop.is_open() and pop.pages.currentIndex() == 0
    assert not pop.searching and pop.found == []  # a folder is in use, so it doesn't search by itself
    assert pop.title.text() == "Using your chosen folder"
    h0 = pop.height()
    pop.again.click()  # Detect Again
    assert pop.searching
    _pump(qapp, 2000, until=lambda: not pop.searching)
    assert not pop.searching
    assert [f.place.short for f in pop.found] == ["Flatpak"]  # the test's Steam lives outside the usual places
    assert pop.dots.states[1] == "hit" and pop.dots.states[2] == "miss"
    assert pop.title.text() == "Using your chosen folder" and pop.found[0].games == 3
    assert pop.account.name.text() == "Deck User" and pop.account.switch.isEnabled()

    pop.show_tab(1)
    _pump(qapp, 50)
    assert pop.height() == h0  # the tabs are the same size
    pop._chosen(tmp_path / "nowhere")  # checked on a worker
    _pump(qapp, 2000, until=lambda: "userdata" in pop.hint.text() and "doesn't" in pop.hint.text())
    assert "doesn't" in pop.hint.text()
    pop._chosen(flatpak)
    _pump(qapp, 2000, until=lambda: pop.title.text() == "Using Steam (Flatpak)")
    assert win.steam_root == flatpak.resolve()
    assert win.config.data["steam_folder"] == str(flatpak.resolve())
    assert pop.title.text() == "Using Steam (Flatpak)" and pop.pages.currentIndex() == 0
    assert pop.height() == h0

    win.statusbar.steam.click()  # a second click closes it
    _pump(qapp, 400)
    assert not pop.isVisible()
    assert steamfolder.tilde(tmp_path / "x") == "~/x"


def test_steam_folder_popover_searches_only_when_no_folder_in_use(qapp, window, tmp_path, steam_root):
    import shutil

    win = window
    pop = win.folderpop
    pop.delay = 0
    flatpak = tmp_path / ".var/app/com.valvesoftware.Steam/.local/share/Steam"
    shutil.copytree(steam_root, flatpak)
    win.use_steam_root(flatpak.resolve())
    _pump(qapp)
    for _ in range(2):  # opening it again and again never searches while a folder is in use
        win.show_folder_popover()
        _pump(qapp, 1000, until=lambda: pop.title.text() == "Using Steam (Flatpak)")
        assert pop.is_open() and not pop.searching
        assert pop.title.text() == "Using Steam (Flatpak)" and pop.found[0].games == 3
        assert pop.dots.states[1] == "hit" and pop.dots.states[0] == ""  # the others weren't checked
        win.show_folder_popover()
        _pump(qapp, 400)

    win.detect_steam([])  # no Steam folder in use, as on a first launch
    _pump(qapp)
    win.show_folder_popover()
    assert pop.searching
    _pump(qapp, 2000, until=lambda: not pop.searching)
    assert win.steam_root == flatpak.resolve()  # the search found it and put it to use
    win.show_folder_popover()
    _pump(qapp, 400)


def test_steam_folder_popover_keeps_disk_checks_off_the_ui_thread(qapp, window, tmp_path, steam_root, monkeypatch):
    import shutil
    import threading

    from steam_artwork_finder.gui import steamfolder

    win = window
    pop = win.folderpop
    pop.delay = 0
    flatpak = tmp_path / ".var/app/com.valvesoftware.Steam/.local/share/Steam"
    shutil.copytree(steam_root, flatpak)
    threads = []
    real_places, real_roots = steamfolder.search_places, steamfolder.find_steam_roots
    monkeypatch.setattr(steamfolder, "search_places",
                        lambda: threads.append(threading.current_thread()) or real_places())
    monkeypatch.setattr(steamfolder, "find_steam_roots",
                        lambda c: threads.append(threading.current_thread()) or real_roots(c))

    fresh = steamfolder.SteamFolderPopover(win)  # building it lists no drives
    assert fresh.places == [] and threads == []
    fresh.deleteLater()
    win.use_steam_root(flatpak.resolve())  # which place it's in is looked up on a worker
    _pump(qapp, 2000, until=lambda: pop.title.text() == "Using Steam (Flatpak)")
    assert pop.found[0].games == 3
    win.show_folder_popover()
    _pump(qapp, 200)
    pop.again.click()  # Detect Again lists the places on the worker too
    _pump(qapp, 2000, until=lambda: not pop.searching)
    assert [pl.short for pl in pop.places][:2] == ["Steam", "Flatpak"] and pop.dots.states[1] == "hit"
    pop.show_tab(1)
    pop._chosen(steam_root)
    _pump(qapp, 2000, until=lambda: win.steam_root == steam_root.resolve())
    assert threads and threading.main_thread() not in threads

    # A folder chosen just before the pop-over closes isn't put to use once it has.
    emitted = []
    pop.use_root.connect(emitted.append)
    pop.show_tab(1)
    pop._chosen(flatpak)
    pop.dismiss()
    _pump(qapp, 400)
    assert emitted == [] and win.steam_root == steam_root.resolve()
    pop.use_root.disconnect(emitted.append)


def test_steam_folder_popover_path_fits_on_first_open(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QWidget

    from steam_artwork_finder.gui import steamfolder

    monkeypatch.setenv("HOME", str(tmp_path))
    host = QWidget()
    host.resize(1000, 800)
    host.show()
    pop = steamfolder.SteamFolderPopover(host)
    root = tmp_path / "Games" / "SteamLibraryOnTheBigDrive" / "Steam"
    pop._located[root] = None  # not in any of the usual places, so no lookup
    pop.set_current(root, ["Deck User"], 0, None)  # before it has ever been shown or laid out
    assert "…" not in pop.sub.text()  # the full path fits; it used to be cut to 80 px
    pop.open_above(host.mapToGlobal(QPoint(500, 700)))
    _pump(qapp, 200)
    assert pop._sub_room() == pop.sub.width()  # the width it worked out is the one it gets
    pop.dismiss()
    _pump(qapp, 300)
    host.deleteLater()


def test_search_dots_dont_animate_while_hidden(qapp):
    from PySide6.QtCore import QVariantAnimation
    from PySide6.QtWidgets import QWidget

    from steam_artwork_finder.gui.steamfolder import Dots, Pulse

    box = QWidget()
    dots, pulse = Dots(box), Pulse(box)
    dots.reset(["Steam", "Flatpak"])
    dots.set(0, "now")  # hidden: a search going on behind a closed pop-over
    pulse.set_state("search")
    assert dots._anim.state() != QVariantAnimation.State.Running
    assert pulse._anim.state() != QVariantAnimation.State.Running
    box.show()
    assert dots._anim.state() == QVariantAnimation.State.Running
    assert pulse._anim.state() == QVariantAnimation.State.Running
    box.hide()
    dots.set(1, "now")
    assert dots._anim.state() != QVariantAnimation.State.Running
    box.deleteLater()


def test_quit_prompt_lists_games_and_remembers(qapp, window, monkeypatch):
    from steam_artwork_finder.artwork import SLOTS
    from steam_artwork_finder.gui import dialogs
    from steam_artwork_finder.sgdb import Asset

    win = window
    win.open_game(0)
    _pump(qapp)
    asset = Asset(7, "https://x/cover.png", "https://x/t.png", 600, 900, "image/png", 0, "", "someone")
    win.stage_pick(SLOTS["grid"], asset)
    win.stage_pick(SLOTS["logo"], asset)
    seen = {}

    def fake_exec(dlg):
        seen["text"] = [w.text() for w in dlg.findChildren(dialogs.QLabel)]
        dlg.remember.setChecked(True)
        dlg._done("apply")
        return 1

    monkeypatch.setattr(dialogs.QuitDialog, "exec", fake_exec)
    assert win._ask_on_quit() == "apply"
    assert "Celeste" in seen["text"] and "New cover and logo" in seen["text"] and "2 CHANGES" in seen["text"]
    assert win.apply_on_quit and win.popover.on_quit.isChecked()
    win.popover.on_quit.setChecked(False)  # turned back off from the Steam pop-over
    assert not win.apply_on_quit


def test_more_menu_has_sections_and_switches(qapp, window):
    from steam_artwork_finder.gui.gamemenu import MenuRow, Section

    win = window
    win.header.more.click()
    _pump(qapp, 100)
    menu = win._more_menu
    assert [s.text for s in menu.findChildren(Section)] == ["LIBRARY", "SETTINGS", "HELP"]
    rows = {r.text(): r for r in menu.findChildren(MenuRow)}
    assert rows["Reload Game List"].item.shortcut == "Ctrl+R"
    quit_row = rows["Apply When I Quit"]
    assert not quit_row.isChecked()
    quit_row.click()
    assert win.apply_on_quit and win.popover.on_quit.isChecked() and menu.isVisible()  # a switch keeps it open
    menu.close()


def test_restore_window_says_what_changes(qapp, window, monkeypatch):
    import time as _t

    from steam_artwork_finder.gui import dialogs
    from steam_artwork_finder.shortcuts import ShortcutsFile

    win = window
    sf = win.sfile
    first = sf.backup()  # a copy with all three games
    sc = sf.shortcuts[0]
    sc.entry.set_string("AppName", "Renamed")
    sf.save()
    sf.reload()
    copies = sf.backups()
    dlg = dialogs.RestoreDialog(win, copies, sf.shortcuts, steam_open=True)
    assert dlg.ok.text() == "Close Steam and Restore" and not dlg.ok.isEnabled()
    texts = [w.text() for w in dlg.findChildren(dialogs.QLabel)]
    assert "1 NAME DIFFERENT" in texts
    dlg.rows[-1].click()
    assert dlg.selected() == copies[-1][0] and dlg.ok.isEnabled()
    now = _t.mktime((2026, 10, 19, 6, 44, 0, 0, 0, -1))
    assert dialogs.friendly_time(now - 14 * 60, now) == ("Today, 06:30", "14 minutes ago")
    assert dialogs.friendly_time(now - 9 * 3600, now)[0] == "Yesterday, 21:44"
    assert ShortcutsFile(first).shortcuts[0].name != "Renamed"


def test_app_opens_on_the_library(qapp, window):
    from steam_artwork_finder.gui.app import focus_start

    win = window
    focus_start(win)
    win.activateWindow()
    _pump(qapp, 600)
    assert win.stage.currentWidget() is win.start and win.current == -1


def test_match_window_suggests_and_previews(qapp):
    from steam_artwork_finder.gui.dialogs import MatchDialog
    from steam_artwork_finder.sgdb import Asset, Game

    games = [Game(1, "Super Bomberman R", "2017", True), Game(2, "Super Bomberman R 2", "2023", True)]

    class Fake:
        searched: list = []

        def search(self, term):
            self.searched.append(term)
            return games if term.startswith("Super") else []

        def assets(self, kind, gid, page=0):
            n = {"grid": 60, "hero": 3, "logo": 2, "icon": 1}[kind]
            return [Asset(i, f"u{kind}{gid}{i}", f"u{kind}{gid}{i}", 1, 1, "", 0, "", "") for i in range(n)]

        def download(self, url):
            return PNG

    fake = Fake()
    dlg = MatchDialog(None, fake, "SB.exe", "/home/a/Games/Super Bomberman R/SB.exe")
    _pump(qapp, 3000, until=lambda: ">1</b>" in dlg.stats["icon"].text())
    assert fake.searched[0] == "Super Bomberman R"  # never "SB.exe"
    assert dlg.tip is not None and [c.term for c in dlg.chips] == ["Super Bomberman R", "SB"]
    assert dlg.selected().id == 1 and dlg.status.text() == "2 results"
    assert "50+" in dlg.stats["grid"].text() and "covers" in dlg.stats["grid"].text()
    dlg.rows[1].setChecked(True)
    assert dlg.selected().id == 2 and dlg.p_name.text() == "Super Bomberman R 2"
    dlg.chips[1].click()
    _pump(qapp, 600, until=lambda: dlg.status.text().startswith("No"))
    assert dlg.edit.text() == "SB" and dlg.selected() is None and not dlg.ok.isEnabled()
    dlg.close()


def test_wheel_scrolling_glides(qapp):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication, QScrollArea, QWidget

    from steam_artwork_finder.gui import smooth

    f = smooth.install(qapp)
    try:
        area = QScrollArea()
        inner = QWidget()
        inner.setFixedSize(200, 3000)
        area.setWidget(inner)
        area.resize(220, 300)
        area.show()
        bar = area.verticalScrollBar()

        def wheel(dy, pixels=0, phase=Qt.ScrollPhase.NoScrollPhase):
            ev = QWheelEvent(QPointF(50, 50), QPointF(area.viewport().mapToGlobal(QPoint(50, 50))), QPoint(0, pixels),
                             QPoint(0, dy), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, phase, False)
            QApplication.sendEvent(area.viewport(), ev)

        wheel(-120)
        _pump(qapp, 40)
        mid = bar.value()
        _pump(qapp, 800)
        step = QApplication.wheelScrollLines() * bar.singleStep()
        assert 0 < mid < step and bar.value() == step  # it glides there, it doesn't jump
        wheel(-120)
        wheel(-120)
        _pump(qapp, 800)
        assert bar.value() == 3 * step  # quick notches add up
        wheel(-120, pixels=-40)  # a mouse wheel that also reports pixels (KDE on Wayland)
        _pump(qapp, 40)
        assert 3 * step < bar.value() < 4 * step
        _pump(qapp, 800)
        assert bar.value() == 4 * step
        before = bar.value()
        wheel(-120, pixels=-40, phase=Qt.ScrollPhase.ScrollUpdate)  # a touchpad gesture: left to Qt, no glide
        assert bar.findChild(smooth._Glide).running() is False and bar.value() != before
        area.close()
    finally:
        qapp.removeEventFilter(f)


def test_home_bar_switches_library_view(qapp, window):
    bar = window.game_list.homebar
    assert bar.home.on and window.start.wall.view == "grid"
    window.open_game(0)
    _pump(qapp, 100)
    assert not bar.home.on
    bar.menu.chosen.emit("details")  # as if picked from the view menu
    _pump(qapp, 100)
    assert bar.home.on and window.stage.currentWidget() is window.start
    assert window.start.wall.view == "details" and window.start.header.isVisible()
    assert bar.view.view == "details" and window.config.data["library_view"] == "details"
    bar.menu.chosen.emit("list")
    assert window.start.wall.view == "list" and not window.start.header.isVisible()
    window.open_game(1)
    bar.home.click()
    _pump(qapp, 100)
    assert window.stage.currentWidget() is window.start and window.current < 0


def _asset(n, mime="image/png"):
    from steam_artwork_finder.sgdb import Asset

    return Asset(n, f"https://x/{n}.png", f"https://x/t{n}.png", 600, 900, mime, 0, "", "someone")


@pytest.fixture
def steam_state(window, monkeypatch):
    """Steam's running state, switchable by the test; launches are recorded, not made."""
    from steam_artwork_finder.gui import app as appmod

    state = {"running": False, "calls": []}
    monkeypatch.setattr(appmod, "steam_running", lambda root=None: state["running"])

    def shutdown(root=None):
        state["calls"].append("shutdown")
        state["running"] = False
        return True

    monkeypatch.setattr(appmod, "shutdown_steam", shutdown)
    monkeypatch.setattr(appmod, "wait_until_closed", lambda root=None: not state["running"])
    monkeypatch.setattr(appmod, "start_steam", lambda root=None: state["calls"].append("start") or True)
    return state


def test_one_bad_download_keeps_the_rest_and_downloads_before_closing_steam(qapp, window, steam_state):
    from steam_artwork_finder.artwork import SLOTS, existing
    from steam_artwork_finder.pending import Pick
    from steam_artwork_finder.sgdb import SGDBError

    win = window
    a, b = win.sfile.shortcuts[0], win.sfile.shortcuts[2]

    def download(url):
        if "/t" in url:
            return PNG  # a thumbnail for the waiting pick
        steam_state["calls"].append("download")
        if url.endswith("/2.png"):
            raise SGDBError("Download failed: 404")
        return PNG

    win.sgdb.download = download
    win._fetch_pick = lambda key, pick: None  # as if picked offline: Apply does the downloads
    win._stage(a, "grid", Pick(_asset(1)))
    win._stage(b, "grid", Pick(_asset(2)))
    steam_state["running"] = True
    assert win.apply_all()
    _pump(qapp, 3000, until=lambda: not win.busy)
    assert steam_state["calls"] == ["download", "download", "shutdown", "start"]
    assert existing(win.user.grid_dir, a.appid, SLOTS["grid"]) is not None
    assert existing(win.user.grid_dir, b.appid, SLOTS["grid"]) is None
    assert win.pending.games() == [b.appid]  # only the failed pick still waits
    assert win.last_errors == ["Pokémon émulé cover: Download failed: 404"]

    # When nothing could be downloaded, Steam is left open.
    steam_state["calls"].clear()
    steam_state["running"] = True
    win.apply_all()
    _pump(qapp, 3000, until=lambda: not win.busy)
    assert steam_state["calls"] == ["download"]


def test_a_clicked_pick_shows_its_full_file_and_apply_reuses_it(qapp, window, steam_state):
    from pathlib import Path

    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QImage

    from steam_artwork_finder.artwork import SLOTS, existing
    from steam_artwork_finder.pending import Pick

    img = QImage(64, 96, QImage.Format.Format_ARGB32)
    img.fill(0xFF336699)
    buf = QBuffer(ba := QByteArray())
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    full = bytes(ba)
    calls = []

    def download(url):
        calls.append(url)
        return PNG if "/t" in url else full  # a 1 px thumbnail, a 64 x 96 full file

    win = window
    win.sgdb.download = download
    sc = win.sfile.shortcuts[0]
    pick = Pick(_asset(7))
    win._stage(sc, "grid", pick)
    _pump(qapp, 3000, until=lambda: (pm := win.pick_pix.get((sc.appid, "grid"))) is not None and pm.width() == 64)
    assert win.pick_pix[(sc.appid, "grid")].size().width() == 64  # the full file, not the thumbnail
    assert pick.fetched and Path(pick.cached).is_file()

    calls.clear()
    win.apply_games([sc.appid])
    _pump(qapp, 3000, until=lambda: not win.busy)
    assert calls == []  # Apply used the download it already had
    assert existing(win.user.grid_dir, sc.appid, SLOTS["grid"]) is not None
    assert Path(pick.cached).is_file()  # the cache's copy is left for See all and later picks


def test_switching_and_quitting_wait_while_busy(qapp, window):
    from steam_artwork_finder.pending import Pick

    win = window
    sfile, user = win.sfile, win.user
    win._set_busy("apply", "Applying…")
    assert not win.statusbar.steam.isEnabled()
    win.reload_shortcuts()
    win._account_changed(1)
    assert win.sfile is sfile and win.user is user
    assert win.toast.label.text() == "Wait until the artwork has been applied."

    win._set_busy("fetch", "Getting artwork…")
    win.pending.set(sfile.shortcuts[0].appid, "grid", Pick(None))
    win.close()
    assert win.isVisible() and not win._close_after_apply  # a later close still asks first
    win._set_busy("")
    assert win.statusbar.steam.isEnabled()


def test_undo_with_steam_open_waits_to_put_the_icon_back(qapp, window, steam_state):
    from steam_artwork_finder.pending import Pick
    from steam_artwork_finder.shortcuts import ShortcutsFile

    win = window
    win.config.data["auto_restart_steam"] = False
    sc = win.sfile.shortcuts[1]  # icon "/old/icon.png"
    win._stage(sc, "icon", Pick(_asset(3), PNG))
    win.apply_games([sc.appid])
    _pump(qapp, 3000, until=lambda: not win.busy)
    path = win.sfile.path
    new_icon = ShortcutsFile(path).shortcuts[1].icon
    assert new_icon.endswith("_icon.png")

    steam_state["running"] = True  # the user opened Steam, then pressed Undo
    win.toast.undo_now()
    assert ShortcutsFile(path).shortcuts[1].icon == new_icon  # not written while Steam runs
    assert win.pending_icons() == {str(sc.appid): "/old/icon.png"}
    assert win.steam_state() == "restart"

    steam_state["running"] = False
    win.update_steam_state()
    assert ShortcutsFile(path).shortcuts[1].icon == "/old/icon.png"
    assert win.pending_icons() == {}


def test_waiting_icons_wait_for_their_own_account(qapp, window, steam_state):
    import os

    from steam_artwork_finder.shortcuts import ShortcutsFile

    win = window
    sc = win.sfile.shortcuts[0]
    other = win.user.shortcuts_path.parent.parent.parent / "87654321/config/shortcuts.vdf"
    win.config.set_pending_icon(other, sc.appid, "/other/grid/icon.png")
    win.update_steam_state()
    assert win.config.pending_icons(other) == {str(sc.appid): "/other/grid/icon.png"}  # kept for that account
    assert ShortcutsFile(win.sfile.path).shortcuts[0].icon == ""

    # Linking reloads the list Steam rewrote on exit, and the views follow it.
    win.open_game(2)
    _pump(qapp)
    win.config.set_pending_icon(win.sfile.path, sc.appid, "/mine/icon.png")
    win.sfile.path.write_bytes(win.sfile.path.read_bytes())
    os.utime(win.sfile.path, ns=(1, 1))
    win.update_steam_state()
    assert ShortcutsFile(win.sfile.path).shortcuts[0].icon == "/mine/icon.png"
    assert win.current_shortcut().name == "Pokémon émulé"


def test_restore_with_steam_open_starts_it_again(qapp, window, steam_state, monkeypatch):
    from steam_artwork_finder.gui import dialogs

    win = window
    first = win.sfile.backup()
    steam_state["running"] = True
    monkeypatch.setattr(dialogs.RestoreDialog, "exec", lambda dlg: 1)
    monkeypatch.setattr(dialogs.RestoreDialog, "selected", lambda dlg: first)
    win.restore_backup()
    _pump(qapp, 3000, until=lambda: not win.busy)
    assert steam_state["calls"] == ["shutdown", "start"]


def test_rate_limit_message_reaches_the_gui_thread(qapp, window):
    import threading

    win = window
    win._set_busy("fetch", "Getting artwork…")
    t = threading.Thread(target=lambda: win.sgdb.on_wait(12.0))
    t.start()
    t.join()
    _pump(qapp, 500, until=lambda: "12 s" in win.progress_text)
    assert win.progress_text == "SteamGridDB asked us to wait 12 s…"
    win._set_busy("")


def test_background_matching_stops_on_a_bad_key(qapp, window):
    from steam_artwork_finder.sgdb import AuthError

    win = window
    win.config.data["api_key"] = "x"
    calls = []

    def match(name):
        calls.append(name)
        raise AuthError("SteamGridDB rejected the API key")

    win.sgdb.match = match
    win.set_connection("connected")
    _pump(qapp, 3000, until=lambda: calls and not win._matching)
    assert len(calls) == 1 and win.conn == "disconnected"


def test_thumbnail_cache_replaces_a_cut_off_file(qapp, tmp_path, monkeypatch):
    import hashlib

    from steam_artwork_finder.gui import workers

    from PySide6.QtCore import QBuffer
    from PySide6.QtGui import QImage

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = QBuffer()
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img = QImage(4, 4, QImage.Format.Format_RGB32)
    img.fill(0)
    img.save(buf, "PNG")
    PNG = bytes(buf.data())  # noqa: N806 - a decodable one, unlike the module's
    url = "https://x/t.png"
    fetched = []
    assert workers.cached_download(url, lambda u: fetched.append(u) or PNG) == PNG
    p = workers.cache_dir() / "thumbs" / hashlib.sha1(url.encode()).hexdigest()
    assert p.read_bytes() == PNG and not list(p.parent.glob("*.part"))
    assert workers.cached_download(url, lambda u: fetched.append(u) or PNG) == PNG
    assert len(fetched) == 1
    p.write_bytes(PNG[:20])  # the app was killed mid-download
    assert workers.cached_download(url, lambda u: fetched.append(u) or PNG) == PNG
    assert len(fetched) == 2 and p.read_bytes() == PNG


def test_banner_header_fits_the_page(qapp, window):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QLabel

    win = window
    win.config.data["api_key"] = "x"
    win.sgdb.match = lambda name: (None, 0)
    win.sgdb.assets = lambda *a, **k: []
    win.open_game(0)
    _pump(qapp)
    page, card = win.page, win.page.card
    win.sfile.shortcuts[0].name = "A Very Long Game Name That Goes On And On, The Definitive Edition"
    card.set_game(win.sfile.shortcuts[0].name)
    card.set_state("check", "The Legend of Zelda: Tears of the Kingdom", "2023", 12)
    card.set_actions(True, 3, False)

    def fits():
        _pump(qapp, 150)
        right = card.apply_btn.mapTo(card, QPoint(card.apply_btn.width(), 0)).x()
        assert page.scroll.widget().width() <= page.scroll.viewport().width()
        assert right <= card.width() and card.title.width() <= card.width()

    fits()
    assert not card.compact and card.height() == 210  # buttons beside the name, as always
    shown = QLabel.text(card.title)
    assert card.title.text().startswith("A Very Long") and shown.endswith("…") and card.title.toolTip()
    win.split.setSizes([670, 610])  # a narrow page (the tray no longer narrows it: it lies over it)
    fits()
    assert card.compact  # the buttons moved to a row of their own
    win.split.setSizes([300, 980])
    _pump(qapp, 150)
    assert not card.compact


def test_artwork_files_are_looked_up_once_and_again_after_a_write(qapp, window):
    from steam_artwork_finder.artwork import SLOTS
    from steam_artwork_finder.pending import Pick

    win = window
    sc = win.sfile.shortcuts[0]
    assert win.coverage(sc) == [False] * 5
    win.user.grid_dir.mkdir(parents=True, exist_ok=True)
    (win.user.grid_dir / f"{sc.appid}_hero.png").write_bytes(PNG)
    assert win.coverage(sc)[2] is False  # remembered, not looked up on every paint
    assert win.slot_path(sc, SLOTS["hero"], fresh=True) is not None
    win._stage(sc, "grid", Pick(_asset(1), PNG))
    win.apply_games([sc.appid])
    _pump(qapp, 3000, until=lambda: not win.busy)
    assert win.coverage(sc)[:3] == [True, False, True]
    win.toast.undo_now()
    assert win.coverage(sc)[0] is False
    (win.user.grid_dir / f"{sc.appid}_logo.png").write_bytes(PNG)
    win.reload_shortcuts()
    assert win.coverage(win.sfile.shortcuts[0])[3] is True


def test_content_bar_buttons_keep_focus_through_a_refresh(qapp, window):
    win = window
    win.activateWindow()
    b = win.cbar.buttons[0]
    b.setFocus()
    _pump(qapp, 50)
    win.refresh_all()
    win.refresh_all()
    assert win.cbar.buttons[0] is b and b.hasFocus() and b.text() == "Get Artwork (3 Games)"


def test_popover_keeps_tab_and_esc_to_itself(qapp, window):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication, QLineEdit

    win = window
    win.activateWindow()
    _pump(qapp, 50)
    win.ask_api_key()
    _pump(qapp, 200)
    pop = win.keypop
    assert pop.is_open()
    for _ in range(8):  # Tab goes round the pop-over's own controls
        pop.focusNextPrevChild(True)
        assert pop.isAncestorOf(QApplication.focusWidget())
    other = QLineEdit()  # Esc in another window (a dialog) isn't the pop-over's
    QApplication.sendEvent(other, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert pop.is_open()
    QApplication.sendEvent(pop.edit, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert not pop.is_open()
    assert QApplication.focusWidget() is win.statusbar.connection  # back where it was opened from


def test_dialogs_are_deleted_when_closed(qapp, window, monkeypatch):
    from PySide6.QtCore import QEvent

    from steam_artwork_finder.gui import dialogs

    win = window
    win.config.data["api_key"] = "x"
    win.sgdb.search = lambda term: []
    monkeypatch.setattr(dialogs.MatchDialog, "exec", lambda dlg: 0)
    win.open_game(0)
    for _ in range(2):
        win.change_match()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)  # what the event loop does when it gets back
    assert not win.findChildren(dialogs.MatchDialog)


def test_details_columns_never_overlap_the_names(qapp):
    from steam_artwork_finder.gui.library import NAME_X, STATUS_W, detail_columns

    end, centres, status_x, col = detail_columns(900)
    assert status_x is not None and centres[-1] + col / 2 <= status_x and col == 64
    for width in (380, 500, 639):
        end, centres, status_x, col = detail_columns(width)
        assert status_x is None and end >= NAME_X + 40  # the status column moves into the tooltip
        assert centres[0] - col / 2 >= end
    assert detail_columns(640 + STATUS_W)[2] is not None


def test_sidebar_search_and_filter_reach_the_wall(qapp, window):
    win, start = window, window.start

    def on_wall():
        return [start.wall.item(r).text() for r in range(start.wall.count()) if not start.wall.item(r).isHidden()]

    assert len(on_wall()) == 3 and win.cbar.count.text() == "(3)"
    win.game_list.search.setText("celeste")
    assert on_wall() == ["Celeste"] and win.cbar.count.text() == "(1 of 3)"
    start.set_piece(0)  # the chips still narrow it further
    assert on_wall() == ["Celeste"] and start.note.isVisibleTo(start)
    win.game_list.search.setText("zzz")
    assert on_wall() == [] and start.empty.isVisibleTo(start) and win.cbar.count.text() == "(0 of 3)"
    start._show_everything()
    assert len(on_wall()) == 3 and not start.empty.isVisibleTo(start) and start.piece is None
    win.status[win.sfile.shortcuts[1].appid] = "check"
    win.game_list.set_filter("check")
    assert on_wall() == [win.sfile.shortcuts[1].name] and win.cbar.count.text() == "(1 of 3)"
    win.game_list.set_filter("all")
    assert len(on_wall()) == 3


def test_banner_header_has_one_filled_button(qapp, window):
    win = window
    win.config.data["api_key"] = "x"
    win.sgdb.match = lambda name: (None, 0)
    win.sgdb.assets = lambda *a, **k: []
    win.open_game(0)
    _pump(qapp)
    card = win.page.card

    def filled():
        return [b.text() for b in card.acts.findChildren(type(card.apply_btn)) if b.isVisibleTo(card) and b.isEnabled()
                and b.property("split") != "arrow"  # Get All Artwork's arrow is part of the same button
                and (b.objectName() == "bannerget" or b.property("kind") in ("go", "confirm"))]

    for state, changes, want in (("exact", 0, ["Get All Artwork"]), ("exact", 2, ["Apply (2 Changes)"]),
                                 ("check", 0, []), ("check", 1, ["Apply (1 Change)"])):
        card.set_state(state, "Celeste", "2018", 3)
        card.set_actions(True, changes, False)
        assert filled() == want, (state, changes)
    assert "href='confirm'" in card.meta.text()  # "Yes, That's It" is a quiet link beside "Change Game"
    confirmed = []
    card.confirm.connect(lambda: confirmed.append(True))
    card.meta.linkActivated.emit("confirm")
    assert confirmed


def test_get_all_arrow_chooses_still_or_animated(qapp, window):
    win = window
    win.config.data["api_key"] = "x"
    win.sgdb.match = lambda name: (None, 0)
    win.sgdb.assets = lambda *a, **k: []
    win.open_game(0)
    _pump(qapp)
    card = win.page.card
    assert not win.get_all_animated and not card.get_btn.animated
    still = card.get_btn.sizeHint().width()
    card.get_arrow.click()
    _pump(qapp)
    menu = win._get_menu
    assert [r.text() for r in menu.rows] == ["Still Artwork", "Animated Artwork"]
    assert menu.rows[0].item.checked and not menu.rows[1].item.checked
    assert card.get_arrow.property("open")  # drawn held down while its menu shows
    menu.rows[1].click()
    _pump(qapp)
    assert win.get_all_animated and win.config.data["get_all_animated"]
    assert card.get_btn.animated and card.get_btn.sizeHint().width() > still  # room for the ANIMATED tag
    assert card.get_arrow.objectName() == card.get_btn.objectName()  # both halves styled alike
    win._set_get_all_animated(False)
    assert not card.get_btn.animated


def test_option_strips_line_up_and_banners_keep_their_top(qapp, window):
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QColor, QPainter, QPixmap

    from steam_artwork_finder.gui.widgets import fill_rect

    win = window
    win.open_game(0)
    _pump(qapp)
    rows = win.page.rows.values()
    assert len({r.mid_col.geometry().right() for r in rows}) == 1  # every strip starts at the same x

    pm = QPixmap(100, 200)
    pm.fill(QColor("red"))
    p = QPainter(pm)
    p.fillRect(0, 100, 100, 100, QColor("blue"))  # red top half, blue bottom half
    p.end()
    centred = fill_rect(pm, QSize(100, 50)).toImage()
    assert QColor(centred.pixel(50, 0)) == QColor("red") and QColor(centred.pixel(50, 49)) == QColor("blue")
    top = fill_rect(pm, QSize(100, 50), top=True).toImage()
    assert QColor(top.pixel(50, 0)) == QColor("red") and QColor(top.pixel(50, 49)) == QColor("red")


def test_sort_menu_orders_the_list_and_wall(qapp, window):
    win = window
    bar = win.game_list.homebar
    assert win.game_list.order() == win.start.order() == [0, 1, 2]  # A to Z by default
    bar.sort_menu.chosen.emit("za")
    _pump(qapp, 100)
    assert win.game_list.order() == win.start.order() == [2, 1, 0]
    assert bar.sort.view == "za" and win.config.data["library_sort"] == "za"
    assert win.game_list.list.item(0).text() == "Pokémon émulé"

    bar.sort_menu.chosen.emit("missing")
    win.user.grid_dir.mkdir(parents=True, exist_ok=True)
    sc = win.sfile.shortcuts[0]  # Celeste gets a cover: it has the least missing now
    (win.user.grid_dir / f"{sc.appid}p.png").write_bytes(PNG)
    win.forget_files()
    win.refresh_all()
    assert win.game_list.order() == win.start.order() == [1, 2, 0]

    win.game_list.set_filter("missing")  # sorting works on what the filter leaves
    shown = [i for row, i in enumerate(win.game_list.order()) if not win.game_list.list.item(row).isHidden()]
    assert shown == [1, 2, 0]
    bar.sort_menu.chosen.emit("recent")
    assert win.game_list.order() == [2, 1, 0]


def test_generic_title_is_renamed_on_apply(qapp, window, steam_root, steam_state):
    from conftest import SAMPLE_ENTRIES, make_entry, make_shortcuts

    from steam_artwork_finder.shortcuts import ShortcutsFile

    win = window
    entries = [make_entry(*e) for e in SAMPLE_ENTRIES] + [
        make_entry(-123456, "game.exe", '"/home/deck/Games/Hollow Knight/game.exe"')]
    win.user.shortcuts_path.write_bytes(make_shortcuts(entries))
    win.reload_shortcuts()
    _pump(qapp, 100)
    idx = next(i for i, s in enumerate(win.sfile.shortcuts) if s.name == "game.exe")
    sc = win.sfile.shortcuts[idx]
    win.config.set_match(sc.appid, 42, "Hollow Knight", "2017")
    win.open_game(idx)
    _pump(qapp, 100)
    card = win.page.card
    assert win.rename_suggestion(sc) == "Hollow Knight"
    assert card.rename_btn.isVisible() and card.rename_btn.text() == "Rename to Hollow Knight"

    card.rename_btn.click()
    assert win.pending.name(sc.appid) == "Hollow Knight" and win.pending.count(sc.appid) == 1
    assert card.title.text() == "Hollow Knight" and card.unrename_btn.isVisible() and not card.rename_btn.isVisible()
    assert ShortcutsFile(win.user.shortcuts_path).find(sc.appid).name == "game.exe"  # nothing written yet
    assert card.apply_btn.text() == "Apply (1 Change)"

    steam_state["running"] = True  # Apply closes Steam, renames, then starts it again
    win.apply_games([sc.appid])
    _pump(qapp, 3000, until=lambda: not win.busy)
    assert steam_state["calls"] == ["shutdown", "start"]
    on_disk = ShortcutsFile(win.user.shortcuts_path).find(sc.appid)
    assert on_disk is not None and on_disk.name == "Hollow Knight"  # same appid: artwork stays linked
    assert not win.pending and win.current_shortcut().name == "Hollow Knight"
    assert not card.rename_btn.isVisible()
    assert any(win.game_list.list.item(r).text() == "Hollow Knight" for r in range(win.game_list.list.count()))

    win.toast.undo_now()
    assert ShortcutsFile(win.user.shortcuts_path).find(sc.appid).name == "game.exe"


def test_rename_waits_for_steam_to_close(qapp, window, steam_state):
    from steam_artwork_finder.shortcuts import ShortcutsFile

    win = window
    win.config.data["auto_restart_steam"] = False
    sc = win.sfile.shortcuts[1]
    win.pending.set_name(sc.appid, "Hollow Knight")
    steam_state["running"] = True
    win.apply_games([sc.appid])
    _pump(qapp, 3000, until=lambda: not win.busy)
    assert ShortcutsFile(win.user.shortcuts_path).find(sc.appid).name == "Hollow Knight™"  # Steam would undo it
    assert win.pending_names() == {str(sc.appid): "Hollow Knight"} and win.steam_state() == "restart"
    steam_state["running"] = False
    win.update_steam_state()
    assert ShortcutsFile(win.user.shortcuts_path).find(sc.appid).name == "Hollow Knight"
    assert not win.pending_names()


def test_get_artwork_and_apply_rename_by_default(qapp, window, steam_state):
    from conftest import SAMPLE_ENTRIES, make_entry, make_shortcuts

    from steam_artwork_finder.sgdb import Game
    from steam_artwork_finder.shortcuts import ShortcutsFile

    win = window
    entries = [make_entry(*e) for e in SAMPLE_ENTRIES] + [
        make_entry(-123456, "game.exe", '"/home/deck/Games/Hollow Knight/game.exe"'),
        make_entry(-654321, "Launcher", '"/home/deck/Games/Hades/Launcher.exe"')]
    win.user.shortcuts_path.write_bytes(make_shortcuts(entries))
    win.reload_shortcuts()
    win.config.data["api_key"] = "x"
    searched = []
    win.sgdb.match = lambda name: searched.append(name) or (Game(len(searched), name, "2020"), 1)
    win.sgdb.assets = lambda kind, gid, page=0: [_asset(gid * 10 + len(kind))]
    win.sgdb.download = lambda url: PNG
    hk, hades = (next(s for s in win.sfile.shortcuts if s.name == n) for n in ("game.exe", "Launcher"))
    win.config.set_match(hades.appid, 77, "Hades", "2020")
    win._set_keeps_name(hades.appid, True)  # the user said Keep Old Name for this one
    _pump(qapp, 100)

    win.fill_everything()
    _pump(qapp, 5000, until=lambda: not win.busy)
    assert "Hollow Knight" in searched  # looked up by its folder
    assert win.pending.name(hk.appid) == "Hollow Knight"
    assert win.pending.name(hades.appid) is None
    win.apply_all()
    _pump(qapp, 5000, until=lambda: not win.busy)
    on_disk = ShortcutsFile(win.user.shortcuts_path)
    assert on_disk.find(hk.appid).name == "Hollow Knight"
    assert on_disk.find(hades.appid).name == "Launcher"  # kept, as asked
    assert {s.name for s in on_disk.shortcuts} >= {"Celeste", "Hollow Knight™", "Pokémon émulé"}  # real titles stay


def test_wall_gaps_are_not_clickable(qapp, window):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    wall = window.start.wall
    picked = []
    window.start.picked.connect(picked.append)
    _pump(qapp, 100)
    first, second = wall.visualRect(wall.model().index(0, 0)), wall.visualRect(wall.model().index(1, 0))
    tile = wall.delegate.hit_rect(first)
    assert tile.width() < first.width()  # the cell is wider than its cover
    gap_x = (tile.right() + wall.delegate.hit_rect(second).left()) // 2
    below = QPoint(tile.center().x(), first.bottom() - 4)  # under the artwork line, above the next row
    for pt in (QPoint(gap_x, tile.center().y()), below):
        assert not wall.indexAt(pt).isValid() and wall.itemAt(pt) is None
        QTest.mouseClick(wall.viewport(), Qt.MouseButton.LeftButton, pos=pt)
    assert picked == []
    QTest.mouseClick(wall.viewport(), Qt.MouseButton.LeftButton, pos=tile.center())  # the cover
    QTest.mouseClick(wall.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(tile.x() + 5, tile.bottom() - 5))
    assert picked == [0, 0]
    window.start.set_view("list")
    _pump(qapp, 50)
    row = wall.visualRect(wall.model().index(0, 0))
    assert wall.indexAt(row.center()).row() == 0
    assert not wall.indexAt(QPoint(row.center().x(), row.bottom())).isValid()  # the gap between rows
    window.start.set_view("grid")



def _image_bytes(w, h, fmt="PNG", alpha=True):
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QColor, QImage

    img = QImage(w, h, QImage.Format.Format_ARGB32 if alpha else QImage.Format.Format_RGB32)
    img.fill(QColor(30, 120, 200, 128 if alpha else 255))
    arr = QByteArray()
    buf = QBuffer(arr)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, fmt)
    buf.close()
    return bytes(arr.data())


def test_own_file_waits_until_apply_and_undoes(qapp, window, tmp_path):
    from steam_artwork_finder.artwork import SLOTS, existing
    from steam_artwork_finder.pending import LocalFile

    win = window
    src = tmp_path / "pics" / "my cover.jpg"
    src.parent.mkdir()
    data = _image_bytes(600, 900, "JPG", alpha=False)
    src.write_bytes(data)
    win.open_game(0)
    _pump(qapp)
    sc = win.current_shortcut()
    row = win.page.rows["grid"]
    assert row.file_btn.isVisible() and row.file_btn.text() == "Custom Image"  # there even without an API key or options
    win.stage_file(SLOTS["grid"], path=str(src))
    _pump(qapp, 2000, until=lambda: win.pending.count(sc.appid))
    pick = win.pending.get(sc.appid, "grid")
    assert isinstance(pick.asset, LocalFile) and pick.data == data  # a JPEG is kept as it is
    assert row.caption.text() == "Not applied yet" and not row.art.pixmap.isNull()
    assert win.config.data["last_image_folder"] == str(src.parent)
    assert existing(win.user.grid_dir, sc.appid, SLOTS["grid"]) is None

    win.apply_games([sc.appid])
    _pump(qapp, 3000, until=lambda: not win.busy)
    path = existing(win.user.grid_dir, sc.appid, SLOTS["grid"])
    assert path is not None and path.suffix == ".jpg" and path.read_bytes() == data
    assert win.config.get_applied(sc.appid, "grid")["file"] == "my cover.jpg"
    assert path.parent == win.user.grid_dir and path.name == f"{sc.appid}p.jpg"  # a copy, named Steam's way
    assert src.read_bytes() == data  # the user's own file is left alone
    win.page.show_game()
    assert row.caption.text() == "In use · custom image"
    win.toast.undo_now()
    assert existing(win.user.grid_dir, sc.appid, SLOTS["grid"]) is None


def test_own_file_is_converted_and_warned_about(qapp):
    from PySide6.QtGui import QImage

    from steam_artwork_finder.artwork import SLOTS
    from steam_artwork_finder.gui import localfile

    bmp = localfile.load(SLOTS["hero"], _image_bytes(1920, 620, "BMP", alpha=False), "a.bmp")
    assert bmp.pick.asset.mime == "image/png" and bmp.pick.data.startswith(b"\x89PNG") and not bmp.note

    wide = localfile.load(SLOTS["grid"], _image_bytes(920, 430), "w.png")
    assert "wider than a cover" in wide.note
    small = localfile.load(SLOTS["hero"], _image_bytes(400, 130), "s.png")
    assert "small" in small.note
    logo = localfile.load(SLOTS["logo"], _image_bytes(400, 200, "JPG", alpha=False), "l.jpg")
    assert "see-through" in logo.note

    big = localfile.load(SLOTS["grid"], _image_bytes(4000, 6000), "big.png")
    assert big.pick.asset.height == 3840 and max(big.preview.width(), big.preview.height()) <= 1920

    pasted = localfile.load_image(SLOTS["icon"], QImage.fromData(_image_bytes(256, 256), "PNG"))
    assert pasted.pick.data.startswith(b"\x89PNG") and pasted.pick.asset.path == ""
    with pytest.raises(localfile.FileProblem):
        localfile.load(SLOTS["grid"], b"not a picture", "x.png")


def test_own_icon_is_copied_and_linked_from_steams_folder(qapp, window, tmp_path):
    from steam_artwork_finder.artwork import SLOTS, existing

    win = window
    src = tmp_path / "icon.bmp"
    src.write_bytes(_image_bytes(256, 256, "BMP", alpha=False))
    win.open_game(0)
    _pump(qapp)
    sc = win.current_shortcut()
    win.stage_file(SLOTS["icon"], path=str(src))
    _pump(qapp, 2000, until=lambda: win.pending.count(sc.appid))
    win.apply_games([sc.appid])
    _pump(qapp, 3000, until=lambda: not win.busy)
    path = existing(win.user.grid_dir, sc.appid, SLOTS["icon"])
    assert path is not None and path.name == f"{sc.appid}_icon.png"  # converted for Steam
    assert win.sfile.find(sc.appid).icon == str(path)  # points at the copy, never the original
    src.unlink()
    assert path.exists()


def test_artwork_steamgriddb_lacks_is_remembered(qapp, window, tmp_path):
    from PySide6.QtGui import QImage

    win = window
    win.config.data["api_key"] = "x"
    for sc in win.sfile.shortcuts:
        win.config.set_match(sc.appid, 9, sc.name)
    cover = QImage(60, 90, QImage.Format.Format_RGB32)
    cover.fill(0x3366CC)
    cover.save(str(tmp_path / "cover.png"))
    png = (tmp_path / "cover.png").read_bytes()
    win.sgdb.assets = lambda kind, game_id, page=0, animated=False: [_asset(1)] if kind == "grid" else []
    win.sgdb.download = lambda url: png
    win.refresh_all()
    tip = win.cbar.buttons[0].toolTip()
    assert all(sc.name in tip for sc in win.sfile.shortcuts)  # the button says which games

    win.fill_everything()
    _pump(qapp, 3000, until=lambda: not win.busy)
    sc = win.sfile.shortcuts[0]
    assert win.pending.get(sc.appid, "icon") is None  # Steam's own icon is never made up
    assert win.config.no_art(sc.appid) == {"wide", "hero", "logo", "icon"}
    assert "12 pieces had no options yet" in win.statusbar.message.accessibleName()

    win.pending.clear()
    win.refresh_all()
    # SteamGridDB only has covers, so only the cover is still to get; a recheck asks for all again.
    assert [s.key for s in win._unstaged_missing(sc)] == ["grid"]
    assert len(win._unstaged_missing(sc, recheck=True)) == 5
    win.config.set_no_art(sc.appid, "hero", False)  # e.g. the game page found banners after all
    assert "hero" not in win.config.no_art(sc.appid)


def test_startup_learns_what_steamgriddb_lacks(qapp, window):
    from steam_artwork_finder.gui.library import status_text

    win = window
    win.config.data["api_key"] = "x"
    for sc in win.sfile.shortcuts:
        win.config.set_match(sc.appid, 9, sc.name)
    asked = []
    win.sgdb.assets = lambda kind, game_id, page=0: asked.append(kind) or []
    win.set_connection("connected")
    _pump(qapp, 3000, until=lambda: len(asked) == 15)
    _pump(qapp, 300)
    sc = win.sfile.shortcuts[0]
    assert win.complete(sc) and win.art_states(sc) == ["none"] * 5
    assert status_text(win, 0)[0] == "All available"
    assert win.cbar.buttons[0].text() == "Get Artwork" and not win.cbar.buttons[0].isEnabled()
    win.check_gaps()  # once a session is enough
    _pump(qapp, 300)
    assert len(asked) == 15


def test_a_game_with_all_there_is_goes_quiet(qapp, window, tmp_path):
    from PySide6.QtGui import QImage

    from steam_artwork_finder.artwork import SLOTS, save_asset
    from steam_artwork_finder.gui.library import status_text

    win = window
    win.config.data["api_key"] = "x"
    sc = win.sfile.shortcuts[0]
    win.config.set_match(sc.appid, 9, sc.name)
    img = QImage(60, 90, QImage.Format.Format_RGB32)
    img.fill(0x3366CC)
    img.save(str(tmp_path / "c.png"))
    save_asset(win.user.grid_dir, sc, SLOTS["grid"], (tmp_path / "c.png").read_bytes(), "image/png", None)
    win.forget_files()
    win.sgdb.assets = lambda kind, game_id, page=0, *a, **k: []
    win.sgdb.match = lambda name: (None, 0)
    win.set_connection("connected")
    _pump(qapp, 3000, until=lambda: len(win.config.no_art(sc.appid)) == 4)
    _pump(qapp, 300)
    assert win.complete(sc) and status_text(win, 0)[0] == "All available"
    assert not win.pending  # nothing staged for Steam
    assert win.list_icon(sc) == (win.slot_path(sc, SLOTS["grid"]), False)  # the app's list uses the cover


def test_game_list_icon_falls_back_to_other_artwork(qapp, window, tmp_path):
    from PySide6.QtGui import QImage

    from steam_artwork_finder.artwork import SLOTS, save_asset
    from steam_artwork_finder.gui.widgets import decode_scaled

    win = window
    sc = win.sfile.shortcuts[0]
    assert win.list_icon(sc) == (None, False)  # no artwork at all: the letter
    img = QImage(200, 50, QImage.Format.Format_ARGB32)
    img.fill(0xFF3366CC)
    img.save(str(tmp_path / "logo.png"))
    save_asset(win.user.grid_dir, sc, SLOTS["logo"], (tmp_path / "logo.png").read_bytes(), "image/png", None)
    win.forget_files()
    path, contain = win.list_icon(sc)
    assert path is not None and contain  # a logo is fitted whole
    out = decode_scaled(str(path), 32, 32, contain=True)
    assert out.size().width() == 32 and out.pixelColor(16, 0).alpha() == 0 and out.pixelColor(16, 16).alpha() == 255
    save_asset(win.user.grid_dir, sc, SLOTS["grid"], (tmp_path / "logo.png").read_bytes(), "image/png", None)
    win.forget_files()
    assert win.list_icon(sc)[1] is False  # the cover comes first, cropped to fill


def _tiny_exe(path, png: bytes):
    """A minimal Windows program whose only content is one icon (a PNG), like a game's .exe."""
    import struct

    def directory(entries):  # [(id, offset)]
        return struct.pack("<IIHHHH", 0, 0, 0, 0, 0, len(entries)) + b"".join(
            struct.pack("<II", i, o) for i, o in entries)

    # Layout inside .rsrc: root, icon dir, icon lang, group dir, group lang, 2 data entries, data.
    root, d_icon, d_icon_l, d_grp, d_grp_l = 0, 32, 56, 80, 104
    e_icon, e_grp = 128, 144
    group = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHIH", 64, 64, 0, 0, 1, 32, len(png), 1)
    icon_at, grp_at = 160, 160 + len(png)
    rsrc = b"".join([
        directory([(3, 0x80000000 | d_icon), (14, 0x80000000 | d_grp)]),
        directory([(1, 0x80000000 | d_icon_l)]), directory([(0x409, e_icon)]),
        directory([(1, 0x80000000 | d_grp_l)]), directory([(0x409, e_grp)]),
        struct.pack("<IIII", 0x1000 + icon_at, len(png), 0, 0),
        struct.pack("<IIII", 0x1000 + grp_at, len(group), 0, 0),
        png, group,
    ])
    opt = bytearray(224)
    struct.pack_into("<H", opt, 0, 0x10B)
    struct.pack_into("<I", opt, 92, 16)
    struct.pack_into("<II", opt, 96 + 2 * 8, 0x1000, len(rsrc))
    head = bytearray(b"MZ" + bytes(58) + struct.pack("<I", 0x40))
    head += b"PE\0\0" + struct.pack("<HHIIIHH", 0x14C, 1, 0, 0, 0, 224, 0x102) + opt
    head += struct.pack("<8sIIIIIIHHI", b".rsrc", len(rsrc), 0x1000, len(rsrc), 0x200, 0, 0, 0, 0, 0)
    path.write_bytes(bytes(head) + bytes(0x200 - len(head)) + rsrc)


def test_exe_icon_is_the_fallback_for_steam_and_the_list(qapp, window, tmp_path, monkeypatch):
    from PySide6.QtGui import QImage

    from steam_artwork_finder.exeicon import exe_icon, exe_path

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    img = QImage(64, 64, QImage.Format.Format_ARGB32)
    img.fill(0xFFCC3333)
    img.save(str(tmp_path / "i.png"))
    exe = tmp_path / "Railway Empire 2" / "RailwayEmpire2.exe"
    exe.parent.mkdir()
    _tiny_exe(exe, (tmp_path / "i.png").read_bytes())
    assert exe_icon(exe) == (tmp_path / "i.png").read_bytes()
    assert exe_path(f'"{exe}"') == exe and exe_path('"/usr/bin/heroic" --launch x') is None

    win = window
    sc = win.sfile.shortcuts[0]
    sc.exe = f'"{exe}"'
    win.list_icon(sc)  # looked up in a worker, so painting never waits on the disk
    _pump(qapp, 3000, until=lambda: win.list_icon(sc)[0] is not None)
    path, contain = win.list_icon(sc)  # the app's list shows the .exe's icon
    assert path is not None and QImage(str(path)).size().width() == 64 and not contain
    assert win.pending.get(sc.appid, "icon") is None  # Steam is only offered it once SteamGridDB has none

    win.config.data["api_key"] = "x"
    win.config.set_match(sc.appid, 9, sc.name)
    win.sgdb.assets = lambda kind, game_id, page=0, *a, **k: []
    win.sgdb.match = lambda name: (None, 0)
    win.set_connection("connected")
    _pump(qapp, 3000, until=lambda: win.pending.get(sc.appid, "icon") is not None)
    assert win.pending.get(sc.appid, "icon").asset.style == "exe"
    win.show_index(0)
    _pump(qapp, 200)
    assert win.page.rows["icon"].caption.text() == "From the game's .exe · not applied yet"


def test_steam_official_artwork_comes_first_and_fills_without_a_key(qapp, window):
    from steam_artwork_finder.artwork import SLOTS, existing
    from steam_artwork_finder.steamstore import SteamAsset, StoreGame

    win = window
    win.sgdb.download = lambda url: PNG
    official = {k: SteamAsset(-(504230 * 10 + i), f"https://cdn/{k}.jpg", f"https://cdn/{k}_s.jpg", 600, 900,
                              "image/jpeg", 0, "official", "Steam", appid=504230)
                for i, k in enumerate(("grid", "wide", "hero", "logo"), 1)}  # the store's icons are never offered
    win.steam.match = lambda name: StoreGame(504230, "Celeste") if name == "Celeste" else None
    win.steam.assets = lambda appid: dict(official)
    win.open_game(0)
    _pump(qapp, 1000, until=lambda: win.page.rows["grid"].steam_assets)
    row = win.page.rows["grid"]
    assert row.assets[0] is official["grid"] and row.thumbs[0].official
    assert "Official" in row.count.text()
    meta = win.page.card.meta.text()
    assert "Celeste" in meta and "Steam" in meta and meta.count("href") == 1  # one line, one Change Game
    assert win.steam_option("hero") is official["hero"]

    # No SteamGridDB key: Get All Artwork still fills from Steam.
    sc = win.current_shortcut()
    win.fill_current()
    _pump(qapp, 3000, until=lambda: not win.busy)
    picks = win.pending.for_game(sc.appid)
    assert sorted(picks) == ["grid", "hero", "logo", "wide"]
    assert all(isinstance(p.asset, SteamAsset) for p in picks.values())
    win.apply_games([sc.appid])
    _pump(qapp, 3000, until=lambda: not win.busy)
    assert existing(win.user.grid_dir, sc.appid, SLOTS["grid"]) is not None
    assert win.config.get_applied(sc.appid, "grid")["steam"] == 504230
    row.show_current()
    assert row.caption.text() == "In use · from Steam"

    # Turned off: Steam's options go, and nothing is looked up.
    win._set_steam_art(False)
    _pump(qapp)
    assert not row.steam_assets and win.steam_option("grid") is None
    win._set_steam_art(True)


def test_steam_game_is_changed_in_the_game_picker(qapp, window, monkeypatch):
    from steam_artwork_finder.gui import app as appmod
    from steam_artwork_finder.gui.dialogs import MatchDialog
    from steam_artwork_finder.sgdb import Game
    from steam_artwork_finder.steamstore import StoreGame

    win = window
    win.steam.match = lambda name: None
    looked = []
    win.steam.assets = lambda appid: looked.append(appid) or {}
    win.open_game(0)
    _pump(qapp, 1000, until=lambda: win.current_shortcut().appid in win.steam_found)
    sc = win.current_shortcut()

    # The picker: the Steam game follows the SteamGridDB pick by name until one is chosen.
    win.sgdb.search = lambda term: [Game(9, "Celeste", "2018")]
    win.sgdb.assets = lambda *a, **k: []
    win.steam.search = lambda term: [StoreGame(7, "Celeste Classic"), StoreGame(504230, "Celeste™")]
    dlg = MatchDialog(win, win.sgdb, sc.name, sc.exe, win.steam, win._steam_now(sc))
    _pump(qapp, 1000, until=lambda: dlg.steam_box.count() == 3)
    assert dlg.steam_box.currentData().appid == 504230
    assert dlg.steam_choice().appid == 504230
    dlg.steam_box.setCurrentIndex(dlg.steam_box.count() - 1)
    assert dlg.steam_choice() is None  # back where it started: no store game, nothing to change
    dlg.done(0)
    dlg.deleteLater()

    class Dlg:
        NOT_ON_STEAM = MatchDialog.NOT_ON_STEAM
        steam = StoreGame(42, "Celeste Classic")

        def __init__(self, *a):
            pass

        def exec(self):
            return 1

        def selected(self):
            return None

        def steam_choice(self):
            return Dlg.steam

        def deleteLater(self):
            pass

    monkeypatch.setattr(appmod, "MatchDialog", Dlg)
    win.change_match()  # no SteamGridDB key: the picker still opens, for the Steam game
    _pump(qapp, 1000, until=lambda: sc.appid in win.steam_found)
    assert win.config.get_steam_match(sc.appid) == {"id": 42, "name": "Celeste Classic"}
    assert looked == [42]
    assert "Celeste Classic" in win.page.card.meta.text() and "Change" in win.page.card.meta.text()
    Dlg.steam = Dlg.NOT_ON_STEAM
    win.change_match()
    _pump(qapp, 500)
    assert win.config.get_steam_match(sc.appid)["id"] == 0 and looked == [42]


def test_dialogs_dim_the_window_and_close_on_a_dim_click(qapp, window):
    from PySide6.QtCore import QEvent, QPoint, Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from steam_artwork_finder.gui.dialogs import ConfirmDialog
    from steam_artwork_finder.gui.scrim import run_dialog

    win = window
    seen = {}

    def click_dim():
        dlg = win.scrim.top()
        seen["dialog"] = dlg
        seen["dimmed"] = win.scrim.isVisible() and isinstance(dlg, ConfirmDialog) and dlg.isVisible()
        seen["modal"] = QApplication.activeModalWidget()
        QTest.mouseClick(win.scrim, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))

    QTimer.singleShot(200, click_dim)
    assert win._ask_discard() is False  # the dim click cancels it
    assert seen["dimmed"] and seen["modal"] is None  # dimmed, and the dim does the blocking
    assert not win.scrim.is_covering()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not win.findChildren(ConfirmDialog)  # not left behind

    # The keyboard still works: Enter takes the default button, Esc cancels.
    for key, want in ((Qt.Key.Key_Return, 1), (Qt.Key.Key_Escape, 0)):
        dlg = ConfirmDialog(win, "Heading", "Words", "Do it")
        QTimer.singleShot(150, lambda d=dlg, k=key: QTest.keyClick(d, k))
        assert run_dialog(dlg, delete=True) == want

    # Over a pop-over: a dim click closes the dialog only, and the pop-over comes back above the dim.
    win.ask_api_key()
    _pump(qapp, 200)
    QTimer.singleShot(200, click_dim)
    assert run_dialog(ConfirmDialog(win, "Heading", "Words", "Do it"), delete=True) == 0
    _pump(qapp, 50)
    assert win.keypop.is_open() and win.scrim.top() is win.keypop
    kids = win.children()
    assert kids.index(win.keypop) > kids.index(win.scrim)  # stacked above the dim again
    win.keypop.dismiss()


def test_window_shortcuts_wait_behind_the_dim(qapp, window):
    from PySide6.QtGui import QKeySequence
    from PySide6.QtWidgets import QApplication

    win = window
    find = next(a for a in win.actions() if a.shortcut().matches(QKeySequence(QKeySequence.StandardKey.Find)))
    win.activateWindow()
    win.ask_api_key()
    _pump(qapp, 200)
    assert win.scrim.is_covering()
    find.trigger()
    _pump(qapp, 50)
    assert QApplication.focusWidget() is not win.game_list.search
    win.keypop.dismiss()
    _pump(qapp, 300)
    find.trigger()
    _pump(qapp, 50)
    assert QApplication.focusWidget() is win.game_list.search


def test_key_check_after_closing_doesnt_save(qapp, window, monkeypatch):
    from steam_artwork_finder.gui import dialogs

    win = window
    calls = []
    monkeypatch.setattr(dialogs.workers, "run", lambda fn, on_done, on_error: calls.append(on_done))
    saved = []
    win.keypop.saved.connect(saved.append)
    win.ask_api_key()
    _pump(qapp, 200)
    pop = win.keypop
    bottom = pop.geometry().bottom()
    pop._say("SteamGridDB didn't accept that key. Check you copied all of it, then try again.", "red")
    _pump(qapp, 100)
    assert pop.geometry().bottom() == bottom  # grows upwards, still clear of the status bar
    assert pop.height() >= pop.sizeHint().height()
    pop.edit.setText("abc")
    pop._check()
    pop.dismiss()  # closed while it was being checked
    calls[-1](True)
    assert saved == [] and not pop.is_open()
    win.ask_api_key()
    _pump(qapp, 200)
    assert pop.save.isEnabled()  # not stuck on the check that was dropped
    pop._check()
    calls[-1](True)
    assert saved == ["abc"]


def test_minimising_keeps_a_pop_over_dimmed(qapp, window):
    win = window
    win.ask_api_key()
    _pump(qapp, 300)
    pop = win.keypop
    win.showMinimized()  # every child gets a spontaneous hide
    _pump(qapp, 300)
    win.showNormal()
    _pump(qapp, 300)
    assert pop.is_open()
    assert win.scrim.is_covering() and win.scrim.isVisible() and win.scrim.top() is pop
    pop.dismiss()
    _pump(qapp, 300)
    assert not win.scrim.is_covering()


def test_get_all_can_prefer_animated_artwork(qapp, window):
    from steam_artwork_finder.sgdb import Asset
    from steam_artwork_finder.steamstore import SteamAsset, StoreGame

    win = window
    win.config.data["api_key"] = "x"
    for g in win.sfile.shortcuts:
        win.config.set_match(g.appid, 9, g.name)
    sc = win.sfile.shortcuts[0]
    asked = []

    def assets(kind, game_id, page=0, animated=False):
        asked.append((kind, animated))
        still = Asset(10, f"https://x/{kind}.png", f"https://x/{kind}_t.png", 600, 900, "image/png", 5, "", "a")
        moving = Asset(20, f"https://x/{kind}.webp", f"https://x/{kind}.webm", 600, 900, "image/webp", 1, "", "b")
        if kind == "logo":
            return [still]  # no animated logo: the still one, without asking again
        return [still, moving] if animated else [still]

    win.sgdb.assets = assets
    win.sgdb.download = lambda url: PNG
    official = {"grid": SteamAsset(-1, "https://cdn/grid.jpg", "https://cdn/grid_s.jpg", 600, 900,
                                   "image/jpeg", 0, "official", "Steam", appid=504230)}
    win.steam.match = lambda name: StoreGame(504230, sc.name) if name == sc.name else None
    win.steam.assets = lambda appid: dict(official)

    assert not win.get_all_animated  # off unless asked for: nothing changes
    win.fill_everything()
    _pump(qapp, 3000, until=lambda: not win.busy)
    assert isinstance(win.pending.get(sc.appid, "grid").asset, SteamAsset)
    assert not any(a for _, a in asked)
    assert not any(p.asset.animated for p in win.pending.for_game(sc.appid).values())

    win.pending.clear()
    asked.clear()
    win._set_get_all_animated(True)
    assert win.config.data["get_all_animated"]
    win.fill_everything()
    _pump(qapp, 3000, until=lambda: not win.busy)
    picks = win.pending.for_game(sc.appid)
    for k in ("grid", "wide", "hero"):  # an animated piece beats Steam's still one
        assert picks[k].asset.id == 20 and picks[k].asset.animated
    assert picks["logo"].asset.id == 10
    assert picks["icon"].asset.id == 10 and ("icon", True) not in asked  # icons stay still
    assert asked.count(("logo", True)) == 3  # once for each game

    # A one-off choice (the Get All button's own menu) overrides the setting.
    win.pending.clear()
    win.fill_everything(animated=False)
    _pump(qapp, 3000, until=lambda: not win.busy)
    assert not win.pending.get(sc.appid, "hero").asset.animated
    win._set_get_all_animated(False)


def test_about_credits_acid_design_and_fits_its_text(qapp, monkeypatch):
    from PySide6.QtWidgets import QLabel

    from steam_artwork_finder.gui import dialogs

    monkeypatch.setattr(dialogs.MakerLockup, "LOGO_FILES", ())  # without the logo, the words alone
    d = dialogs.AboutDialog(None, "Steam Artwork for Non-Steam Games", "1.2.3")
    text = " ".join(lab.text() for lab in d.findChildren(QLabel))
    assert "Version 1.2.3" in text and "MIT licence" in text
    assert "https://aciddesign.uk" in text and "Claude Code" in text and "SteamGridDB" in text
    lock = d.maker_mark
    assert lock.logo is None and lock.accessibleName() == "Acid Design"
    assert lock.word_font.family() == "Space Grotesk" and lock.cap_font.family().startswith("IBM Plex Mono")
    lock._tick(0.1)
    assert lock.phase == 0.0  # held before the sweep
    lock._tick(0.35)
    assert 0.0 < lock.phase < 1.0
    lock._tick(0.9)
    assert lock.phase == 1.0
    assert d.height() >= d.layout().totalHeightForWidth(d.width())  # no wrapped line cut off
    d.deleteLater()


def test_a_strip_pick_flies_into_the_piece(qapp, window):
    from PySide6.QtGui import QColor, QPixmap

    win = window
    win.open_game(0)
    _pump(qapp)
    row = win.page.rows["grid"]
    row.set_assets([_asset(n) for n in range(1, 4)], False)
    for v in row.thumbs:
        pm = QPixmap(60, 90)
        pm.fill(QColor("#3498db"))
        v.set_pixmap(pm)
    row.thumbs[1].clicked.emit()
    flight = row._flight
    assert flight is not None and flight.isVisible()
    assert row.pick().asset.id == 2  # staged at once; only the picture waits
    row.thumbs[2].clicked.emit()  # a second pick lands the first at once
    assert flight._landed and row._flight is not None
    _pump(qapp, 600, until=lambda: row._flight._landed)
    assert row._flight._landed and row.pick().asset.id == 3
    row.show_current()  # a change from elsewhere (See all, Get All, undo) doesn't fly
    assert row._flight is None
    row.thumbs[0].hide()  # a tile not on show just picks, as before
    row._option_clicked(row.assets[0])
    assert row._flight is None and row.pick().asset.id == 1
