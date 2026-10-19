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

    app.setStyleSheet(theme.stylesheet())
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
    _pump(qapp, 100)
    assert pop.is_open() and pop.pages.currentIndex() == 0
    h0 = pop.height()
    _pump(qapp, 2000, until=lambda: not pop.searching)
    assert not pop.searching
    assert [f.place.short for f in pop.found] == ["Flatpak"]  # the test's Steam lives outside the usual places
    assert pop.dots.states[1] == "hit" and pop.dots.states[2] == "miss"
    assert pop.title.text() == "Using your chosen folder" and pop.found[0].games == 3
    assert pop.account.name.text() == "Deck User" and pop.account.switch.isEnabled()

    pop.show_tab(1)
    _pump(qapp, 50)
    assert pop.height() == h0  # the tabs are the same size
    pop._chosen(tmp_path / "nowhere")
    assert "userdata" in pop.hint.text()
    pop._chosen(flatpak)
    _pump(qapp)
    assert win.steam_root == flatpak.resolve()
    assert win.config.data["steam_folder"] == str(flatpak.resolve())
    assert pop.title.text() == "Using Steam (Flatpak)" and pop.pages.currentIndex() == 0
    assert pop.height() == h0

    win.statusbar.steam.click()  # a second click closes it
    _pump(qapp, 400)
    assert not pop.isVisible()
    assert steamfolder.tilde(tmp_path / "x") == "~/x"


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

        def wheel(dy, pixels=0):
            ev = QWheelEvent(QPointF(50, 50), QPointF(area.viewport().mapToGlobal(QPoint(50, 50))), QPoint(0, pixels),
                             QPoint(0, dy), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                             Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(area.viewport(), ev)

        wheel(-120)
        _pump(qapp, 40)
        mid = bar.value()
        _pump(qapp, 400)
        step = QApplication.wheelScrollLines() * bar.singleStep()
        assert 0 < mid < step and bar.value() == step  # it glides there, it doesn't jump
        wheel(-120)
        wheel(-120)
        _pump(qapp, 400)
        assert bar.value() == 3 * step  # quick notches add up
        wheel(-120, pixels=-40)  # a mouse wheel that also reports pixels (KDE on Wayland)
        _pump(qapp, 40)
        assert 3 * step < bar.value() < 4 * step
        _pump(qapp, 400)
        assert bar.value() == 4 * step
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
