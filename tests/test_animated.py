import struct
import time
import zlib

import pytest
from PySide6.QtCore import Qt

from steam_artwork_finder.artwork import SLOTS, is_animated, save_asset
from steam_artwork_finder.sgdb import Asset, SteamGridDB
from steam_artwork_finder.gui.tray import ROLE_ASSET
from steam_artwork_finder.shortcuts import ShortcutsFile

from test_sgdb import FakeResp, FakeSession

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def _rgba_png_parts(w, h, rgba):
    """IHDR body and compressed image data for a flat colour."""
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes(rgba) * w for _ in range(h))
    return ihdr, zlib.compress(raw)


def make_apng(w, h, frames):
    """frames: [(x, y, fw, fh, rgba, delay_ms, dispose, blend)]; the first is the default image."""
    ihdr, _ = _rgba_png_parts(w, h, (0, 0, 0, 0))
    out = PNG_SIG + _chunk(b"IHDR", ihdr) + _chunk(b"acTL", struct.pack(">II", len(frames), 0))
    seq = 0
    for i, (x, y, fw, fh, rgba, ms, dispose, blend) in enumerate(frames):
        out += _chunk(b"fcTL", struct.pack(">IIIIIHHBB", seq, fw, fh, x, y, ms, 1000, dispose, blend))
        seq += 1
        _, data = _rgba_png_parts(fw, fh, rgba)
        if i == 0:
            out += _chunk(b"IDAT", data)
        else:
            out += _chunk(b"fdAT", struct.pack(">I", seq) + data)
            seq += 1
    return out + _chunk(b"IEND", b"")


def test_animated_files_are_recognised():
    apng = make_apng(4, 4, [(0, 0, 4, 4, (255, 0, 0, 255), 100, 0, 0), (0, 0, 4, 4, (0, 0, 255, 255), 100, 0, 0)])
    assert is_animated(apng)
    _ihdr, data = _rgba_png_parts(4, 4, (1, 2, 3, 255))
    still = PNG_SIG + _chunk(b"IHDR", _ihdr) + _chunk(b"IDAT", data) + _chunk(b"IEND", b"")
    assert not is_animated(still)
    webp = b"RIFF\x00\x00\x00\x00WEBPVP8X\x0a\x00\x00\x00" + bytes([0x02]) + b"\x00" * 9
    assert is_animated(webp)
    assert not is_animated(webp[:20] + b"\x00" + webp[21:])
    assert not is_animated(b"\xff\xd8\xff\xe0")


def test_animated_webp_is_saved_as_png_for_steam(steam_root):
    path = steam_root / "userdata/12345678/config/shortcuts.vdf"
    sc = ShortcutsFile(path).shortcuts[0]
    grid = path.parent / "grid"
    webp = b"RIFF\x00\x00\x00\x00WEBPVP8X\x0a\x00\x00\x00\x02" + b"\x00" * 9
    for key, name in {"grid": f"{sc.appid}p.png", "hero": f"{sc.appid}_hero.png"}.items():
        dest = save_asset(grid, sc, SLOTS[key], webp, "image/webp").new_file
        assert dest.name == name and dest.read_bytes() == webp


def test_animated_options_only_where_steam_plays_them():
    for key in ("grid", "wide", "hero", "logo"):
        q = SLOTS[key].search(animated=True)
        assert q["types"] == "static,animated" and "image/webp" in q["mimes"]
        assert SLOTS[key].search()["types"] == "static"
    assert SLOTS["icon"].search(animated=True)["types"] == "static"

    s = FakeSession({"/grids/game/7": FakeResp(200, {"success": True, "data": [
        {"id": 1, "url": "https://x/a.png", "thumb": "https://x/a.webm", "mime": "image/png"},
        {"id": 2, "url": "https://x/b.png", "thumb": "https://x/b_thumb.png", "mime": "image/png"},
    ]})})
    a = SteamGridDB("k", s).assets("grid", 7, animated=True)
    assert [x.animated for x in a] == [True, False]
    assert s.calls[0][1]["types"] == "static,animated"
    assert not Asset(1, "u", "https://x/t.jpg", 0, 0, "", 0, "", "").animated


