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
    win.tray.show()
    win.split.setSizes([300, 610, 370])
    fits()
    assert card.compact  # the buttons moved to a row of their own
    win.tray.hide()
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
