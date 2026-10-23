"""Animation playback, the See all panel's loading, and late answers: regression tests for the
second GUI review (scrolling past options, retired animations, memory budgets, stale results)."""

import threading
import time

import pytest
from PySide6.QtCore import QMimeData, QSize, QUrl

from steam_artwork_finder.artwork import SLOTS
from steam_artwork_finder.sgdb import Asset, Game

from test_animated import _open_panel, _png, make_apng
from test_gui import _pump, qapp, window  # noqa: F401  (shared fixtures)

RED, GREEN, BLUE = (255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255)


def _anim(path, w=64, h=64, colours=(RED, GREEN)):
    path.write_bytes(make_apng(w, h, [(0, 0, w, h, c, 40, 0, 0) for c in colours]))
    return str(path)


def _still(i, w=600, h=900):
    return Asset(i, f"https://x/{i}.png", f"https://x/t{i}.png", w, h, "image/png", 0, "", "a")


def _animated(i, w=600, h=900):
    return Asset(i, f"https://x/{i}.png", f"https://x/{i}.webm", w, h, "image/png", 0, "", "a")


@pytest.fixture
def block_decodes():
    """Hold every decode thread busy until the returned event is set."""
    from steam_artwork_finder.gui import motion, workers

    gate = threading.Event()
    pool = motion.decode_pool()
    for _ in range(pool.maxThreadCount()):
        workers.run(gate.wait, 10, pool=pool)
    yield gate
    gate.set()
    pool.waitForDone(5000)


# ---- P1-1: the See all panel loads only what's in view once scrolling settles ----


