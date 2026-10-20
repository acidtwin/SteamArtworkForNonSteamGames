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

    @property
    def animated(self) -> bool:
        """SteamGridDB gives animated options a video thumbnail."""
        return self.thumb.lower().split("?")[0].endswith((".webm", ".mp4"))


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


_FILEISH = re.compile(r"\.(exe|bat|cmd|com|lnk|sh|x86_64|x86|x64|appimage|bin|elf|jar|py)$", re.I)
_PLAIN_DIRS = {
    "", "bin", "bin32", "bin64", "x64", "x86", "x86_64", "win32", "win64", "binaries", "build", "release",
    "linux", "windows", "system", "app", "data", "game", "games", "usr", "opt", "local", "share", "home",
    "mnt", "media", "run", "steamapps", "common", "drive_c", "program files", "program files (x86)",
}


def looks_like_file(name: str) -> bool:
    """True for shortcut names that are really file names, like "SB.exe"."""
    return bool(_FILEISH.search(name.strip()))


# Titles that say nothing about which game it is.
_GENERIC = {
    "launcher", "game launcher", "launch", "game", "start", "start game", "play", "play game", "run", "setup",
    "main", "client", "app", "application", "program", "bootstrap", "loader", "unity", "godot", "shipping",
}


def _program(exe: str) -> str:
    """The program's own name, without its folder or file ending ("game" for ".../game.exe")."""
    base = re.split(r"[\\/]+", exe.strip().strip('"'))[-1]
    return _FILEISH.sub("", base)


def generic_name(name: str, exe: str = "") -> bool:
    """True for a shortcut title that doesn't name the game: a file name ("game.exe"), a word
    like "Launcher", or the program's own name when its folder says something else
    ("hollow_knight" in Games/Hollow Knight/)."""
    name = name.strip()
    if not name:
        return False
    if looks_like_file(name) or _tidy(name).casefold() in _GENERIC:
        return True
    folder = _folder(exe)
    return bool(folder) and name.casefold() == _program(exe).casefold() != folder.casefold()


def search_name(name: str, exe: str = "") -> str:
    """What to look a shortcut up by: its title, or for a generic title the folder the
    program is in (else the file name without its ending)."""
    if not generic_name(name, exe):
        return name
    return _folder(exe) or (_tidy(_FILEISH.sub("", name.strip())) if looks_like_file(name) else "") or name


def _tidy(word: str) -> str:
    return clean_name(word.replace("_", " "))


def _folder(exe: str) -> str:
    """The first meaningful folder above the program, skipping bin/x64/Binaries and the like."""
    exe = exe.strip().strip('"')
    if not exe:
        return ""
    parts = re.split(r"[\\/]+", exe)[:-1]
    for i in range(len(parts) - 1, max(-1, len(parts) - 5), -1):
        part = parts[i]
        if part.casefold() in _PLAIN_DIRS or part.endswith(":"):
            continue
        if i > 0 and parts[i - 1].casefold() in ("home", "users"):  # a user name
            return ""
        return _tidy(part)
    return ""


def suggestions(name: str, exe: str = "") -> list[tuple[str, str]]:
    """Search terms worth trying for a shortcut, best first, as (term, where it came from).

    A name like "SB.exe" or "Launcher" says little, so the folder the program sits
    in comes first, then the file name without its extension (or the name itself).
    """
    out: list[tuple[str, str]] = []

    def add(term: str, source: str):
        if term and term.casefold() not in {t.casefold() for t, _ in out}:
            out.append((term, source))

    folder = _folder(exe)
    if looks_like_file(name):
        add(folder, "folder")
        add(_tidy(_FILEISH.sub("", name.strip())), "file")
    elif generic_name(name, exe):
        add(folder, "folder")
        add(name_variants(name)[-1] if name_variants(name) else "", "name")
    else:
        add(name_variants(name)[-1] if name_variants(name) else "", "name")
        add(folder, "folder")
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
        """The exact-name result if there is one, else the top result."""
        return self.match(name)[0]

    def match(self, name: str) -> tuple[Game | None, int]:
        """The best match for `name` and how many results its search term returned.

        Launcher tags ("- GOG", "(Ryujinx)") are stripped step by step until
        something is found, so shortcuts named by other tools still match.
        """
        first: tuple[Game | None, int] = (None, 0)
        for term in name_variants(name):
            results = self.search(term)
            if not results:
                continue
            want = term.casefold()
            for g in results:
                if clean_name(g.name).casefold() == want:
                    return g, len(results)
            if first[0] is None:
                first = (results[0], len(results))
        return first

    def assets(self, kind: str, game_id: int, page: int = 0, animated: bool = False) -> list[Asset]:
        """One page of options; `animated` mixes in animated ones where Steam plays them."""
        from .artwork import SLOTS

        slot = SLOTS[kind]
        params = slot.search(animated)
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
