"""Small client for the SteamGridDB v2 API."""

from __future__ import annotations

import datetime
import re
import threading
import time
from dataclasses import dataclass
from urllib.parse import quote

import requests

from . import __version__

API = "https://www.steamgriddb.com/api/v2"


class SGDBError(Exception):
    pass


class AuthError(SGDBError):
    pass


@dataclass
class Game:
    id: int
    name: str
    year: str = ""
    verified: bool = False


@dataclass
class Asset:
    id: int
    url: str
    thumb: str
    width: int
    height: int
    mime: str
    score: int
    style: str
    author: str


def clean_name(name: str) -> str:
    name = re.sub(r"[™®©]", "", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip()


_SUFFIX = re.compile(r"\s*(\(.*?\)|\[.*?\]|\{.*?\})\s*$")
_LAUNCHER = re.compile(r"\s+[-–—|:]\s+(GOG|Heroic|Lutris|Bottles|Epic|EGS|Steam|Ryujinx|Yuzu|RetroArch|"
                       r"Dolphin|PCSX2|RPCS3|Cemu|Citra|Proton|Wine|Flatpak|Legendary|itch(\.io)?|"
                       r"Amazon|Ubisoft|EA|Origin|Battle\.net|Xbox|Game Pass)\b.*$", re.I)


def name_variants(name: str) -> list[str]:
    """The name as given, then with launcher tags and bracketed suffixes removed."""
    out = []
    cur = clean_name(name)
    while cur and cur not in out:
        out.append(cur)
        nxt = _LAUNCHER.sub("", cur)
        if nxt == cur:
            nxt = _SUFFIX.sub("", cur)
        cur = clean_name(nxt)
    return out


class SteamGridDB:
    def __init__(self, api_key: str, session: requests.Session | None = None):
        self.api_key = api_key
        self._session = session  # a fixed session (tests); otherwise one per thread
        self._local = threading.local()
        self.sleep = time.sleep  # swapped out by tests
        self.on_wait = None  # callback(seconds) when rate limited

    @property
    def http(self):
        if self._session is not None:
            return self._session
        s = getattr(self._local, "session", None)
        if s is None:
            # requests.Session is not thread-safe; workers each get their own.
            s = self._local.session = requests.Session()
            s.headers["User-Agent"] = f"steam-artwork-finder/{__version__}"
        return s

    def _get(self, path: str, params: dict | None = None):
        if not self.api_key:
            raise AuthError("No SteamGridDB API key set")
        for attempt in range(4):
            try:
                r = self.http.get(
                    API + path,
                    params=params,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=20,
                )
            except requests.RequestException as e:
                raise SGDBError(f"Could not reach SteamGridDB: {e}") from e
            if r.status_code != 429 or attempt == 3:
                break
            # Rate limited: wait as long as SteamGridDB asks (capped), then retry.
            try:
                wait = min(float(r.headers.get("Retry-After", 5)), 60.0)
            except ValueError:
                wait = 5.0
            if self.on_wait:
                self.on_wait(wait)
            self.sleep(wait)
        if r.status_code == 401:
            raise AuthError("SteamGridDB rejected the API key")
        if r.status_code == 404:
            return []
        if r.status_code == 429:
            raise SGDBError("SteamGridDB is rate limiting requests; try again in a minute")
        try:
            body = r.json()
        except ValueError:
            raise SGDBError(f"SteamGridDB returned HTTP {r.status_code}")
        if not r.ok or not body.get("success", False):
            errs = body.get("errors") or [f"HTTP {r.status_code}"]
            raise SGDBError("; ".join(map(str, errs)))
        return body.get("data", [])

    def check_key(self) -> bool:
        try:
            self._get("/search/autocomplete/" + quote("portal", safe=""))
            return True
        except AuthError:
            return False

    def search(self, term: str) -> list[Game]:
        term = clean_name(term)
        if not term:
            return []
        out = []
        for g in self._get("/search/autocomplete/" + quote(term, safe="")):
            year = ""
            if g.get("release_date"):
                year = str(datetime.datetime.fromtimestamp(g["release_date"], datetime.timezone.utc).year)
            out.append(Game(g["id"], g["name"], year, bool(g.get("verified"))))
        return out

    def best_match(self, name: str) -> Game | None:
        """The exact-name result if there is one, else the top result.

        Launcher tags ("- GOG", "(Ryujinx)") are stripped step by step until
        something is found, so shortcuts named by other tools still match.
        """
        first = None
        for term in name_variants(name):
            results = self.search(term)
            if not results:
                continue
            want = term.casefold()
            for g in results:
                if clean_name(g.name).casefold() == want:
                    return g
            if first is None:
                first = results[0]
        return first

    def assets(self, kind: str, game_id: int, page: int = 0) -> list[Asset]:
        from .artwork import SLOTS

        slot = SLOTS[kind]
        params = dict(slot.query)
        params.update(nsfw="false", humor="any", page=page)
        data = self._get(f"/{slot.endpoint}/game/{game_id}", params)
        out = []
        for a in data:
            out.append(
                Asset(
                    id=a["id"],
                    url=a["url"],
                    thumb=a.get("thumb") or a["url"],
                    width=a.get("width", 0),
                    height=a.get("height", 0),
                    mime=a.get("mime", ""),
                    score=a.get("score", 0),
                    style=a.get("style", ""),
                    author=(a.get("author") or {}).get("name", ""),
                )
            )
        return out

    def download(self, url: str) -> bytes:
        try:
            r = self.http.get(url, timeout=60)
            r.raise_for_status()
        except requests.RequestException as e:
            raise SGDBError(f"Download failed: {e}") from e
        return r.content
