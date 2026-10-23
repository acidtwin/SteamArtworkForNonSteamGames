"""Find the Steam install, its users, and whether Steam is running."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

STEAMID64_BASE = 76561197960265728


@dataclass
class SteamUser:
    account_id: str  # folder name under userdata/
    persona: str
    most_recent: bool
    userdata: Path

    @property
    def config_dir(self) -> Path:
        return self.userdata / "config"

    @property
    def shortcuts_path(self) -> Path:
        return self.config_dir / "shortcuts.vdf"

    @property
    def grid_dir(self) -> Path:
        return self.config_dir / "grid"

    @property
    def label(self) -> str:
        return self.persona or f"Steam user {self.account_id}"


def candidate_roots() -> list[Path]:
    home = Path.home()
    if sys.platform == "win32":
        roots = []
        reg = _windows_registry_path()
        if reg:
            roots.append(Path(reg))
        for env in ("PROGRAMFILES(X86)", "PROGRAMFILES"):
            base = os.environ.get(env)
            if base:
                roots.append(Path(base) / "Steam")
        return roots
    if sys.platform == "darwin":
        return [home / "Library/Application Support/Steam"]
    return _native_roots() + _flatpak_roots() + _snap_roots()


def _native_roots() -> list[Path]:
    """Steam from a distro package or Valve's own installer (also the Steam Deck)."""
    home = Path.home()
    out = [Path(os.environ["STEAM_DIR"])] if os.environ.get("STEAM_DIR") else []  # a user's own choice
    out += [home / ".steam/steam", home / ".steam/root", home / ".local/share/Steam"]
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        out.append(Path(xdg) / "Steam")
    out.append(home / ".steam/debian-installation")  # Debian and Ubuntu's steam-installer
    return out


def _flatpak_roots() -> list[Path]:
    home = Path.home()
    return [home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
            home / ".var/app/com.valvesoftware.Steam/.steam/steam"]


def _snap_roots() -> list[Path]:
    home = Path.home()
    return [home / "snap/steam/common/.local/share/Steam",
            home / ".snap/data/steam/common/.local/share/Steam"]  # snapd's hidden-folder option


@dataclass
class Place:
    """One kind of place Steam might be installed, for the Steam folder pop-over's search."""

    name: str  # "Steam (Flatpak)"
    short: str  # "Flatpak", under its progress bar
    candidates: list[Path]


def _drive_candidates() -> list[Path]:
    """A "Steam" folder at the top of each mounted drive."""
    if sys.platform == "win32":
        import string

        out = []
        for letter in string.ascii_uppercase[3:]:  # D: onwards; C: is covered by Program Files
            drive = Path(f"{letter}:/")
            if drive.exists():
                out += [drive / "Steam", drive / "Program Files (x86)/Steam", drive / "Program Files/Steam"]
        return out
    user = os.environ.get("USER", "")
    bases = [Path("/Volumes")] if sys.platform == "darwin" else \
        [Path("/run/media") / user, Path("/media") / user, Path("/media"), Path("/mnt")]
    out = []
    for base in bases:
        try:
            mounts = [d for d in base.iterdir() if d.is_dir()]
        except OSError:
            continue
        for d in mounts:
            out += [d / "Steam", d / "steam", d / ".local/share/Steam"]
    return out


def search_places() -> list[Place]:
    """Where the pop-over looks, in order. The first three match candidate_roots()."""
    if sys.platform != "linux" and not sys.platform.startswith("freebsd"):
        return [Place("Steam", "Steam", candidate_roots()),
                Place("Other drives", "Drives", _drive_candidates())]
    return [
        Place("Steam", "Steam", _native_roots()),
        Place("Steam (Flatpak)", "Flatpak", _flatpak_roots()),
        Place("Steam (Snap)", "Snap", _snap_roots()),
        Place("Other drives", "Drives", _drive_candidates()),
    ]


def _windows_registry_path() -> str | None:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            return winreg.QueryValueEx(k, "SteamPath")[0]
    except OSError:
        return None