from test_gui import _pump, qapp, window  # noqa: E402,F401  (shared fixtures)


def test_apng_frames_blend_and_dispose(qapp):
    from steam_artwork_finder.gui.motion import apng_frames

    red, blue, clear = (255, 0, 0, 255), (0, 0, 255, 255), (0, 0, 0, 0)
    data = make_apng(4, 4, [
        (0, 0, 4, 4, red, 200, 0, 0),  # all red, kept
        (2, 2, 2, 2, blue, 50, 1, 0),  # a blue corner, cleared afterwards
        (0, 0, 2, 2, clear, 0, 0, 1),  # transparent laid over: changes nothing
    ])
    frames = apng_frames(data)
    assert [ms for _, ms in frames] == [200, 50, 100]  # a zero delay plays at 10 fps
    px = [(f.pixelColor(0, 0).name(), f.pixelColor(3, 3).name()) for f, _ in frames]
    assert px == [("#ff0000", "#ff0000"), ("#ff0000", "#0000ff"), ("#ff0000", "#000000")]
    assert frames[2][0].pixelColor(3, 3).alpha() == 0  # disposed to transparent
    assert frames[2][0].pixelColor(0, 0).alpha() == 255


def test_decode_frames_reads_webp_named_png(qapp, tmp_path):
    from PySide6.QtCore import QSize
    from steam_artwork_finder.gui.motion import decode_frames

    Image = pytest.importorskip("PIL.Image")
    frames = [Image.new("RGBA", (60, 90), (i * 50, 0, 0, 255)) for i in range(4)]
    p = tmp_path / "123p.png"  # how Steam's folder holds it
    frames[0].save(p, format="WEBP", save_all=True, append_images=frames[1:], duration=80, loop=0)
    out = decode_frames(str(p), QSize(20, 30))
    assert len(out) == 4 and out[0][0].width() == 20 and out[1][1] == 80


