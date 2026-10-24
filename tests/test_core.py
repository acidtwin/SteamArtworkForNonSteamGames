import sys
from pathlib import Path

import pytest

from steam_artwork_finder import binvdf
from steam_artwork_finder.artwork import SLOTS, current_icon, existing, save_asset
from steam_artwork_finder.shortcuts import ShortcutsFile, legacy_appid
from steam_artwork_finder.steam import find_steam_roots, list_users

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def test_round_trip_is_byte_exact(sample_bytes):
    assert binvdf.dumps(binvdf.loads(sample_bytes)) == sample_bytes


def test_round_trip_with_float_uint64_and_bad_utf8():
    raw = (
        b"\x00shortcuts\x00\x00" b"0\x00"
        b"\x01AppName\x00caf\xe9\x00"  # latin-1 byte, not valid UTF-8
        b"\x03f\x00\x00\x00\x80\x3f"
        b"\x07big\x00\xff\xff\xff\xff\xff\xff\xff\xff"
        b"\x08\x08\x08"
    )
    assert binvdf.dumps(binvdf.loads(raw)) == raw


def test_truncated_file_is_rejected(sample_bytes):
    with pytest.raises(binvdf.VDFError):
        binvdf.loads(sample_bytes[:-30])


def test_parse_shortcuts(steam_root):
    sf = ShortcutsFile(steam_root / "userdata/12345678/config/shortcuts.vdf")
    names = [s.name for s in sf.shortcuts]
    assert names == ["Celeste", "Hollow Knight™", "Pokémon émulé"]
    assert sf.shortcuts[0].appid == (-1734531011) & 0xFFFFFFFF
    assert sf.shortcuts[1].icon == "/old/icon.png"


def test_legacy_appid_used_when_missing():
    raw = b"\x00shortcuts\x00\x000\x00\x01AppName\x00Game\x00\x01Exe\x00\"/g\"\x00\x08\x08\x08"
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "shortcuts.vdf"
        p.write_bytes(raw)
        sc = ShortcutsFile(p).shortcuts[0]
        assert sc.appid == legacy_appid('"/g"', "Game")
        assert sc.appid & 0x80000000


def test_icon_write_backs_up_and_keeps_everything_else(steam_root, sample_bytes):
    path = steam_root / "userdata/12345678/config/shortcuts.vdf"
    sf = ShortcutsFile(path)
    grid = path.parent / "grid"
    sc = sf.shortcuts[0]
    dest = save_asset(grid, sc, SLOTS["icon"], PNG, "image/png", sf).new_file

    backups = list((path.parent / "shortcuts-backups").glob("*.vdf"))
    assert len(backups) == 1 and backups[0].read_bytes() == sample_bytes

    again = ShortcutsFile(path)
    assert again.shortcuts[0].icon == str(dest)
    for before, after in zip(binvdf.loads(sample_bytes)["shortcuts"].values(),
                             again.root["shortcuts"].values()):
        for k in before:
            if k != "icon":
                assert before[k] == after[k], k
    assert current_icon(again.shortcuts[0], grid) == dest


def test_artwork_file_names(steam_root):
    path = steam_root / "userdata/12345678/config/shortcuts.vdf"
    sf = ShortcutsFile(path)
    sc = sf.shortcuts[0]
    grid = path.parent / "grid"
    expected = {"grid": f"{sc.appid}p.png", "wide": f"{sc.appid}.png",
                "hero": f"{sc.appid}_hero.png", "logo": f"{sc.appid}_logo.png"}
    for key, name in expected.items():
        assert save_asset(grid, sc, SLOTS[key], PNG, "image/png").new_file.name == name
    jpg = save_asset(grid, sc, SLOTS["grid"], b"\xff\xd8\xff" + b"0" * 10, "").new_file
    assert jpg.name == f"{sc.appid}p.jpg"
    assert existing(grid, sc.appid, SLOTS["grid"]) == jpg
    assert not (grid / f"{sc.appid}p.png").exists()
    assert list((grid / ".replaced").iterdir())


def test_find_users(steam_root, tmp_path):
    roots = find_steam_roots([tmp_path / "nope", steam_root, steam_root])
    assert roots == [steam_root.resolve()]
    users = list_users(roots[0])
    assert [u.account_id for u in users] == ["12345678", "87654321"]
    assert users[0].persona == "Deck User" and users[0].most_recent


