"""Offscreen smoke test: the window builds against a synthetic Steam install."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from steam_artwork_finder.gui import theme

    app.setStyleSheet(theme.stylesheet())
    return app


def _pump(app, ms=300):
    import time

    t = time.time()
    while time.time() - t < ms / 1000:
        app.processEvents()


def test_window_shows_games_and_pages(qapp, steam_root, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    from steam_artwork_finder.gui.app import MainWindow
    from steam_artwork_finder.gui.library import ROLE_INDEX
    from PySide6.QtCore import Qt

    win = MainWindow()
    win.resize(1280, 800)
    win.show()
    win.detect_steam([steam_root])
    _pump(qapp)
    assert win.pages.currentIndex() == 2
    assert win.game_list.list.count() == 4  # "All games" + 3
    assert win.game_list.list.item(1).text() == "Celeste"
    assert "Celeste" in win.game_list.list.item(1).data(Qt.ItemDataRole.AccessibleTextRole)
    assert win.fill_btn.text() == "Get artwork for 3 games"
    assert win.minimumSizeHint().width() <= 1280

    win.game_list.select(1)
    _pump(qapp)
    assert win.stage.currentWidget() is win.page
    assert win.page.title.text() == "Hollow Knight™"
    assert win.page.keybar.isVisible()  # no API key yet: one prompt, not five messages
    # Every control the design calls a touch target is at least 40 px tall.
    for b in list(win.game_list.filter_buttons.values()) + [win.page.match_btn, win.page.rows["grid"].see_all]:
        assert b.height() >= 40, b.text()

    win.open_tray(win.page.rows["grid"].slot) if win.config.api_key else None
    win.detect_steam([])
    _pump(qapp)
    assert win.pages.currentIndex() == 0 and win.sfile is None
    win.close()