def test_decode_frames_stays_within_budget(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSize
    from steam_artwork_finder.gui import motion

    Image = pytest.importorskip("PIL.Image")
    frames = [Image.new("RGBA", (200, 100), (i * 8, 0, 0, 255)) for i in range(30)]
    p = tmp_path / "hero.png"
    frames[0].save(p, format="WEBP", save_all=True, append_images=frames[1:], duration=40, loop=0)
    one = 100 * 50 * 4  # a frame scaled down to cover 100x50
    monkeypatch.setattr(motion, "FRAME_BUDGET", one * 7)
    out = motion.decode_frames(str(p), QSize(100, 50))
    assert out[0][0].width() == 100
    assert 1 < len(out) <= 7 and sum(f.sizeInBytes() for f, _ in out) <= one * 7
    assert sum(ms for _, ms in out) == 30 * 40  # dropped frames' time goes to the ones kept


def test_frames_halve_when_count_unknown(qapp):
    from PySide6.QtGui import QImage
    from steam_artwork_finder.gui.motion import _Frames

    img = QImage(10, 10, QImage.Format.Format_ARGB32)
    out = _Frames(budget=img.sizeInBytes() * 4)
    for _ in range(20):
        out.add(img, 10)
    got = out.frames()
    assert len(got) <= 4 and sum(ms for _, ms in got) == 200


def test_library_plays_animated_covers_on_screen(qapp, window):
    win = window
    sc = win.sfile.shortcuts[0]
    grid = win.user.grid_dir
    grid.mkdir(parents=True, exist_ok=True)
    red, blue = (255, 0, 0, 255), (0, 0, 255, 255)
    (grid / f"{sc.appid}p.png").write_bytes(
        make_apng(60, 90, [(0, 0, 60, 90, red, 40, 0, 0), (0, 0, 60, 90, blue, 40, 0, 0)]))
    win._cov.clear()
    win.go_home()
    wall = win.start.wall
    wall.viewport().repaint()
    anim = wall.anim
    _pump(qapp, 3000, until=lambda: any(a["frames"] for a in anim.anims.values()))
    (a,) = anim.anims.values()
    assert len(a["frames"]) == 2
    seen = set()
    for _ in range(20):
        _pump(qapp, 30)
        wall.viewport().repaint()
        seen.add(a["i"])
    assert seen == {0, 1}  # it plays

    win.config.data["show_animated"] = False  # the setting stills it
    wall.viewport().repaint()
    assert not anim.anims
    win.config.data["show_animated"] = True


def test_own_animated_file_is_kept_as_it_is():
    pytest.importorskip("PySide6")
    from steam_artwork_finder.gui import localfile

    data = make_apng(40, 60, [(0, 0, 40, 60, (255, 0, 0, 255), 100, 0, 0), (0, 0, 40, 60, (0, 0, 255, 255), 100, 0, 0)])
    cover = localfile.load(SLOTS["grid"], data, "moving.png", "/x/moving.png")
    assert cover.pick.data == data and cover.pick.asset.animated
    icon = localfile.load(SLOTS["icon"], data, "moving.png")
    assert not icon.pick.asset.animated  # Steam doesn't play icons


def test_see_all_counts_and_filters_animated(qapp, window, monkeypatch):
    from steam_artwork_finder.gui import tray as traymod

    win = window
    monkeypatch.setattr(win, "load_thumb", lambda a, target: None)
    win.current = 0
    t = win.tray
    t.slot, t.page, t.assets = SLOTS["wide"], 0, []
    t.list.clear()
    mk = lambda i, thumb: Asset(i, f"https://x/{i}.png", thumb, 920, 430, "image/png", 0, "alternate", "a")
    t._loaded(t.slot, 0, win.current_shortcut().appid,
              [mk(1, "https://x/1.png"), mk(2, "https://x/2.webm"), mk(3, "https://x/3.png")])
    assert t.status.text() == "3 options, 1 animated"
    chip = next(b for b in t.chips.buttons() if b.property("style_key") == "animated")
    chip.click()
    shown = [t.list.item(i).data(traymod.ROLE_ASSET).id for i in range(t.list.count()) if not t.list.item(i).isHidden()]
    assert shown == [2]

    t.assets = []
    t.list.clear()
    t._loaded(t.slot, 0, win.current_shortcut().appid, [mk(1, "https://x/1.png")])
    assert "none animated" in t.status.text()
    assert not any(b.property("style_key") == "animated" for b in t.chips.buttons())


def test_see_all_slides_over_the_page_without_squeezing_it(qapp, window, monkeypatch):
    from steam_artwork_finder.artwork import SLOTS as S

    win = window
    win.open_game(0)
    _pump(qapp, 200)
    monkeypatch.setattr(win, "current_game", lambda: object())
    monkeypatch.setattr(win.tray, "load", lambda: None)
    before = win.page.width()
    win.open_tray(S["grid"])
    t = win.tray
    assert t.is_open() and t._ghost.isVisible() and not t.isVisible()  # a picture of it slides in
    _pump(qapp, 400)
    assert t.is_open() and win.page.width() == before  # lies over the page
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtTest import QTest
    stage = QRect(win.stage.mapTo(win, QPoint(0, 0)), win.stage.size())
    assert t.geometry().right() == stage.right() and t.geometry().top() == stage.top()
    assert t.isVisible() and not t._ghost.isVisible()  # the real panel once it has settled
    # the rest of the window is dimmed, and the mouse reaches only the panel
    assert win.scrim.isVisible() and win.scrim.is_covering()
    page_point = win.page.mapTo(win, QPoint(40, 40))
    assert win.childAt(page_point) is win.scrim
    assert t.isAncestorOf(win.childAt(t.mapTo(win, QPoint(t.width() // 2, t.height() // 2))))
    # a click on the dimmed part closes it, sliding out
    QTest.mouseClick(win.scrim, Qt.MouseButton.LeftButton, pos=page_point)
    assert not t.is_open()
    _pump(qapp, 400)
    assert not t.isVisible() and not t._ghost.isVisible() and win.page.width() == before
    assert not win.scrim.isVisible()

    win.open_tray(S["grid"])  # going home mid-slide leaves nothing behind
    win.go_home()
    _pump(qapp, 400)
    assert not t.is_open() and not t.isVisible() and not t._ghost.isVisible()
    assert not win.scrim.isVisible()


def test_pop_overs_and_menus_dim_the_window(qapp, window):
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    win = window
    win.keypop.open_above(win.statusbar.connection.mapToGlobal(QPoint(10, 0)), align_right=True)
    _pump(qapp, 300)
    assert win.scrim.isVisible() and win.scrim.is_covering()
    assert win.childAt(win.header.mapTo(win, QPoint(400, 10))) is win.scrim
    QTest.mouseClick(win.scrim, Qt.MouseButton.LeftButton, pos=QPoint(400, 300))
    _pump(qapp, 400)
    assert not win.keypop.isVisible() and not win.scrim.isVisible()

    from steam_artwork_finder.gui.gamemenu import FadingPopup
    menu = FadingPopup(win)
    menu.move(win.mapToGlobal(QPoint(100, 100)))
    menu.show()
    _pump(qapp, 300)
    assert win.scrim.is_covering()
    menu.hide()
    _pump(qapp, 400)
    assert not win.scrim.isVisible()


def test_see_all_fills_its_rows(qapp, window, monkeypatch):
    win = window
    win.open_game(0)
    _pump(qapp, 200)
    monkeypatch.setattr(win, "current_game", lambda: object())
    monkeypatch.setattr(win, "load_thumb", lambda a, target: None)
    t = win.tray
    monkeypatch.setattr(t, "load", lambda: None)
    mk = lambda i: Asset(i, f"https://x/{i}.png", f"https://x/{i}.png", 600, 900, "image/png", 0, "", "a")
    per_row = {}
    for key in ("grid", "wide", "hero", "logo", "icon"):
        win.open_tray(SLOTS[key])
        _pump(qapp, 400)
        t._loaded(t.slot, 0, win.current_shortcut().appid, [mk(i) for i in range(8)])
        _pump(qapp, 50)
        ys = [t.list.visualItemRect(t.list.item(i)).y() for i in range(8)]
        per_row[key] = ys.count(ys[0])
        assert t.title.text() == SLOTS[key].label  # it opens on the piece asked for
    assert per_row == {"grid": 4, "wide": 3, "hero": 2, "logo": 3, "icon": 6}
    assert win.page.width() > t.width()  # still leaves most of the page in view at 1280 wide


def test_see_all_plays_animated_options_while_in_view(qapp, window, monkeypatch, tmp_path):
    win = window
    win.open_game(0)
    _pump(qapp, 200)
    monkeypatch.setattr(win, "current_game", lambda: object())
    monkeypatch.setattr(win, "load_thumb", lambda a, target: None)
    t = win.tray
    monkeypatch.setattr(t, "load", lambda: None)
    f = tmp_path / "anim.png"
    f.write_bytes(make_apng(60, 90, [(0, 0, 60, 90, (255, 0, 0, 255), 40, 0, 0),
                                     (0, 0, 60, 90, (0, 0, 255, 255), 40, 0, 0)]))
    win.motion_files[7] = ("frames", str(f))
    win.open_tray(SLOTS["grid"])
    _pump(qapp, 400)
    t._loaded(t.slot, 0, win.current_shortcut().appid,
              [Asset(7, "https://x/7.png", "https://x/7.webm", 600, 900, "image/png", 0, "", "a"),
               Asset(8, "https://x/8.png", "https://x/8.png", 600, 900, "image/png", 0, "", "a")])
    _pump(qapp, 3000, until=lambda: t.playing.motions.get(7, {}).get("pm") is not None)
    assert list(t.playing.motions) == [7]  # only the animated one
    seen = set()
    for _ in range(12):
        _pump(qapp, 30)
        seen.add(t.playing.frame(t.list.item(0).data(ROLE_ASSET)).toImage().pixelColor(5, 5).name())
    assert seen == {"#ff0000", "#0000ff"}  # it plays
    win.tray.close_tray()
    _pump(qapp, 400)
    assert not t.playing.motions  # stopped once closed


def test_game_page_banner_plays_with_its_logo_over_it(qapp, window):
    win = window
    sc = win.sfile.shortcuts[0]
    grid = win.user.grid_dir
    grid.mkdir(parents=True, exist_ok=True)
    red, blue = (255, 0, 0, 255), (0, 0, 255, 255)
    (grid / f"{sc.appid}_hero.png").write_bytes(
        make_apng(192, 62, [(0, 0, 192, 62, red, 40, 0, 0), (0, 0, 192, 62, blue, 40, 0, 0)]))
    ihdr, data = _rgba_png_parts(64, 24, (255, 255, 255, 255))
    (grid / f"{sc.appid}_logo.png").write_bytes(PNG_SIG + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", data)
                                                + _chunk(b"IEND", b""))
    win.open_game(0)
    card = win.page.card
    _pump(qapp, 3000, until=lambda: card.layers["banner"].m is not None and card.banner is not None
          and not card.banner.isNull() and card.banner.width() > 1)
    assert card.layers["banner"].key is not None  # the banner plays on the page's own header
    assert card._logo_art() is not None  # with the logo over it
    seen = set()
    for _ in range(15):
        _pump(qapp, 30)
        seen.add(card.banner.toImage().pixelColor(5, 5).name())
    assert seen == {"#ff0000", "#0000ff"}

    win.config.data["logo_on_banner"] = False
    win.page._banner()
    assert card._logo_art() is None
    win.config.data["logo_on_banner"] = True


def test_page_banner_decodes_at_the_size_it_is_shown(qapp, window):
    from PySide6.QtCore import QSize
    win = window
    sc = win.sfile.shortcuts[0]
    grid = win.user.grid_dir
    grid.mkdir(parents=True, exist_ok=True)
    red, blue = (255, 0, 0, 255), (0, 0, 255, 255)
    (grid / f"{sc.appid}_hero.png").write_bytes(
        make_apng(1920, 620, [(0, 0, 1920, 620, red, 40, 0, 0), (0, 0, 1920, 620, blue, 40, 0, 0)]))
    win.open_game(0)
    card = win.page.card
    big = lambda: card.banner is not None and card.banner.width() >= card.width()
    _pump(qapp, 5000, until=big)
    assert big()  # as wide as the banner is shown, not a thumbnail
    dpr = card.devicePixelRatioF()
    exact = lambda: card.banner.size() == QSize(round(card.width() * dpr), round(card.height() * dpr))
    _pump(qapp, 2000, until=exact)
    assert exact()  # cut to the card, so it's drawn pixel for pixel, not scaled a second time
    first = card.layers["banner"].key
    win.resize(win.width() + 400, win.height())
    _pump(qapp, 5000, until=lambda: card.layers["banner"].key != first and big())
    assert card.layers["banner"].key != first and big()  # decoded again for the wider window


def test_a_big_animation_streams_every_frame_on_time(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSize
    from steam_artwork_finder.gui import motion

    colours = [(i * 20, 255 - i * 20, 0, 255) for i in range(12)]
    f = tmp_path / "hero.png"
    f.write_bytes(make_apng(400, 130, [(0, 0, 400, 130, c, 40, 0, 0) for c in colours]))
    bound = QSize(300, 80)
    one = 300 * 80 * 4
    assert motion.decode_frames(str(f), bound, budget=one * 4, crop=True, keep_all=True) is None  # won't fit
    m = motion.make("frames", str(f), bound, one * 4, crop=True, stream=True)
    got = []
    m.frame.connect(lambda pm: got.append((time.monotonic(), pm)))
    m.start()
    _pump(qapp, 3000, until=lambda: m.stream is not None and len(got) >= 2)
    start = len(got)
    _pump(qapp, 1000)
    m.stop()
    m.deleteLater()
    shown = got[start:]
    assert all(pm.size() == bound for _, pm in shown)  # every frame full size, cut to the bound
    assert 20 <= len(shown) <= 27  # 40 ms apiece for a second: on time, none dropped or slowed
    names = {pm.toImage().pixelColor(5, 5).name() for _, pm in got}
    assert len(names) == 12  # every frame, none dropped


def _png(w, h, rgba):
    ihdr, data = _rgba_png_parts(w, h, rgba)
    return PNG_SIG + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", data) + _chunk(b"IEND", b"")


def _open_panel(qapp, win, monkeypatch, key="grid"):
    win.open_game(0)
    _pump(qapp, 200)
    monkeypatch.setattr(win, "current_game", lambda: object())
    monkeypatch.setattr(win, "load_thumb", lambda a, target: None)
    t = win.tray
    monkeypatch.setattr(t, "load", lambda: None)
    win.open_tray(SLOTS[key])
    _pump(qapp, 400)
    return t


def test_see_all_click_picks_an_option(qapp, window, monkeypatch):
    win = window
    t = _open_panel(qapp, win, monkeypatch, "hero")
    assert t.title.text() == "Banner"
    sc = win.current_shortcut()
    mk = lambda i: Asset(i, f"https://x/{i}.png", f"https://x/{i}.png", 1920, 620, "image/png", 0, "", "a")
    t._loaded(t.slot, 0, sc.appid, [mk(1), mk(2)])
    _pump(qapp, 50)
    from PySide6.QtTest import QTest
    QTest.mouseClick(t.list.viewport(), Qt.MouseButton.LeftButton,
                     pos=t.list.visualItemRect(t.list.item(1)).center())
    pick = win.pending.get(sc.appid, "hero")
    assert pick is not None and pick.asset.id == 2  # one click, picked
    assert t.list.item(1).text().endswith(", picked")
    t.ok_btn.click()  # OK closes the panel and keeps the pick
    _pump(qapp, 50)
    assert not t.is_open() and win.pending.get(sc.appid, "hero").asset.id == 2


def test_see_all_loads_full_quality_stills(qapp, window, monkeypatch):
    from types import SimpleNamespace

    from steam_artwork_finder.gui import tray as traymod

    win = window
    full = _png(600, 900, (0, 200, 0, 255))
    monkeypatch.setattr(win, "sgdb", SimpleNamespace(download=lambda url: full))
    t = _open_panel(qapp, win, monkeypatch, "grid")
    a = Asset(31, "https://x/full31.png", "https://x/thumb31.png", 600, 900, "image/png", 0, "", "a")
    t._loaded(t.slot, 0, win.current_shortcut().appid, [a])
    t.playing.sweep()
    it = t.list.item(0)
    _pump(qapp, 3000, until=lambda: it.data(traymod.ROLE_FULL) is not None)
    pm = it.data(traymod.ROLE_FULL)
    tile = traymod.thumb_size("grid")
    assert pm is not None and tile.width() <= pm.width() < 600  # shrunk as it decoded, but sharp
    seen = []
    t.preview.connect(lambda slot, pm, asset: seen.append(pm))
    t._hover(it)
    assert seen and seen[-1].cacheKey() == pm.cacheKey()  # the page previews the full-quality picture


def test_see_all_swaps_in_the_whole_animation(qapp, window, monkeypatch, tmp_path):
    from types import SimpleNamespace

    win = window
    anim = make_apng(60, 90, [(0, 0, 60, 90, (255, 0, 0, 255), 40, 0, 0),
                              (0, 0, 60, 90, (0, 255, 0, 255), 40, 0, 0),
                              (0, 0, 60, 90, (0, 0, 255, 255), 40, 0, 0)])
    monkeypatch.setattr(win, "sgdb", SimpleNamespace(download=lambda url: anim))
    preview = tmp_path / "preview.png"
    preview.write_bytes(make_apng(60, 90, [(0, 0, 60, 90, (9, 9, 9, 255), 40, 0, 0),
                                           (0, 0, 60, 90, (8, 8, 8, 255), 40, 0, 0)]))
    win.motion_files[41] = ("frames", str(preview))  # SteamGridDB's small preview
    t = _open_panel(qapp, win, monkeypatch, "grid")
    a = Asset(41, "https://x/41.png", "https://x/41.webm", 600, 900, "image/png", 0, "", "a")
    t._loaded(t.slot, 0, win.current_shortcut().appid, [a])
    t.playing.sweep()
    _pump(qapp, 3000, until=lambda: 41 in win.full_files)
    t.playing.sweep()
    _pump(qapp, 3000, until=lambda: t.playing.motions[41]["src"] == ("frames", win.full_files[41]))
    seen = set()
    for _ in range(15):
        _pump(qapp, 30)
        seen.add(t.playing.frame(a).toImage().pixelColor(5, 5).name())
    assert {"#ff0000", "#00ff00", "#0000ff"} <= seen  # every frame of the full one, looping


def test_see_all_scrolls_smoothly(qapp, window, monkeypatch):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication

    from steam_artwork_finder.gui import smooth

    win = window
    f = smooth.install(qapp)  # as the app's main() does
    t = _open_panel(qapp, win, monkeypatch, "grid")
    mk = lambda i: Asset(i, f"https://x/{i}.png", f"https://x/{i}.png", 600, 900, "image/png", 0, "", "a")
    t._loaded(t.slot, 0, win.current_shortcut().appid, [mk(i) for i in range(40)])
    _pump(qapp, 50)
    bar = t.list.verticalScrollBar()
    assert bar.maximum() > 0
    vp = t.list.viewport()
    # A plain wheel click, as the user's mouse sends it on KDE (no scroll phase).
    e = QWheelEvent(QPointF(50, 50), QPointF(vp.mapToGlobal(QPoint(50, 50))), QPoint(0, 0), QPoint(0, -120),
                    Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(vp, e)
    assert bar.value() == 0  # it doesn't jump...
    values = []
    for _ in range(30):
        _pump(qapp, 15)
        values.append(bar.value())
    qapp.removeEventFilter(f)
    assert len(set(values)) > 3  # ...it glides
    assert 0 < values[-1] <= 3 * 24 * 2  # a click moves a few lines, not three rows of covers


def test_see_all_previews_only_where_the_pointer_rests(qapp, window, monkeypatch):
    from PySide6.QtGui import QCursor

    from steam_artwork_finder.gui import tray as traymod

    win = window
    t = _open_panel(qapp, win, monkeypatch, "grid")
    mk = lambda i: Asset(i, f"https://x/{i}.png", f"https://x/{i}.png", 600, 900, "image/png", 0, "", "a")
    t._loaded(t.slot, 0, win.current_shortcut().appid, [mk(i) for i in range(40)])
    _pump(qapp, 50)
    seen = []
    t.preview.connect(lambda slot, pm, asset: seen.append(asset.id if asset else None))
    vp = t.list.viewport()
    target = t.list.item(5)
    monkeypatch.setattr(traymod, "QCursor", type("C", (), {"pos": staticmethod(
        lambda: vp.mapToGlobal(t.list.visualItemRect(target).center()))}))
    for i in range(8):  # the list scrolls options under a still pointer
        t.list.itemEntered.emit(t.list.item(i))
        t.list.verticalScrollBar().setValue(i)
        _pump(qapp, 20)
    assert seen == []  # nothing previewed mid-scroll
    _pump(qapp, 400)
    assert seen == [5]  # just the one it came to rest on
