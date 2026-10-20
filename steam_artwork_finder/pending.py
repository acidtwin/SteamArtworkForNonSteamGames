"""Artwork picked but not yet written to Steam: the changes "Apply" saves."""

from __future__ import annotations

from dataclasses import dataclass

from .sgdb import Asset


@dataclass
class LocalFile(Asset):
    """A picture from the user's own files, picked in place of a SteamGridDB option.

    `path` is where it came from (empty for a pasted picture); `moving` says
    it's an animated PNG or WebP that Steam will play.
    """

    path: str = ""
    moving: bool = False

    @property
    def animated(self) -> bool:
        return self.moving


@dataclass
class Pick:
    """One waiting change to one piece of a game's artwork.

    `asset` None means "clear this piece". `data` is the downloaded file once
    it has been fetched (Get Artwork fetches it straight away, into `file`; a pick
    made by clicking an option is downloaded when it is applied).
    """

    asset: Asset | None
    data: bytes | None = None
    file: str | None = None  # the download kept on disk instead of in memory (Get Artwork)

    @property
    def clears(self) -> bool:
        return self.asset is None

    @property
    def fetched(self) -> bool:
        return self.data is not None or self.file is not None

    def content(self) -> bytes | None:
        """The downloaded file's bytes, from memory or from its spool file."""
        if self.data is not None:
            return self.data
        if self.file is not None:
            with open(self.file, "rb") as f:
                return f.read()
        return None


class Pending:
    """appid -> {slot key -> Pick}, and appid -> new title for games being renamed."""

    def __init__(self):
        self.by_game: dict[int, dict[str, Pick]] = {}
        self.names: dict[int, str] = {}

    def name(self, appid: int) -> str | None:
        return self.names.get(appid)

    def set_name(self, appid: int, name: str | None):
        if name is None:
            self.names.pop(appid, None)
        else:
            self.names[appid] = name

    def get(self, appid: int, slot_key: str) -> Pick | None:
        return self.by_game.get(appid, {}).get(slot_key)

    def set(self, appid: int, slot_key: str, pick: Pick):
        self.by_game.setdefault(appid, {})[slot_key] = pick

    def drop(self, appid: int, slot_key: str | None = None):
        if slot_key is None:
            self.by_game.pop(appid, None)
            self.names.pop(appid, None)
            return
        picks = self.by_game.get(appid)
        if picks is not None:
            picks.pop(slot_key, None)
            if not picks:
                del self.by_game[appid]

    def for_game(self, appid: int) -> dict[str, Pick]:
        return dict(self.by_game.get(appid, {}))

    def count(self, appid: int) -> int:
        """Waiting changes for one game: its picks, and its new title."""
        return len(self.by_game.get(appid, {})) + (appid in self.names)

    def games(self) -> list[int]:
        out = [a for a, picks in self.by_game.items() if picks]
        return out + [a for a in self.names if a not in out]

    def clear(self):
        self.by_game.clear()
        self.names.clear()

    def __bool__(self) -> bool:
        return any(self.by_game.values()) or bool(self.names)
