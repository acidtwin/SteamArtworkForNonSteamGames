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


class _Run:
    """Fake subprocess.run: answers by the command's first words."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        for prefix, out in self.answers.items():
            if tuple(cmd[:len(prefix)]) == prefix:
                if isinstance(out, BaseException):
                    raise out
                return steam.subprocess.CompletedProcess(cmd, 0, out, "")
        raise FileNotFoundError(cmd[0])


@pytest.fixture
def home(linux, monkeypatch, tmp_path):
    monkeypatch.setattr(steam.Path, "home", classmethod(lambda cls: tmp_path))
    comms = {}
    real_read = Path.read_text

    def read_text(self, *a, **kw):
        s = self.as_posix()
        if s.startswith("/proc/"):
            pid = int(s.split("/")[2])
            if pid not in comms:
                raise FileNotFoundError(s)
            return comms[pid] + "\n"
        return real_read(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", read_text)
    return tmp_path, comms


def _pid_file(path, pid):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n")


def test_native_steam_needs_its_exact_name(home):
    tmp, comms = home
    _pid_file(tmp / ".steam/steam.pid", 4242)
    assert not steam.steam_running()
    comms[4242] = "steam-artwork-f"  # this app's own process name
    assert not steam.steam_running()
    comms[4242] = "steam"
    assert steam.steam_running()


def test_snap_steam_pid_file(home):
    tmp, comms = home
    _pid_file(tmp / "snap/steam/common/.steam/steam.pid", 5000)
    comms[5000] = "steam"
    assert steam.steam_running()


def test_flatpak_steam_asks_flatpak(home, monkeypatch):
    tmp, comms = home
    _pid_file(tmp / ".var/app/com.valvesoftware.Steam/.steam/steam.pid", 2)
    comms[2] = "kthreadd"
    run = _Run({("flatpak", "ps"): "org.mozilla.firefox\ncom.valvesoftware.Steam\n"})
    monkeypatch.setattr(steam.subprocess, "run", run)
    assert steam.steam_running()
    run.answers[("flatpak", "ps")] = "org.mozilla.firefox\n"
    comms[2] = "steam"  # a namespace pid that happens to be a Steam here is not trusted
    assert not steam.steam_running()


def test_flatpak_steam_without_flatpak_falls_back_to_the_pid(home, monkeypatch):
    tmp, comms = home
    _pid_file(tmp / ".var/app/com.valvesoftware.Steam/.steam/steam.pid", 77)
    monkeypatch.setattr(steam.subprocess, "run", _Run({}))  # no flatpak command
    assert not steam.steam_running()
    comms[77] = "steam"
    assert steam.steam_running()


def test_inside_our_flatpak_the_host_is_asked(home, monkeypatch):
    tmp, comms = home
    monkeypatch.setattr(steam, "in_flatpak", lambda: True)
    _pid_file(tmp / ".steam/steam.pid", 9)
    comms[9] = "steam"  # a sandbox pid: must not be read locally
    run = _Run({("flatpak-spawn", "--host", "cat"): "bash\n"})
    monkeypatch.setattr(steam.subprocess, "run", run)
    assert not steam.steam_running()
    run.answers[("flatpak-spawn", "--host", "cat")] = "steam\n"
    assert steam.steam_running()
    _pid_file(tmp / ".var/app/com.valvesoftware.Steam/.steam/steam.pid", 2)
    (tmp / ".steam/steam.pid").unlink()
    run.answers[("flatpak-spawn", "--host", "flatpak", "ps")] = "com.valvesoftware.Steam\n"
    assert steam.steam_running()
    assert ["flatpak-spawn", "--host", "flatpak", "ps", "--columns=application"] in run.calls
