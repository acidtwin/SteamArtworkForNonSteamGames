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
