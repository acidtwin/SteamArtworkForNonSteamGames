import struct
import zlib

import pytest

from steam_artwork_finder.artwork import SLOTS, is_animated, save_asset
from steam_artwork_finder.sgdb import Asset, SteamGridDB
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
