"""Settings stored in the user's config folder."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import APP_ID


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_ID


def cache_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / APP_ID


class Config:
    def __init__(self, path: Path | None = None):
        self.path = path or config_dir() / "config.json"
        self.data: dict = {}
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.data = {}

    @property
    def api_key(self) -> str:
        return self.data.get("api_key", "")

    @api_key.setter
    def api_key(self, value: str):
        self.data["api_key"] = value.strip()
        self.save()

    def get_match(self, appid: int) -> dict | None:
        return self.data.get("matches", {}).get(str(appid))

    def set_match(self, appid: int, game_id: int, name: str, year: str = ""):
        self.data.setdefault("matches", {})[str(appid)] = {"id": game_id, "name": name, "year": year}
        self.save()

    def get_steam_match(self, appid: int) -> dict | None:
        """The Steam store game picked for a shortcut's official artwork: {id, name}, id 0 for
        "not on Steam"; None when the user hasn't picked one."""
        m = self.data.get("steam_matches", {}).get(str(appid))
        return m if isinstance(m, dict) else None

    def set_steam_match(self, appid: int, store_appid: int, name: str = ""):
        self.data.setdefault("steam_matches", {})[str(appid)] = {"id": store_appid, "name": name}
        self.save()

    def pending_icons(self, shortcuts_path) -> dict:
        """appid -> icon path (or "" to clear) waiting until Steam closes, for
        the account whose shortcuts.vdf is `shortcuts_path`."""
        queues = self.data.get("pending_icons")
        if not isinstance(queues, dict):
            return {}
        queue = queues.get(str(shortcuts_path))
        return dict(queue) if isinstance(queue, dict) else {}

    def set_pending_icon(self, shortcuts_path, appid: int, path: str | None):
        queues = self.data.get("pending_icons")
        if not isinstance(queues, dict):
            queues = self.data["pending_icons"] = {}
        for k in [k for k, v in queues.items() if not isinstance(v, dict)]:
            del queues[k]  # the old format, not tied to an account
        queue = queues.setdefault(str(shortcuts_path), {})
        if path is None:
            queue.pop(str(appid), None)
            if not queue:
                del queues[str(shortcuts_path)]
        else:
            queue[str(appid)] = path
        self.save()

    def get_applied(self, appid: int, slot: str) -> dict | None:
        return self.data.get("applied", {}).get(str(appid), {}).get(slot)

    def set_applied(self, appid: int, slot: str, info: dict | None):
        per_game = self.data.setdefault("applied", {}).setdefault(str(appid), {})
        if info is None:
            per_game.pop(slot, None)
        else:
            per_game[slot] = info
        self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        # Created private (it holds the API key) and synced, so a power cut
        # never leaves an empty config.json behind.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(self.data, indent=2))
                f.flush()
                os.fsync(f.fileno())
            if sys.platform != "win32":
                os.chmod(tmp, 0o600)  # an older .tmp left behind may have other bits
            os.replace(tmp, self.path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