def find_steam_roots(candidates: list[Path] | None = None) -> list[Path]:
    """Every distinct Steam install that has user data, best guess first."""
    found: list[Path] = []
    seen: set[Path] = set()
    for c in candidates if candidates is not None else candidate_roots():
        try:
            real = c.resolve()
        except OSError:
            continue
        if real in seen or not (real / "userdata").is_dir():
            continue
        seen.add(real)
        found.append(real)
    return found


def _parse_text_vdf(text: str) -> dict:
    """Minimal parser for Valve's text KeyValues (loginusers.vdf)."""
    tokens: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c == "/" and text.startswith("//", i):
            i = text.find("\n", i)
            i = n if i < 0 else i
        elif c in "{}":
            tokens.append(c)
            i += 1
        elif c == '"':
            j = i + 1
            buf = []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    j += 1
                buf.append(text[j])
                j += 1
            tokens.append("".join(buf))
            i = j + 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '{}"':
                j += 1
            tokens.append(text[i:j])
            i = j
    root: dict = {}
    stack = [root]
    key = None
    for t in tokens:
        if t == "{":
            child: dict = {}
            stack[-1][key] = child
            stack.append(child)
            key = None
        elif t == "}":
            if len(stack) > 1:
                stack.pop()
        elif key is None:
            key = t
        else:
            stack[-1][key] = t
            key = None
    return root


def list_users(root: Path) -> list[SteamUser]:
    login = {}
    p = root / "config" / "loginusers.vdf"
    if p.exists():
        try:
            data = _parse_text_vdf(p.read_text(encoding="utf-8", errors="replace"))
            for sid, info in data.get("users", {}).items():
                if sid.isdigit() and isinstance(info, dict):
                    login[str(int(sid) - STEAMID64_BASE)] = info
        except Exception:
            login = {}
    users = []
    for d in sorted((root / "userdata").iterdir()):
        if not d.is_dir() or not d.name.isdigit() or d.name == "0":
            continue
        info = login.get(d.name, {})
        recent = str(info.get("MostRecent", info.get("mostrecent", "0"))) == "1"
        users.append(SteamUser(d.name, info.get("PersonaName", ""), recent, d))

    def rank(u: SteamUser):
        try:
            mtime = u.shortcuts_path.stat().st_mtime
        except OSError:
            mtime = 0
        return (not u.most_recent, -mtime)

    return sorted(users, key=rank)


FLATPAK_STEAM = "com.valvesoftware.Steam"
STEAM_COMMS = ("steam", "steam.exe")  # /proc/<pid>/comm of the Steam client itself


def steam_running(root: Path | None = None) -> bool:
    """Whether a Steam client is running. Can block for a few seconds inside a
    Flatpak (it asks the host), so the window calls it from a worker each tick."""
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam\ActiveProcess"
            ) as k:
                pid = int(winreg.QueryValueEx(k, "pid")[0])
        except (OSError, ValueError):
            return False
        # Steam clears the pid when it quits, but not when it crashes or the PC loses
        # power: check the process is still there and is Steam.
        return pid != 0 and _win_pid_is_steam(pid)
    home = Path.home()
    pid_files = [home / ".steam/steam.pid",
                 home / "snap/steam/common/.steam/steam.pid"]  # Snap: Steam's $HOME is ~/snap/steam/common
    for pf in pid_files:
        try:
            pid = int(pf.read_text().strip())
        except (OSError, ValueError):
            continue
        if _pid_is_steam(pid):
            return True
    flatpak_pid = home / f".var/app/{FLATPAK_STEAM}/.steam/steam.pid"
    if flatpak_pid.exists():
        # Flatpak Steam's pid is from its own PID namespace, so it means nothing
        # here: ask Flatpak instead, and only trust the pid if Flatpak can't say.
        running = _flatpak_steam_running()
        if running is not None:
            return running
        try:
            return _pid_is_steam(int(flatpak_pid.read_text().strip()))
        except (OSError, ValueError):
            return False
    return False


def in_flatpak() -> bool:
    return Path("/.flatpak-info").exists()


def _host(cmd: list[str]) -> list[str]:
    return ["flatpak-spawn", "--host", *cmd] if in_flatpak() else cmd


