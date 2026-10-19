"""Find the Steam install, its users, and whether Steam is running."""

from __future__ import annotations

import os
import sys
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
    return [
        home / ".steam/steam",
        home / ".steam/root",
        home / ".local/share/Steam",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",  # Flatpak
        home / ".var/app/com.valvesoftware.Steam/.steam/steam",
        home / "snap/steam/common/.local/share/Steam",  # Snap
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


def steam_running(root: Path | None = None) -> bool:
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam\ActiveProcess"
            ) as k:
                return int(winreg.QueryValueEx(k, "pid")[0]) != 0
        except OSError:
            return False
    pid_files = [Path.home() / ".steam/steam.pid",
                 Path.home() / ".var/app/com.valvesoftware.Steam/.steam/steam.pid"]
    for pf in pid_files:
        try:
            pid = int(pf.read_text().strip())
        except (OSError, ValueError):
            continue
        if _pid_is_steam(pid):
            return True
    return False


def in_flatpak() -> bool:
    return Path("/.flatpak-info").exists()


def _pid_is_steam(pid: int) -> bool:
    try:
        return "steam" in Path(f"/proc/{pid}/comm").read_text().lower()
    except OSError:
        pass
    if in_flatpak():
        # Host processes are invisible inside the sandbox; ask the host if allowed.
        import subprocess

        try:
            r = subprocess.run(
                ["flatpak-spawn", "--host", "cat", f"/proc/{pid}/comm"],
                capture_output=True, text=True, timeout=3,
            )
            return r.returncode == 0 and "steam" in r.stdout.lower()
        except (OSError, subprocess.SubprocessError):
            return False
    return False
