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
    assert win.cbar.buttons[1].text() == "Apply" and not win.cbar.buttons[1].isEnabled()
    for b in [win.page.rows["grid"].see_all] + win.cbar.buttons:
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
    assert win.cbar.buttons[1].text() == "Apply (1 Change)"

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
