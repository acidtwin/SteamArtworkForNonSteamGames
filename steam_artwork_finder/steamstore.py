"""Steam's own store artwork, for shortcuts to games that are also sold on Steam.

Only public, key-free addresses are used: the store's search, the store's item
details (for each game's artwork file names, which newer games keep under hashed
paths) and the image CDN. Lookups run on the user's machine, only when they open
a game or ask for artwork, one at a time with a pause between them. Nothing is
stored beyond the user's own cache and the artwork they choose to apply.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass

import requests

from . import __version__
from .sgdb import Asset, name_variants, retrying_session

SEARCH = "https://store.steampowered.com/api/storesearch/"
ITEMS = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/"
CDN = "https://shared.akamai.steamstatic.com/store_item_assets/"
AGENT = (f"steam-artwork-finder/{__version__} "
         "(+https://github.com/acidtwin/SteamArtworkForNonSteamGames)")
GAP = 1.0  # seconds between calls to the store, so a long Get Artwork stays gentle

# Each piece: (the store's name for it, the plain file name older games use, its size, a smaller one to preview)
PIECES = {
    "grid": ("library_capsule_2x", "library_600x900_2x.jpg", (1200, 1800), "library_capsule"),
    "wide": ("header", "header.jpg", (920, 430), ""),
    "hero": ("library_hero", "library_hero.jpg", (1920, 620), ""),
    "logo": ("library_logo", "logo.png", (1280, 720), ""),
}
FALLBACK_SMALL = {"library_capsule": "library_600x900.jpg"}
ORDER = ("grid", "wide", "hero", "logo", "icon")


class SteamStoreError(Exception):
    pass


@dataclass
class StoreGame:
    appid: int
    name: str


@dataclass
class SteamAsset(Asset):
    """Official artwork from the game's Steam store page."""

    appid: int = 0

    @property
    def animated(self) -> bool:
        return False


def norm_name(name: str) -> str:
    """Names compared loosely: case, symbols and spacing don't count."""
    return re.sub(r"[^0-9a-z]+", "", name.casefold())


def _mime(url: str) -> str:
    path = url.split("?")[0].lower()
    return "image/png" if path.endswith(".png") else "image/jpeg"


class SteamStore:
    def __init__(self, session: requests.Session | None = None):
        self._session = session  # a fixed session (tests); otherwise one per thread
        self._local = threading.local()
        self._lock = threading.Lock()
        self._last = 0.0
        self.sleep = time.sleep  # swapped out by tests
        self.gap = GAP
        self._assets: dict[int, dict[str, SteamAsset]] = {}
        self._searches: dict[str, list[StoreGame]] = {}

    @property
    def http(self):
        if self._session is not None:
            return self._session
        s = getattr(self._local, "session", None)
        if s is None:
            s = self._local.session = retrying_session()
            s.headers["User-Agent"] = AGENT
        return s

    def _call(self, url: str, params: dict) -> dict:
        """One request to the store, never sooner than `gap` after the last."""
        with self._lock:
            for attempt in range(3):
                wait = self._last + self.gap - time.monotonic()
                if wait > 0:
                    self.sleep(wait)
                try:
                    r = self.http.get(url, params=params, timeout=20)
                except requests.RequestException as e:
                    raise SteamStoreError(f"Could not reach the Steam store: {e}") from e
                finally:
                    self._last = time.monotonic()
                if r.status_code != 429 or attempt == 2:
                    break
                # Busy: back off as asked (capped), holding the lock so every thread waits.
                try:
                    pause = float(r.headers.get("Retry-After", 30))
                except ValueError:
                    pause = 30.0
                pause = 30.0 if pause != pause else max(1.0, min(pause, 120.0))
                self.sleep(pause)
        if r.status_code == 429:
            raise SteamStoreError("The Steam store is busy; try again in a minute")
        if not r.ok:
            raise SteamStoreError(f"The Steam store returned HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError:
            raise SteamStoreError("The Steam store sent something unexpected")

    def _exists(self, url: str) -> bool | None:
        """True if the file is there, False if the CDN says it isn't, None if we couldn't tell."""
        try:
            r = self.http.head(url, timeout=20, allow_redirects=True)
        except requests.RequestException:
            return None
        if r.status_code == 200:
            return True
        return False if r.status_code in (403, 404, 410) else None

    # -- finding the game ------------------------------------------------------

    def search(self, term: str) -> list[StoreGame]:
        term = term.strip()
        if not term:
            return []
        key = term.casefold()
        if key not in self._searches:
            body = self._call(SEARCH, {"term": term, "l": "english", "cc": "US"})
            self._searches[key] = [StoreGame(int(i["id"]), i.get("name", ""))
                                   for i in body.get("items") or [] if i.get("type", "app") == "app" and i.get("id")]
        return list(self._searches[key])

    def reachable(self) -> bool:
        """Ask the store something small, uncached; raises SteamStoreError if it can't be reached."""
        self._call(SEARCH, {"term": "portal", "l": "english", "cc": "US"})
        return True

    def match(self, name: str) -> StoreGame | None:
        """The store game with the same name, or None. A near name isn't taken: a wrong game's
        official art looks right and isn't, so anything less than a match waits for the user."""
        for term in name_variants(name):
            want = norm_name(term)
            if not want:
                continue
            for g in self.search(term):
                if norm_name(g.name) == want:
                    return g
        return None

    # -- its artwork -------------------------------------------------------------

    def _item(self, appid: int) -> dict:
        req = {"ids": [{"appid": appid}], "context": {"language": "english", "country_code": "US"},
               "data_request": {"include_assets": True}}
        body = self._call(ITEMS, {"input_json": json.dumps(req)})
        items = (body.get("response") or {}).get("store_items") or []
        return items[0] if items else {}

    def assets(self, appid: int) -> dict[str, SteamAsset]:
        """slot key -> the game's official piece, for the pieces it has. Never the icon: the store's
        is only 32 × 32, blurry wherever Steam shows it, so SteamGridDB's or the game's own wins."""
        if appid in self._assets:
            return dict(self._assets[appid])
        try:
            item, ok = self._item(appid), True
        except SteamStoreError:
            item, ok = {}, False
        listed = item.get("assets") or {}
        fmt = listed.get("asset_url_format") or f"steam/apps/{appid}/${{FILENAME}}"

        def url_for(name: str) -> str:
            return CDN + fmt.replace("${FILENAME}", name)

        out: dict[str, SteamAsset] = {}
        unsure = False
        for i, key in enumerate(ORDER):
            if key == "icon":
                continue
            field, plain, (w, h), small = PIECES[key]
            if listed.get(field):
                url = url_for(listed[field])
            else:
                # Not listed (or the details didn't load): the plain name older games use, if it's there.
                url = url_for(plain)
                there = self._exists(url)
                if there is None:
                    unsure = True  # a network blip isn't "no such piece": ask again next time
                if not there:
                    continue
            thumb = url
            if small:
                thumb = url_for(listed[small]) if listed.get(small) else url_for(FALLBACK_SMALL[small])
            out[key] = SteamAsset(id=-(appid * 10 + i + 1), url=url, thumb=thumb, width=w, height=h,
                                  mime=_mime(url), score=0, style="official", author="Steam", appid=appid)
        if not ok and not out:
            # Couldn't ask the store (offline, busy): don't remember "no official art".
            raise SteamStoreError("Couldn't load the game's Steam store details")
        if ok and not unsure:
            self._assets[appid] = out
        return dict(out)
