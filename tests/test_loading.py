"""Pictures that are slow, retried or can't be had: retries, the loading shimmer and "couldn't load"."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from test_gui import _pump, qapp, window  # noqa: E402,F401 - shared fixtures


def _png() -> bytes:
    from PySide6.QtCore import QBuffer
    from PySide6.QtGui import QImage

    buf = QBuffer()
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img = QImage(6, 9, QImage.Format.Format_RGB32)
    img.fill(0x336699)
    img.save(buf, "PNG")
    return bytes(buf.data())


def test_fetch_whole_retries_then_gives_up(qapp):
    from steam_artwork_finder.gui import workers
    from steam_artwork_finder.sgdb import GoneError, SGDBError

    png = _png()
    told, calls = [], []

    def flaky(url):  # dropped twice, then cut off once, then whole
        calls.append(url)
        if len(calls) <= 2:
            raise SGDBError("connection reset")
        return png[:30] if len(calls) == 3 else png

    assert workers.fetch_whole("u", flaky, told.append) == png
    assert len(calls) == 4 and told == [1, 2, 3]

    told.clear()
    with pytest.raises(SGDBError):
        workers.fetch_whole("u", lambda u: (_ for _ in ()).throw(SGDBError("down")), told.append)
    assert told == [1, 2, 3]  # the first try and 3 retries

    calls.clear()

    def gone(url):
        calls.append(url)
        raise GoneError("HTTP 404")

    with pytest.raises(GoneError):
        workers.fetch_whole("u", gone)
    assert len(calls) == 1  # not there: not asked again


def test_strip_option_shows_loading_then_failed_then_retries(qapp, window):
    from steam_artwork_finder.gui.widgets import ArtView
    from steam_artwork_finder.sgdb import Asset

    win = window
    png = _png()
    ok = {"now": False}

    def download(url):
        if not ok["now"]:
            raise OSError("no network")
        return png

    win.sgdb.download = download
    v = ArtView(win.size().__class__(80, 120))
    v.show()
    a = Asset(11, "https://x/c.png", "https://x/thumb-retry.png", 600, 900, "image/png", 0, "", "someone")
    win.load_thumb(a, v)
    assert v.load_state == ("loading", 0)
    v.repaint()  # paints the shimmer
    _pump(qapp, 2000, until=lambda: v.load_state and v.load_state[0] == "failed")
    assert v.load_state[0] == "failed" and v.pixmap.isNull()
    v.repaint()
    picked = []
    v.clicked.connect(lambda: picked.append(1))
    ok["now"] = True
    v._activate()  # a click on "couldn't load" tries again, and doesn't pick it
    assert not picked
    _pump(qapp, 2000, until=lambda: not v.pixmap.isNull())
    assert not v.pixmap.isNull() and v.load_state is None
    v.repaint()  # fades in over the shimmer
    v._activate()
    assert picked == [1]


def test_see_all_option_counts_retries_and_retries_on_click(qapp, window):
    from PySide6.QtWidgets import QListWidgetItem
    from steam_artwork_finder.gui.tray import ROLE_LOAD, ROLE_PIX
    from steam_artwork_finder.sgdb import Asset

    win = window
    states = []
    it = QListWidgetItem("x")
    orig = it.setData

    def spy(role, value):
        if role == ROLE_LOAD and value is not None:
            states.append(value[0] if value[0] == "failed" else ("loading", value[1]))
        orig(role, value)

    it.setData = spy
    win.sgdb.download = lambda url: (_ for _ in ()).throw(OSError("no network"))
    a = Asset(12, "https://x/c.png", "https://x/thumb-tray.png", 600, 900, "image/png", 0, "", "someone")
    win.load_thumb(a, it)
    _pump(qapp, 2000, until=lambda: states and states[-1] == "failed")
    assert states == [("loading", 0), ("loading", 1), ("loading", 2), ("loading", 3), "failed"]
    png = _png()
    win.sgdb.download = lambda url: png
    it.data(ROLE_LOAD)[1]()  # what a click on it does
    _pump(qapp, 2000, until=lambda: it.data(ROLE_PIX) is not None)
    assert it.data(ROLE_PIX) is not None and it.data(ROLE_LOAD) is None
