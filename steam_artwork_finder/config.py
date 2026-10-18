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

    def set_match(self, appid: int, game_id: int, name: str):
        self.data.setdefault("matches", {})[str(appid)] = {"id": game_id, "name": name}
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
        tmp.write_text(json.dumps(self.data, indent=2))
        if sys.platform != "win32":
            os.chmod(tmp, 0o600)  # holds the API key
        os.replace(tmp, self.path)
