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


def test_downloads_retry_then_give_up(qapp):
    from steam_artwork_finder.gui import workers
    from steam_artwork_finder.sgdb import GoneError, SGDBError

    png = _png()
    calls, told, got, errors = [], [], [], []

    def flaky(url):  # dropped twice, then cut off once, then whole
        calls.append(url)
        if len(calls) <= 2:
            raise SGDBError("connection reset")
        return workers._fetch(url, lambda u: png[:30] if len(calls) == 3 else png)

    workers.run_retrying(flaky, "u", on_done=got.append, on_error=errors.append, on_retry=told.append)
    _pump(qapp, 2000, until=lambda: got or errors)
    assert got == [png] and not errors and len(calls) == 4 and told == [1, 2, 3]

    told.clear()
    workers.run_retrying(lambda u: (_ for _ in ()).throw(SGDBError("down")), "u",
                         on_done=got.append, on_error=errors.append, on_retry=told.append)
    _pump(qapp, 2000, until=lambda: errors)
    assert errors and told == [1, 2, 3]  # the first try and 3 retries

    calls.clear()
    errors.clear()

    def gone(url):
        calls.append(url)
        raise GoneError("HTTP 404")

    workers.run_retrying(gone, "u", on_error=errors.append)
    _pump(qapp, 2000, until=lambda: errors)
    _pump(qapp, 100)
    assert errors and len(calls) == 1  # not there: not asked again


def test_one_download_for_everyone_waiting(qapp):
    import threading

    from steam_artwork_finder.gui import workers

    gate, calls, got = threading.Event(), [], []

    def slow(url):
        calls.append(url)
        gate.wait(2)
        return url.upper()

    shared = workers.Shared()
    shared.get("k", slow, "a", on_done=got.append)
    shared.get("k", slow, "a", on_done=got.append)
    gate.set()
    _pump(qapp, 2000, until=lambda: len(got) == 2)
    assert got == ["A", "A"] and calls == ["a"]


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


def test_strip_keeps_options_when_more_arrive(qapp, window):
    """Steam's piece and SteamGridDB's arrive separately: options already showing aren't made (and loaded) again."""
    from steam_artwork_finder.sgdb import Asset

    win = window
    png = _png()
    asked = []
    win.sgdb.download = lambda url: asked.append(url) or png
    win.open_game(0)
    _pump(qapp)
    row = win.page.rows["grid"]
    a = [Asset(20 + i, f"https://x/c{i}.png", f"https://x/keep{i}.png", 600, 900, "image/png", 0, "", "s") for i in range(3)]
    row.set_assets(a[:2], False)
    _pump(qapp, 1500, until=lambda: all(not v.pixmap.isNull() for v in row.thumbs))
    views = list(row.thumbs)
    assert len(views) == 2 and all(not v.pixmap.isNull() for v in views)
    n = len(asked)
    row.set_assets(a, False)
    assert row.thumbs[:2] == views  # the same views, pictures and all
    _pump(qapp, 1500, until=lambda: not row.thumbs[2].pixmap.isNull())
    assert len(asked) == n + 1  # only the new one was fetched


def test_given_up_pictures_come_back_with_the_connection(qapp, window):
    from PySide6.QtCore import QSize
    from steam_artwork_finder.gui.widgets import ArtView
    from steam_artwork_finder.sgdb import Asset, GoneError

    win = window
    png = _png()
    online = {"now": False}

    def download(url):
        if "missing" in url:
            raise GoneError("HTTP 404")
        if not online["now"]:
            raise OSError("no network")
        return png

    win.sgdb.download = download
    a, b, c = (Asset(30 + i, "https://x/c.png", f"https://x/back{i}.png", 600, 900, "image/png", 0, "", "s")
               for i in range(3))
    gone = Asset(40, "https://x/c.png", "https://x/missing.png", 600, 900, "image/png", 0, "", "s")
    views = [ArtView(QSize(80, 120)) for _ in range(3)]
    for v, asset in zip(views, (a, b, gone)):
        win.load_thumb(asset, v)
    _pump(qapp, 2000, until=lambda: all(v.load_state and v.load_state[0] == "failed" for v in views))
    assert all(v.load_state[0] == "failed" for v in views)
    assert set(win._failed) == {id(views[0]), id(views[1])}  # not the one that isn't there

    online["now"] = True
    win.load_thumb(c, ArtView(QSize(80, 120)))  # anything downloading brings the rest back
    _pump(qapp, 2000, until=lambda: not views[0].pixmap.isNull() and not views[1].pixmap.isNull())
    assert not views[0].pixmap.isNull() and not views[1].pixmap.isNull() and not win._failed
    assert views[2].load_state[0] == "failed"

    # With nothing else downloading, one is tried now and then.
    online["now"] = False
    d = ArtView(QSize(80, 120))
    win._probe_wait = 50
    win.load_thumb(Asset(50, "https://x/c.png", "https://x/probe.png", 600, 900, "image/png", 0, "", "s"), d)
    _pump(qapp, 2000, until=lambda: d.load_state and d.load_state[0] == "failed")
    assert win._probe.isActive()
    online["now"] = True
    _pump(qapp, 3000, until=lambda: not d.pixmap.isNull())
    assert not d.pixmap.isNull()


