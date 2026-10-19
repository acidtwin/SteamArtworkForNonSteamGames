"""Artwork picked but not yet written to Steam: the changes "Apply" saves."""

from __future__ import annotations

from dataclasses import dataclass

from .sgdb import Asset


@dataclass
class Pick:
    """One waiting change to one piece of a game's artwork.

    `asset` None means "clear this piece". `data` is the downloaded file once
    it has been fetched (Get Artwork fetches it straight away; a pick made by
    clicking an option is downloaded when it is applied).
    """

    asset: Asset | None
    data: bytes | None = None

    @property
    def clears(self) -> bool:
        return self.asset is None


class Pending:
    """appid -> {slot key -> Pick}."""

    def __init__(self):
        self.by_game: dict[int, dict[str, Pick]] = {}

    def get(self, appid: int, slot_key: str) -> Pick | None:
        return self.by_game.get(appid, {}).get(slot_key)

    def set(self, appid: int, slot_key: str, pick: Pick):
        self.by_game.setdefault(appid, {})[slot_key] = pick

    def drop(self, appid: int, slot_key: str | None = None):
        if slot_key is None:
            self.by_game.pop(appid, None)
            return
        picks = self.by_game.get(appid)
        if picks is not None:
            picks.pop(slot_key, None)
            if not picks:
                del self.by_game[appid]

    def for_game(self, appid: int) -> dict[str, Pick]:
        return dict(self.by_game.get(appid, {}))

    def count(self, appid: int) -> int:
        return len(self.by_game.get(appid, {}))

    def games(self) -> list[int]:
        return [a for a, picks in self.by_game.items() if picks]

    def clear(self):
        self.by_game.clear()

    def __bool__(self) -> bool:
        return any(self.by_game.values())