def _flatpak_steam_running() -> bool | None:
    """Whether Flatpak lists Steam as running; None if `flatpak ps` couldn't be asked."""
    try:
        r = subprocess.run(_host(["flatpak", "ps", "--columns=application"]),
                           capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return any(line.strip() == FLATPAK_STEAM for line in r.stdout.splitlines())


def _is_steam_comm(comm: str) -> bool:
    # An exact name: "steam" in comm would also match this app ("steam-artwork-f").
    return comm.strip().lower() in STEAM_COMMS


def _win_process_name(pid: int) -> str | None:
    """The .exe file name of a running Windows process; None if it isn't running or can't be asked."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    PROCESS_QUERY_LIMITED_INFORMATION, STILL_ACTIVE = 0x1000, 259
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != STILL_ACTIVE:
            return None
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""  # running, name unknown
        return Path(buf.value).name
    finally:
        kernel32.CloseHandle(handle)


def _win_pid_is_steam(pid: int) -> bool:
    try:
        name = _win_process_name(pid)
    except (OSError, AttributeError):
        return True  # can't ask: trust Steam's own record
    if name is None:
        return False
    return name == "" or _is_steam_comm(name)


def _pid_is_steam(pid: int) -> bool:
    if in_flatpak():
        # Host processes are invisible inside the sandbox, and a pid seen here
        # belongs to the sandbox's own namespace: ask the host, if allowed.
        try:
            r = subprocess.run(
                ["flatpak-spawn", "--host", "cat", f"/proc/{pid}/comm"],
                capture_output=True, text=True, timeout=3,
            )
            return r.returncode == 0 and _is_steam_comm(r.stdout)
        except (OSError, subprocess.SubprocessError):
            return False
    try:
        return _is_steam_comm(Path(f"/proc/{pid}/comm").read_text())
    except OSError:
        return False


def avatar_path(root: Path, account_id: str) -> Path | None:
    """Steam's cached avatar for a user, if it has one."""
    try:
        sid = int(account_id) + STEAMID64_BASE
    except (TypeError, ValueError):
        return None
    p = Path(root) / "config" / "avatarcache" / f"{sid}.png"
    return p if p.is_file() else None


def _windows_steam_exe() -> str | None:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            return winreg.QueryValueEx(k, "SteamExe")[0]
    except OSError:
        return None


def _steam_command(root: Path | None, *args: str) -> list[str]:
    """The command line that runs the Steam client for `root` with `args`."""
    if sys.platform == "win32":
        exe = _windows_steam_exe()
        if not exe and root and (Path(root) / "steam.exe").exists():
            exe = str(Path(root) / "steam.exe")
        return [exe or "steam.exe", *args]
    if sys.platform == "darwin":
        if "-shutdown" in args:
            return ["osascript", "-e", 'quit app "Steam"']
        return ["open", "-a", "Steam", *(["--args", *args] if args else [])]
    flatpak = root is not None and ".var/app/com.valvesoftware.Steam" in Path(root).as_posix()
    cmd = ["flatpak", "run", "com.valvesoftware.Steam"] if flatpak else ["steam"]
    if in_flatpak():
        cmd = ["flatpak-spawn", "--host", *cmd]
    return [*cmd, *args]


def _spawn(cmd: list[str]) -> bool:
    if sys.platform == "win32":
        # Its own process group and no console, so Steam outlives this app and no window flashes.
        detach = dict(creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        detach = dict(start_new_session=True)
    try:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, **detach)
        return True
    except OSError:
        return False


def shutdown_steam(root: Path | None = None) -> bool:
    """Ask Steam to quit. True if the request was sent (not that Steam has closed yet)."""
    return _spawn(_steam_command(root, "-shutdown"))


def start_steam(root: Path | None = None) -> bool:
    """Start Steam in the background. True if it was launched."""
    return _spawn(_steam_command(root))


def wait_until_closed(root: Path | None = None, timeout: float = 40.0, poll: float = 0.5) -> bool:
    """Block until Steam is no longer running; False if it is still running after `timeout`."""
    end = time.monotonic() + timeout
    while steam_running(root):
        if time.monotonic() >= end:
            return False
        time.sleep(poll)
    return True