def test_backups_are_capped(steam_root):
    path = steam_root / "userdata/12345678/config/shortcuts.vdf"
    sf = ShortcutsFile(path)
    for _ in range(15):
        sf.backup()
    assert len(list(sf.backup_dir.glob("*.vdf"))) == 10


def test_undo_restores_previous_art_and_icon(steam_root):
    from steam_artwork_finder.artwork import remove_asset, undo

    path = steam_root / "userdata/12345678/config/shortcuts.vdf"
    sf = ShortcutsFile(path)
    sc = sf.shortcuts[1]
    grid = path.parent / "grid"
    first = save_asset(grid, sc, SLOTS["hero"], PNG, "image/png").new_file
    change = save_asset(grid, sc, SLOTS["hero"], b"\xff\xd8\xff" + b"1" * 10, "image/jpeg")
    assert existing(grid, sc.appid, SLOTS["hero"]).suffix == ".jpg"
    undo(change, sf)
    assert existing(grid, sc.appid, SLOTS["hero"]) == first and first.read_bytes() == PNG

    icon_change = save_asset(grid, sc, SLOTS["icon"], PNG, "image/png", sf)
    undo(icon_change, sf)
    assert ShortcutsFile(path).shortcuts[1].icon == "/old/icon.png"

    gone = remove_asset(grid, sc, SLOTS["hero"], sf)
    assert existing(grid, sc.appid, SLOTS["hero"]) is None
    undo(gone, sf)
    assert existing(grid, sc.appid, SLOTS["hero"]) == first


def test_restore_backup(steam_root, sample_bytes):
    path = steam_root / "userdata/12345678/config/shortcuts.vdf"
    sf = ShortcutsFile(path)
    sf.shortcuts[0].set_icon("/x.png")
    sf.save()
    (backup, count), = sf.backups()
    assert count == 3
    sf.restore(backup)
    assert path.read_bytes() == sample_bytes
    assert sf.shortcuts[0].icon == ""
    assert len(sf.backups()) == 2


def test_alt_end_marker_round_trips():
    raw = b"\x00shortcuts\x00\x000\x00\x01AppName\x00G\x00\x0b\x0b\x0b"
    assert binvdf.dumps(binvdf.loads(raw)) == raw


def test_save_refuses_when_steam_rewrote_the_file(steam_root, sample_bytes):
    from steam_artwork_finder.shortcuts import ShortcutsChanged

    path = steam_root / "userdata/12345678/config/shortcuts.vdf"
    sf = ShortcutsFile(path)
    # Steam rewrites the list with one more game while the app is open.
    import os
    from conftest import SAMPLE_ENTRIES, make_entry, make_shortcuts
    newer = make_shortcuts([make_entry(*e) for e in SAMPLE_ENTRIES] + [make_entry(-5, "New Game", '"/n"')])
    path.write_bytes(newer)
    os.utime(path, ns=(1, 1))
    sf.shortcuts[0].set_icon("/x.png")
    with pytest.raises(ShortcutsChanged):
        sf.save()
    assert path.read_bytes() == newer  # nothing was overwritten


def test_icon_change_is_redone_on_fresh_list(steam_root):
    import os
    from conftest import SAMPLE_ENTRIES, make_entry, make_shortcuts

    path = steam_root / "userdata/12345678/config/shortcuts.vdf"
    sf = ShortcutsFile(path)
    sc = sf.shortcuts[1]
    newer = make_shortcuts([make_entry(*e) for e in SAMPLE_ENTRIES] + [make_entry(-5, "New Game", '"/n"')])
    path.write_bytes(newer)
    os.utime(path, ns=(1, 1))
    change = save_asset(path.parent / "grid", sc, SLOTS["icon"], PNG, "image/png", sf)
    assert change.reloaded
    again = ShortcutsFile(path)
    assert len(again.shortcuts) == 4  # Steam's new game survived
    assert again.shortcuts[1].icon == str(change.new_file)


def _steam_rewrites(path):
    import os

    path.write_bytes(path.read_bytes())
    os.utime(path, ns=(1, 1))


def test_icons_saved_through_old_shortcuts_survive_a_reload(steam_root):
    from steam_artwork_finder.artwork import undo

    path = steam_root / "userdata/12345678/config/shortcuts.vdf"
    grid = path.parent / "grid"
    sf = ShortcutsFile(path)
    a, b = sf.shortcuts[0], sf.shortcuts[1]
    _steam_rewrites(path)  # Steam exits during Apply
    first = save_asset(grid, a, SLOTS["icon"], PNG, "image/png", sf)
    second = save_asset(grid, b, SLOTS["icon"], PNG, "image/png", sf)  # `b` is from before the reload
    assert first.reloaded and second.reloaded
    fresh = ShortcutsFile(path)
    assert fresh.shortcuts[0].icon == str(first.new_file)
    assert fresh.shortcuts[1].icon == str(second.new_file)
    undo(first, sf)
    assert ShortcutsFile(path).shortcuts[0].icon == ""