def test_strip_waits_for_steams_piece_so_it_shows_once_in_order(qapp, window):
    from steam_artwork_finder.sgdb import Asset
    from steam_artwork_finder.steamstore import SteamAsset

    win = window
    png = _png()
    win.sgdb.download = lambda url: png
    win.open_game(0)
    _pump(qapp)
    row = win.page.rows["grid"]
    a = [Asset(60 + i, f"https://x/c{i}.png", f"https://x/order{i}.png", 600, 900, "image/png", 0, "", "s") for i in range(2)]
    row.wait_for_steam()
    row.set_assets(a, False)
    assert not row.thumbs  # SteamGridDB's options wait for Steam's answer
    steam = SteamAsset(1, "https://x/steam.png", "https://x/steam-t.png", 600, 900, "image/png", 0, "official", "Steam")
    row.set_steam([steam])
    assert [type(x).__name__ for x in row.assets] == ["SteamAsset", "Asset", "Asset"]

    row.wait_for_steam()  # Steam never answers: they show anyway after a while
    row._steam_cap.setInterval(10)
    row._steam_cap.start()
    _pump(qapp, 500, until=lambda: not row.steam_waiting)
    assert not row.steam_waiting and len(row.thumbs) == 2


def test_complete_files_with_bytes_after_their_end_are_whole(tmp_path):
    from PySide6.QtCore import QBuffer
    from PySide6.QtGui import QImage

    from steam_artwork_finder.gui import workers

    png = _png()
    assert workers._whole(png) and workers._whole(png + b"trailing editor data")
    assert not workers._whole(png[:-20])
    buf = QBuffer()
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img = QImage(64, 64, QImage.Format.Format_RGB32)
    img.fill(0x336699)
    img.save(buf, "JPEG")
    jpg = bytes(buf.data())
    assert workers._whole(jpg) and workers._whole(jpg + b"\0" * 500)
    assert not workers._whole(jpg[:len(jpg) // 2])
    # A big file in the cache is judged by its two ends only.
    big = tmp_path / "big"
    big.write_bytes(png[:-12] + b"\0" * (400 * 1024) + png[-12:])
    assert workers._whole_file(big)
    big.write_bytes(png[:-12] + b"\0" * (400 * 1024))
    assert not workers._whole_file(big)


def test_generic_titles_are_not_matched_on_the_steam_store(qapp, window):
    from steam_artwork_finder.steamstore import StoreGame

    win = window
    asked = []
    win.steam.match = lambda name: asked.append(name) or StoreGame(1, name)
    win.steam.assets = lambda appid: {}
    assert win._steam_lookup("Launcher", None) == (None, {}) and not asked
    assert win._steam_lookup("Celeste", None)[0].name == "Celeste" and asked == ["Celeste"]


def test_library_covers_shimmer_while_get_artwork_fetches_them(qapp, window):
    from steam_artwork_finder.gui import library

    win = window
    wall = win.start.wall
    delegate = wall.itemDelegate()
    sc = next(s for s in win.sfile.shortcuts if win.cover_path(s) is None)
    win.fetching = sc.appid
    win.retrying[sc.appid] = 2
    drawn = []
    real = library.draw_retrying
    library.draw_retrying = lambda p, r, n: drawn.append(n) or real(p, r, n)
    try:
        wall.viewport().repaint()
    finally:
        library.draw_retrying = real
    assert sc.appid in delegate._shimmered and drawn == [2]  # shimmering, and saying which retry
    win.fetching = None
    win.retrying.clear()


def test_library_covers_finish_fading_in_by_themselves(qapp, window):
    """A cover seen loading fades in all the way without a hover or anything else repainting it."""
    from PySide6.QtGui import QImage

    win = window
    img = QImage(60, 90, QImage.Format.Format_RGB32)
    img.fill(0x3366AA)
    grid = win.user.grid_dir
    grid.mkdir(parents=True, exist_ok=True)
    for s in win.sfile.shortcuts:
        img.save(str(grid / f"{s.appid}p.png"))
    win.refresh_all()
    d = win.start.wall.itemDelegate()
    _pump(qapp, 400, until=lambda: d._since)
    _pump(qapp, 1500, until=lambda: not d._since and not d._shimmered)
    assert not d._since and not d._shimmered


def test_pulse_keeps_its_own_copy_of_a_cells_rect(qapp):
    """A delegate's option.rect is gone once its paint ends; the repaint asked for must still happen."""
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QStyleOptionViewItem, QWidget
    from steam_artwork_finder.gui.widgets import pulse

    w = QWidget()
    opt = QStyleOptionViewItem()
    opt.rect = QRect(3, 4, 50, 60)
    pulse().want(w, opt.rect)
    del opt
    rects = pulse()._want[id(w)][1]
    assert rects[-1] == QRect(3, 4, 50, 60)
    pulse()._tick()  # and asking for the repaint doesn't fail