def test_see_all_loads_nothing_for_options_passed_over_while_scrolling(qapp, window, monkeypatch):
    win = window
    t = _open_panel(qapp, win, monkeypatch, "grid")
    t._loaded(t.slot, 0, win.current_shortcut().appid, [_still(i) for i in range(60)])
    _pump(qapp, 50)
    asked = []
    monkeypatch.setattr(t.playing, "_sharpen", lambda it, a: asked.append((a.id, t._settle.isActive())))
    bar = t.list.verticalScrollBar()
    asked.clear()
    for v in range(0, bar.maximum(), max(1, bar.maximum() // 60)):
        bar.setValue(v)
        _pump(qapp, 16)
    assert not [aid for aid, scrolling in asked if scrolling]  # nothing started mid-scroll
    assert not t.playing.timer.isActive()  # the periodic sweep waits for the list to settle too
    passed_over = {aid for aid, _ in asked}
    _pump(qapp, 400)
    assert t.playing.timer.isActive()
    in_view = {a.id for _it, a in t.playing._in_view()}
    settled = {aid for aid, _ in asked} - passed_over
    assert settled and settled <= in_view  # once settled: just the options in view
    assert len({aid for aid, _ in asked}) < 30  # not the 60 it scrolled past


def test_see_all_finds_the_options_in_view_without_walking_the_list(qapp, window, monkeypatch):
    from steam_artwork_finder.gui import tray as traymod

    win = window
    t = _open_panel(qapp, win, monkeypatch, "grid")
    t._loaded(t.slot, 0, win.current_shortcut().appid, [_still(i) for i in range(80)])
    _pump(qapp, 50)
    lst = t.list
    view = lst.viewport().rect()

    def brute():
        return [lst.item(i).data(traymod.ROLE_ASSET).id for i in range(lst.count())
                if not lst.item(i).isHidden() and lst.visualItemRect(lst.item(i)).intersects(view)]

    for value in (0, lst.verticalScrollBar().maximum() // 3, lst.verticalScrollBar().maximum() // 2 + 37,
                  lst.verticalScrollBar().maximum()):
        lst.verticalScrollBar().setValue(value)
        _pump(qapp, 30)
        assert [a.id for _it, a in t.playing._in_view()] == brute()
    assert t.playing.items[42] is lst.item(42)


def test_see_all_asks_for_animated_previews_only_in_view(qapp, window, monkeypatch):
    win = window
    t = _open_panel(qapp, win, monkeypatch, "grid")
    asked = []
    monkeypatch.setattr(win, "load_thumb", lambda a, target: asked.append(a.id))
    t._loaded(t.slot, 0, win.current_shortcut().appid, [_animated(i) for i in range(60)])
    _pump(qapp, 400)
    t.playing.sweep()
    in_view = [a.id for _it, a in t.playing._in_view()]
    # Not one per option up front (each may start a video player): just those in view, once each.
    assert asked == in_view and len(asked) < 60


# ---- P2-1: a retired animation never shows a frame or starts a stream ----


def test_retired_animation_shows_nothing_more(qapp, tmp_path):
    from steam_artwork_finder.gui import motion

    p = _anim(tmp_path / "a.png")
    got = []
    m = motion.FrameMotion(p, QSize(64, 64), stream=True)
    m.frame.connect(got.append)
    m.start()
    _pump(qapp, 2000, until=lambda: got)
    assert got
    frames = motion.decode_frames(p, QSize(64, 64))
    got.clear()
    m.retire()
    m._decoded(frames)  # a decode that finished just before it was replaced
    m._next()
    assert got == []
    m2 = motion.FrameMotion(p, QSize(64, 64), stream=True)
    _pump(qapp, 300)
    m2.retire()
    m2._decoded(None)  # "too big: stream it", arriving the same way
    assert m2.stream is None  # no decoder thread left running for the session


def test_replaced_view_animation_is_retired(qapp, tmp_path):
    from steam_artwork_finder.gui import motion
    from steam_artwork_finder.gui.widgets import ArtView

    p = _anim(tmp_path / "a.png")
    v = ArtView(QSize(64, 64))
    v.show()
    v.set_motion("a", lambda: motion.make("frames", p, QSize(64, 64)))
    first = v._motion
    v.drop_motion()
    assert first.dead and v._motion is None
    v.close()


# ---- P2-2: discarded decodes don't run; the strip previews where the pointer rests ----


def test_retired_animation_never_decodes(qapp, tmp_path, monkeypatch, block_decodes):
    from steam_artwork_finder.gui import motion

    p = _anim(tmp_path / "a.png")
    ran = []
    real = motion._decode
    monkeypatch.setattr(motion, "_decode", lambda path, *a: ran.append(path) or real(path, *a))
    m = motion.FrameMotion(p, QSize(64, 64))
    m.retire()  # replaced while still waiting its turn
    block_decodes.set()
    motion.decode_pool().waitForDone(5000)
    _pump(qapp, 100)
    assert ran == []


def test_a_decode_under_way_stops_when_cancelled(qapp, tmp_path):
    from steam_artwork_finder.gui import motion

    p = _anim(tmp_path / "a.png", colours=[RED, GREEN, BLUE] * 10)
    cancel = threading.Event()
    cancel.set()
    assert motion.decode_frames(p, QSize(64, 64), cancel=cancel) == []
    assert len(motion.decode_frames(p, QSize(64, 64))) == 30


def test_hovering_a_strip_option_leaves_the_piece_alone(qapp, window):
    # A pick flies into the piece instead, so hovering mustn't change it first.
    win = window
    win.open_game(0)
    _pump(qapp, 200)
    row = win.page.rows["grid"]
    row.set_assets([_still(i) for i in range(4)], False)
    _pump(qapp, 50)
    before = row.caption.text()
    row.thumbs[2].hovered.emit(True)
    _pump(qapp, 400)
    assert row.caption.text() == before and not row._previewing


def _qpix():
    from PySide6.QtGui import QPixmap

    pm = QPixmap(20, 30)
    pm.fill()
    return pm


def test_preview_returns_to_the_same_animation(qapp, tmp_path):
    from steam_artwork_finder.gui import motion
    from steam_artwork_finder.gui.widgets import ArtView

    p, q = _anim(tmp_path / "a.png"), _anim(tmp_path / "b.png", colours=[BLUE, GREEN])
    v = ArtView(QSize(64, 64))
    v.show()
    made = []

    def factory(path):
        def make():
            made.append(path)
            return motion.make("frames", path, QSize(64, 64))
        return make

    v.set_motion("current", factory(p))
    current = v._motion
    v.set_motion("option", factory(q))  # a hover preview
    v.set_motion("current", factory(p))  # the pointer leaves
    assert v._motion is current and not current.dead  # carried on, not decoded again
    assert made == [p, q]
    v.drop_motion()
    v.close()


# ---- P2-3: every animation's frames share one budget ----


def test_animations_share_one_frame_budget(qapp, tmp_path, monkeypatch):
    import gc

    from steam_artwork_finder.gui import motion

    gc.collect()  # earlier tests' animations give their shares back now, not mid-test
    base = motion.frames_kept()
    monkeypatch.setattr(motion, "TOTAL_BUDGET", base + 3 * motion.MIN_SHARE)
    # As each decode starts it takes what it asks for, while there's room; then just a little.
    a, b, c = [0], [0], [0]
    assert motion._reserve_into(a, 2 * motion.MIN_SHARE, None) == 2 * motion.MIN_SHARE
    assert motion._reserve_into(b, 2 * motion.MIN_SHARE, None) == motion.MIN_SHARE
    assert motion._reserve_into(c, 2 * motion.MIN_SHARE, None) == motion.MIN_SHARE
    retired = threading.Event()
    retired.set()
    assert motion._reserve_into([0], motion.MIN_SHARE, retired) == 0  # retired meanwhile: nothing taken
    for share in (a, b, c):
        motion._release_share(share)
    assert motion.frames_kept() == base

    # Decoded, an animation keeps only what its frames take; retired, it gives that back.
    p = _anim(tmp_path / "a.png")
    one = 64 * 64 * 4
    ms = []
    for _ in range(3):
        m = motion.FrameMotion(p, QSize(64, 64), budget=2 * motion.MIN_SHARE)
        _pump(qapp, 3000, until=lambda: m.frames)
        assert m._share[0] == 2 * one
        ms.append(m)
    assert motion.frames_kept() == base + 3 * 2 * one
    for m in ms:
        m.retire()
    assert motion.frames_kept() == base


def test_a_view_hidden_for_a_while_lets_go_of_its_animation(qapp, tmp_path, monkeypatch):
    from steam_artwork_finder.gui import motion, widgets

    monkeypatch.setattr(widgets, "HIDDEN_DROP_MS", 50)
    p = _anim(tmp_path / "a.png")
    host = widgets.QWidget()
    v = widgets.ArtView(QSize(64, 64), parent=host)
    made = []
    v.set_motion("a", lambda: made.append(1) or motion.make("frames", p, QSize(64, 64)))
    assert made == []  # not on show: nothing decodes
    host.show()
    _pump(qapp, 50)
    assert made == [1]
    m = v._motion
    v.hide()
    _pump(qapp, 300)
    assert m.dead and v._motion is None
    v.show()
    _pump(qapp, 50)
    assert made == [1, 1] and v._motion is not None  # made again when shown
    v.drop_motion()
    host.close()


# ---- O1: decodes run side by side, within one memory limit ----


def test_decodes_run_side_by_side_within_the_memory_limit(qapp, tmp_path, monkeypatch):
    from steam_artwork_finder.gui import motion, workers

    small = [_anim(tmp_path / f"s{i}.png", 32, 32) for i in range(3)]
    big = [_anim(tmp_path / f"b{i}.png", 800, 800) for i in range(3)]
    budget = 8 * 1024 * 1024
    need_big = motion.decode_need(big[0], None, budget)
    gate = motion._Gate(int(need_big * 1.5))  # room for one big decode at a time
    monkeypatch.setattr(motion, "_gate", gate)
    monkeypatch.setattr(motion, "DECODE_THREADS", 3)
    pool = motion.QThreadPool()
    pool.setMaxThreadCount(3)
    lock = threading.Lock()
    now = {"n": 0, "big": 0, "most": 0, "most_big": 0}
    real = motion._decode

    def slow(path, *a):
        with lock:
            now["n"] += 1
            now["big"] += path in big
            now["most"] = max(now["most"], now["n"])
            now["most_big"] = max(now["most_big"], now["big"])
            assert gate.held <= gate.limit
        time.sleep(0.15)
        try:
            return real(path, *a)
        finally:
            with lock:
                now["n"] -= 1
                now["big"] -= path in big

    monkeypatch.setattr(motion, "_decode", slow)
    done = []
    for path in small + big:
        workers.run(motion.decode_frames, path, None, budget=budget, on_done=done.append, pool=pool)
    pool.waitForDone(10000)
    _pump(qapp, 200, until=lambda: len(done) == 6)
    assert len(done) == 6 and all(len(f) == 2 for f in done)
    assert now["most"] >= 2  # small ones decode together
    assert now["most_big"] == 1  # big ones never do
    assert gate.peak <= gate.limit and gate.held == 0 and gate.running == 0


def test_one_decode_bigger_than_the_limit_runs_alone(qapp, tmp_path, monkeypatch):
    from steam_artwork_finder.gui import motion

    p = _anim(tmp_path / "a.png", 300, 300)
    gate = motion._Gate(1024)  # smaller than any decode
    monkeypatch.setattr(motion, "_gate", gate)
    assert len(motion.decode_frames(p, QSize(64, 64))) == 2
    assert gate.held == 0
    # While it holds the gate, another waits, and gives up once cancelled.
    assert gate.acquire(10 ** 9)
    cancel = threading.Event()
    out = []
    th = threading.Thread(target=lambda: out.append(motion.decode_frames(p, QSize(64, 64), cancel=cancel)))
    th.start()
    time.sleep(0.2)
    assert th.is_alive()  # waiting its turn, holding nothing
    cancel.set()
    th.join(2)
    gate.release(10 ** 9)
    assert out == [[]] and gate.held == 0


# ---- P2-4: first pictures: at most two video players, image files off the GUI thread ----


def test_first_frames_start_two_players_at_most(qapp, monkeypatch):
    from steam_artwork_finder.gui import motion

    started = []

    class FakePlayer:
        def __init__(self):
            from PySide6.QtCore import QObject, Signal

            class S(QObject):
                errorOccurred = Signal()
            self._s = S()
            self.errorOccurred = self._s.errorOccurred

    class FakeVideo(motion.Motion):
        def __init__(self, path):
            super().__init__()
            self.path, self.player = path, FakePlayer()

        def start(self):
            started.append(self)

    monkeypatch.setattr(motion, "VideoMotion", FakeVideo)
    got = []
    for i in range(5):
        motion.first_frame("video", f"v{i}", got.append)
    assert len(started) == 2
    started[0].frame.emit(_qpix())
    _pump(qapp, 50)
    assert len(got) == 1 and len(started) == 3 and started[0].dead
    for m in started[1:]:
        m.frame.emit(_qpix())
    _pump(qapp, 50)
    for m in started[3:]:
        m.frame.emit(_qpix())
    _pump(qapp, 50)
    assert len(got) == 5 and not motion._grabbing


def test_first_frame_of_an_image_decodes_off_the_gui_thread(qapp, tmp_path):
    from steam_artwork_finder.gui import motion

    p = _anim(tmp_path / "a.png", 400, 600)
    got = []
    motion.first_frame("frames", p, got.append, QSize(100, 150))
    assert got == []  # not decoded there and then
    _pump(qapp, 3000, until=lambda: got)
    assert got and got[0].size() == QSize(100, 150)  # and shrunk as it decoded


# ---- P2-5: late answers don't replace newer ones ----


def test_late_options_for_another_game_are_ignored(qapp, window, monkeypatch):
    win = window
    win.config.api_key = "k"
    win.open_game(0)
    # Rows hold their options until the (stubbed) Steam store lookup answers from its worker.
    _pump(qapp, 4000, until=lambda: not win.page.rows["grid"].steam_waiting)
    sc = win.current_shortcut()
    calls = []
    monkeypatch.setattr(win, "fetch_assets", lambda gid, key, page, cb: calls.append((gid, key, cb)))
    games = {"g": Game(1, "Old")}
    monkeypatch.setattr(win, "current_game", lambda: games["g"])
    win._load_rows(sc, games["g"])
    old = [c for c in calls if c[1] == "grid"][0]
    games["g"] = Game(2, "New")  # Change Game
    win._load_rows(sc, games["g"])
    new = [c for c in calls if c[1] == "grid" and c[0] == 2][0]
    new[2]([_still(2)])
    old[2]([_still(1)])  # the old game's answer lands last
    assert [a.id for a in win.page.rows["grid"].assets] == [2]


def test_see_all_ignores_a_late_answer_and_going_home(qapp, window, monkeypatch):
    win = window
    t = _open_panel(qapp, win, monkeypatch, "grid")
    appid = win.current_shortcut().appid
    gen = t._gen
    t.open_slot(t.slot)  # opened again (animated turned off, say)
    t._loaded(t.slot, 0, appid, [_still(1)], None, gen)
    assert t.list.count() == 0
    monkeypatch.setattr(win, "current_shortcut", lambda: None)
    t._loaded(t.slot, 0, appid, [_still(1)], None, t._gen)  # gone home meanwhile: no error
    assert t.list.count() == 0


def test_steam_lookup_for_a_changed_steam_game_is_ignored(qapp, window, monkeypatch):
    from steam_artwork_finder.gui import app as appmod

    win = window
    win.open_game(0)
    _pump(qapp, 100)
    sc = win.current_shortcut()
    win.steam_found.pop(sc.appid, None)
    jobs = []
    monkeypatch.setattr(appmod.workers, "run", lambda fn, *a, on_done=None, **k: jobs.append(on_done))
    win.load_steam()
    win.config.set_steam_match(sc.appid, 99, "Another")
    jobs[-1]((None, {}))
    assert sc.appid not in win.steam_found


# ---- P3 ----


def test_card_cover_decodes_at_its_own_size_and_scale(qapp, window):
    win = window
    win.open_game(0)
    _pump(qapp, 100)
    sc = win.current_shortcut()
    grid = win.user.grid_dir
    grid.mkdir(parents=True, exist_ok=True)
    (grid / f"{sc.appid}p.png").write_bytes(make_apng(60, 90, [(0, 0, 60, 90, RED, 40, 0, 0),
                                                               (0, 0, 60, 90, BLUE, 40, 0, 0)]))
    win.forget_files(sc.appid)
    key, _f = win.page._motion(sc, "grid", QSize(72, 108))
    assert key is not None
    assert key[1:3] == (72, 108)  # not rounded up to 160 × 120
    assert key[3] == round(win.page.devicePixelRatioF() * 100)  # another screen scale, another decode


def test_see_all_sharpens_again_once_its_picture_is_let_go(qapp, window, monkeypatch):
    from types import SimpleNamespace

    from steam_artwork_finder.gui import tray as traymod

    win = window
    monkeypatch.setattr(win, "sgdb", SimpleNamespace(download=lambda url: _png(600, 900, GREEN)))
    t = _open_panel(qapp, win, monkeypatch, "grid")
    t._loaded(t.slot, 0, win.current_shortcut().appid, [_still(77)])
    it = t.list.item(0)
    t.playing.sweep()
    _pump(qapp, 3000, until=lambda: it.data(traymod.ROLE_FULL) is not None)
    assert not [k for k in t.playing._asked if k[0] == "still"]
    for k in [k for k in traymod._full if k[0] == 77]:
        del traymod._full[k]  # pushed out by other games' options
    it.setData(traymod.ROLE_FULL, None)
    t.playing.sweep()
    _pump(qapp, 3000, until=lambda: it.data(traymod.ROLE_FULL) is not None)
    assert it.data(traymod.ROLE_FULL) is not None


def test_see_all_live_frames_come_ready_to_draw(qapp, window, monkeypatch, tmp_path):
    from steam_artwork_finder.gui import tray as traymod

    win = window
    win.motion_files[7] = ("frames", _anim(tmp_path / "a.png", 70, 90))  # not quite a cover's shape
    t = _open_panel(qapp, win, monkeypatch, "grid")
    t._loaded(t.slot, 0, win.current_shortcut().appid, [_animated(7)])
    t.playing.sweep()
    _pump(qapp, 3000, until=lambda: t.playing.motions.get(7, {}).get("pm") is not None)
    pm = t.playing.motions[7]["pm"]
    dpr = t.list.devicePixelRatioF()
    s = traymod.thumb_size("grid")
    assert pm.size() == QSize(round(s.width() * dpr), round(s.height() * dpr))
    assert t.list.item(0).data(traymod.ROLE_PIX) is not None  # its first picture stands in as its still


def test_panel_preview_says_previewing(qapp, window):
    from steam_artwork_finder.gui.theme import C

    win = window
    win.open_game(0)
    _pump(qapp, 100)
    row = win.page.rows["grid"]
    row._set_caption("Not applied yet", "new")
    row.art.new = True
    win._tray_preview(SLOTS["grid"], _qpix(), _still(5))
    assert row.caption.text() == "Previewing" and C["blue"] in row.caption.styleSheet()
    assert not row.art.new


def test_same_picture_keeps_its_scaled_copy(qapp):
    from steam_artwork_finder.gui.widgets import ArtView

    v = ArtView(QSize(64, 96))
    pm = _qpix()
    v.set_pixmap(pm)
    v._render()
    scaled = v._scaled
    v.set_pixmap(pm)  # a refresh
    assert v._scaled is scaled


def test_dragging_over_a_row_doesnt_decode_the_picture(qapp, window, monkeypatch):
    from steam_artwork_finder.gui import gamepage, localfile

    win = window
    win.open_game(0)
    _pump(qapp, 100)
    row = win.page.rows["grid"]
    monkeypatch.setattr(gamepage.localfile, "source", lambda mime: pytest.fail("decoded on drag-over"))
    mime = QMimeData()
    mime.setImageData(_qpix().toImage())

    class Ev:
        accepted = None

        def mimeData(self):
            return mime

        def acceptProposedAction(self):
            self.accepted = True

        def ignore(self):
            self.accepted = False

    e = Ev()
    row.dragEnterEvent(e)
    assert e.accepted
    text = QMimeData()
    text.setText("hello")
    assert not localfile.usable(text)
    files = QMimeData()
    files.setUrls([QUrl.fromLocalFile("/tmp/x.PNG")])
    assert localfile.usable(files)


def test_a_file_pick_lands_on_its_game_even_after_moving_on(qapp, window, monkeypatch, tmp_path):
    from steam_artwork_finder.gui import app as appmod

    win = window
    win.open_game(0)
    _pump(qapp, 100)
    sc = win.current_shortcut()
    f = tmp_path / "c.png"
    f.write_bytes(_png(600, 900, RED))
    jobs = []
    real = appmod.workers.run
    monkeypatch.setattr(appmod.workers, "run",
                        lambda fn, *a, **k: jobs.append((fn, a, k)) if fn is appmod.localfile.load_path
                        else real(fn, *a, **k))
    win.stage_file(SLOTS["grid"], path=str(f))
    win.open_game(1)  # moved on while it converted
    _pump(qapp, 100)
    fn, a, k = jobs[0]
    k["on_done"](fn(*a))
    assert win.pending.get(sc.appid, "grid") is not None
    assert win.pending.get(win.current_shortcut().appid, "grid") is None