def test_restore_the_oldest_of_ten_backups(steam_root, sample_bytes):
    import os

    from steam_artwork_finder.shortcuts import MAX_BACKUPS

    path = steam_root / "userdata/12345678/config/shortcuts.vdf"
    sf = ShortcutsFile(path)
    for i in range(MAX_BACKUPS):
        b = sf.backup()
        os.utime(b, ns=(10**18 + i * 10**9,) * 2)
    oldest = sf.backups()[-1][0]
    sf.shortcuts[0].set_icon("/x.png")
    sf._backed_up = True
    sf.save()
    sf.restore(oldest)
    assert path.read_bytes() == sample_bytes
    assert oldest.exists()
    assert len(sf.backups()) == MAX_BACKUPS + 1  # ten newest, plus the one restored


def test_set_aside_art_is_kept_for_the_full_30_days(tmp_path, steam_root):
    import os
    import time

    from steam_artwork_finder.artwork import prune_replaced, undo

    sf = ShortcutsFile(steam_root / "userdata/12345678/config/shortcuts.vdf")
    sc = sf.shortcuts[0]
    grid = tmp_path / "grid"
    grid.mkdir()
    old = grid / f"{sc.appid}p.png"
    old.write_bytes(PNG + b"hand-made")
    t = time.time() - 60 * 86400
    os.utime(old, (t, t))
    change = save_asset(grid, sc, SLOTS["grid"], PNG, "image/png")
    prune_replaced(grid)
    assert all(aside.exists() for _, aside in change.moved)
    undo(change)
    assert old.read_bytes() == PNG + b"hand-made"

    # A file set aside long ago (the time is in its name) does go.
    stale = grid / ".replaced" / f"{sc.appid}p.20200101-000000-1.png"
    stale.write_bytes(PNG)
    prune_replaced(grid)
    assert not stale.exists()


def test_failed_write_keeps_the_old_art(steam_root, monkeypatch):
    from steam_artwork_finder import artwork
    from steam_artwork_finder.artwork import remove_asset

    path = steam_root / "userdata/12345678/config/shortcuts.vdf"
    grid = path.parent / "grid"
    grid.mkdir()
    sf = ShortcutsFile(path)
    sc = sf.shortcuts[1]
    cover = grid / f"{sc.appid}p.jpg"
    cover.write_bytes(b"\xff\xd8\xffold")
    icon = grid / f"{sc.appid}_icon.png"
    icon.write_bytes(PNG)

    real_write = Path.write_bytes

    def full_disk(self, data):
        if self.name.endswith(".part"):
            real_write(self, data[:1])
            raise OSError(28, "No space left on device")
        return real_write(self, data)

    monkeypatch.setattr(Path, "write_bytes", full_disk)
    with pytest.raises(OSError):
        save_asset(grid, sc, SLOTS["grid"], PNG, "image/png")
    monkeypatch.setattr(Path, "write_bytes", real_write)
    assert cover.read_bytes() == b"\xff\xd8\xffold"
    assert sorted(p.name for p in grid.iterdir() if p.is_file()) == sorted([cover.name, icon.name])

    def broken(*a):
        raise OSError("can't write shortcuts.vdf")

    monkeypatch.setattr(artwork, "set_icon", broken)
    with pytest.raises(OSError):
        save_asset(grid, sc, SLOTS["icon"], b"\x00\x00\x01\x00ico", "image/x-icon", sf)
    assert icon.read_bytes() == PNG and not (grid / f"{sc.appid}_icon.ico").exists()
    with pytest.raises(OSError):
        remove_asset(grid, sc, SLOTS["icon"], sf)
    assert icon.read_bytes() == PNG


