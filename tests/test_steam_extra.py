from pathlib import Path

import pytest

from steam_artwork_finder import steam
from steam_artwork_finder.sgdb import SteamGridDB
from steam_artwork_finder.steam import STEAMID64_BASE, _steam_command, avatar_path

from test_sgdb import FakeResp, FakeSession


def test_avatar_path(tmp_path):
    cache = tmp_path / "config" / "avatarcache"
    cache.mkdir(parents=True)
    assert avatar_path(tmp_path, "12345678") is None
    png = cache / f"{12345678 + STEAMID64_BASE}.png"
    png.write_bytes(b"x")
    assert avatar_path(tmp_path, "12345678") == png
    assert avatar_path(tmp_path, "not-a-number") is None


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(steam.sys, "platform", "linux")
    monkeypatch.setattr(steam, "in_flatpak", lambda: False)


def test_command_native(linux):
    assert _steam_command(Path("/home/d/.local/share/Steam"), "-shutdown") == ["steam", "-shutdown"]
    assert _steam_command(Path("/home/d/.local/share/Steam")) == ["steam"]


def test_command_flatpak_steam(linux):
    root = Path("/home/d/.var/app/com.valvesoftware.Steam/.local/share/Steam")
    assert _steam_command(root, "-shutdown") == ["flatpak", "run", "com.valvesoftware.Steam", "-shutdown"]


def test_command_from_inside_our_flatpak(linux, monkeypatch):
    monkeypatch.setattr(steam, "in_flatpak", lambda: True)
    assert _steam_command(Path("/home/d/.steam/steam")) == ["flatpak-spawn", "--host", "steam"]


def test_command_macos(monkeypatch):
    monkeypatch.setattr(steam.sys, "platform", "darwin")
    assert _steam_command(None) == ["open", "-a", "Steam"]
    assert _steam_command(None, "-shutdown")[0] == "osascript"


def test_command_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(steam.sys, "platform", "win32")
    monkeypatch.setattr(steam, "_windows_steam_exe", lambda: None)
    assert _steam_command(tmp_path, "-shutdown") == ["steam.exe", "-shutdown"]
    (tmp_path / "steam.exe").write_bytes(b"")
    assert _steam_command(tmp_path)[0] == str(tmp_path / "steam.exe")


def test_start_and_shutdown_never_launch(linux, monkeypatch):
    seen = []

    def fake_popen(cmd, **kw):
        seen.append((cmd, kw))
        return object()

    monkeypatch.setattr(steam.subprocess, "Popen", fake_popen)
    assert steam.shutdown_steam(Path("/x")) is True
    assert steam.start_steam(Path("/x")) is True
    assert seen[0][0] == ["steam", "-shutdown"] and seen[1][0] == ["steam"]
    assert seen[1][1]["start_new_session"] is True

    def broken(cmd, **kw):
        raise OSError("no steam")

    monkeypatch.setattr(steam.subprocess, "Popen", broken)
    assert steam.start_steam(Path("/x")) is False


def test_wait_until_closed(monkeypatch):
    states = iter([True, True, False])
    monkeypatch.setattr(steam, "steam_running", lambda root=None: next(states))
    monkeypatch.setattr(steam.time, "sleep", lambda s: None)
    assert steam.wait_until_closed(None, timeout=10, poll=0) is True
    monkeypatch.setattr(steam, "steam_running", lambda root=None: True)
    assert steam.wait_until_closed(None, timeout=0, poll=0) is False


def test_match_counts_results():
    s = FakeSession({"/search/autocomplete/": FakeResp(200, {"success": True, "data": [
        {"id": 1, "name": "Hollow Knight: Silksong"},
        {"id": 2, "name": "Hollow Knight Remix"},
        {"id": 3, "name": "Hollow"},
    ]})})
    db = SteamGridDB("k", s)
    g, n = db.match("Hollow Knight")
    assert (g.id, n) == (1, 3)
    assert db.best_match("Hollow Knight").id == 1


def test_match_nothing_found():
    s = FakeSession({"/search/autocomplete/": FakeResp(200, {"success": True, "data": []})})
    assert SteamGridDB("k", s).match("Nope") == (None, 0)