def test_waiting_icons_belong_to_one_account(tmp_path):
    import stat

    from steam_artwork_finder.config import Config

    cfg = Config(tmp_path / "config.json")
    cfg.data["pending_icons"] = {"123": "/old/format.png"}  # from before icons were kept per account
    cfg.set_pending_icon("/a/shortcuts.vdf", 1, "/a/grid/1_icon.png")
    assert cfg.pending_icons("/a/shortcuts.vdf") == {"1": "/a/grid/1_icon.png"}
    assert cfg.pending_icons("/b/shortcuts.vdf") == {}
    assert "123" not in cfg.data["pending_icons"]
    again = Config(tmp_path / "config.json")
    assert again.pending_icons("/a/shortcuts.vdf") == {"1": "/a/grid/1_icon.png"}
    again.set_pending_icon("/a/shortcuts.vdf", 1, None)
    assert again.pending_icons("/a/shortcuts.vdf") == {}
    if sys.platform != "win32":
        assert stat.S_IMODE((tmp_path / "config.json").stat().st_mode) == 0o600
    assert not (tmp_path / "config.tmp").exists()


def test_rename_keeps_the_appid(steam_root, tmp_path):
    from conftest import make_shortcuts

    from steam_artwork_finder import artwork, binvdf
    from steam_artwork_finder.shortcuts import ShortcutsFile, legacy_appid

    # An entry with no stored appid: Steam works its id out from exe + name.
    body = b"\x01AppName\x00game.exe\x00\x01Exe\x00\"/g/Hollow Knight/game.exe\"\x00"
    path = tmp_path / "shortcuts.vdf"
    path.write_bytes(make_shortcuts([body]))
    sf = ShortcutsFile(path)
    sc = sf.shortcuts[0]
    old_id = legacy_appid(sc.exe, sc.name)
    assert sc.appid == old_id
    artwork.set_name(sf, sc, "Hollow Knight")
    again = ShortcutsFile(path).shortcuts[0]
    assert again.name == "Hollow Knight" and again.appid == old_id
    assert len(list(sf.backup_dir.glob("shortcuts-*.vdf"))) == 1
    assert binvdf.loads(path.read_bytes())  # still a sound file


def test_pending_counts_a_new_title():
    from steam_artwork_finder.pending import Pending, Pick

    p = Pending()
    p.set_name(5, "Hollow Knight")
    assert p and p.games() == [5] and p.count(5) == 1
    p.set(5, "grid", Pick(None))
    assert p.count(5) == 2 and p.games() == [5]
    p.drop(5, "grid")
    assert p.count(5) == 1
    p.drop(5)
    assert not p


def test_finds_steam_in_every_linux_install_place(tmp_path, monkeypatch):
    from steam_artwork_finder import steam

    monkeypatch.setattr(steam.sys, "platform", "linux")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Path.home() on Windows
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("STEAM_DIR", raising=False)
    homes = {
        "debian": tmp_path / ".steam/debian-installation",
        "snap-hidden": tmp_path / ".snap/data/steam/common/.local/share/Steam",
        "xdg": tmp_path / "data/Steam",
        "own": tmp_path / "MySteam",
    }
    for root in homes.values():
        (root / "userdata").mkdir(parents=True)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("STEAM_DIR", str(homes["own"]))
    roots = steam.find_steam_roots()
    assert roots[0] == homes["own"].resolve()  # STEAM_DIR is the user's own choice, so it comes first
    assert set(roots) == {r.resolve() for r in homes.values()}
    places = {pl.short: steam.find_steam_roots(pl.candidates) for pl in steam.search_places()}
    assert homes["snap-hidden"].resolve() in places["Snap"]
    assert homes["debian"].resolve() in places["Steam"]


def test_damaged_vdf_raises_vdf_error():
    import pytest
    from steam_artwork_finder import binvdf

    with pytest.raises(binvdf.VDFError):
        binvdf.loads(b"\x00shortcuts\x00\x000\x00\x02appid\x00\x01\x02")


def test_damaged_config_is_kept_aside(tmp_path):
    from steam_artwork_finder.config import Config

    p = tmp_path / "config.json"
    p.write_text("{not json")
    cfg = Config(p)
    assert cfg.data == {}
    assert list(tmp_path.glob("config.json.bad-*"))
    p.write_text("[]")
    assert Config(p).data == {}


def test_text_download_never_replaces_artwork(tmp_path):
    import pytest
    from steam_artwork_finder.artwork import SLOTS, save_asset
    from steam_artwork_finder.shortcuts import Shortcut

    sc = Shortcut("0", 123, "Game", "/bin/true", "", None)
    with pytest.raises(ValueError):
        save_asset(tmp_path / "grid", sc, SLOTS["grid"], b"<html>Sign in to Wi-Fi</html>", "image/png")
    assert not list((tmp_path / "grid").glob("123*")) if (tmp_path / "grid").exists() else True
