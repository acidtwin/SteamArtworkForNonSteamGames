import pytest

from steam_artwork_finder.sgdb import AuthError, SteamGridDB, clean_name


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.ok = 200 <= status < 300
        self.content = b"img"
        self.headers = {}

    def json(self):
        return self._body

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, routes):
        self.routes, self.headers, self.calls = routes, {}, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params, headers))
        for frag, resp in self.routes.items():
            if frag in url:
                return resp
        return FakeResp(404, {})


def test_best_match_prefers_exact_name():
    s = FakeSession({"/search/autocomplete/": FakeResp(200, {"success": True, "data": [
        {"id": 1, "name": "Hollow Knight: Silksong", "release_date": 1700000000},
        {"id": 2, "name": "Hollow Knight", "verified": True},
    ]})})
    g = SteamGridDB("k", s).best_match("Hollow Knight™")
    assert g.id == 2
    url, _, headers = s.calls[0]
    assert url.endswith("/search/autocomplete/Hollow%20Knight")
    assert headers["Authorization"] == "Bearer k"


def test_assets_use_slot_filters():
    s = FakeSession({"/grids/game/7": FakeResp(200, {"success": True, "data": [
        {"id": 9, "url": "https://x/full.png", "thumb": "https://x/t.png", "width": 600,
         "height": 900, "mime": "image/png", "score": 3, "style": "alternate", "author": {"name": "a"}},
    ]})})
    a = SteamGridDB("k", s).assets("grid", 7)
    assert a[0].url == "https://x/full.png"
    params = s.calls[0][1]
    assert "600x900" in params["dimensions"] and params["nsfw"] == "false"


def test_bad_key():
    s = FakeSession({"/": FakeResp(401, {"success": False})})
    with pytest.raises(AuthError):
        SteamGridDB("bad", s).search("x")
    assert SteamGridDB("bad", s).check_key() is False


def test_clean_name():
    assert clean_name("  DOOM®  Eternal™ ") == "DOOM Eternal"


def test_name_variants_strip_launcher_tags():
    from steam_artwork_finder.sgdb import name_variants

    assert name_variants("Cyberpunk 2077 - GOG") == ["Cyberpunk 2077 - GOG", "Cyberpunk 2077"]
    assert name_variants("Zelda: TotK (Ryujinx) [1.2]") == ["Zelda: TotK (Ryujinx) [1.2]", "Zelda: TotK (Ryujinx)", "Zelda: TotK"]
    assert name_variants("Hades") == ["Hades"]


def test_best_match_falls_back_to_stripped_name():
    class S(FakeSession):
        def get(self, url, params=None, headers=None, timeout=None):
            self.calls.append((url, params, headers))
            if url.endswith("/Hades"):
                return FakeResp(200, {"success": True, "data": [{"id": 5, "name": "Hades"}]})
            return FakeResp(200, {"success": True, "data": []})

    s = S({})
    assert SteamGridDB("k", s).best_match("Hades (Heroic)").id == 5
    assert len(s.calls) == 2


def test_rate_limit_waits_then_retries():
    class Limited(FakeSession):
        n = 0

        def get(self, url, params=None, headers=None, timeout=None):
            self.n += 1
            if self.n == 1:
                r = FakeResp(429, {"success": False})
                r.headers = {"Retry-After": "2"}
                return r
            return FakeResp(200, {"success": True, "data": [{"id": 1, "name": "x"}]})

    waits = []
    c = SteamGridDB("k", Limited({}))
    c.sleep = waits.append
    assert c.search("x")[0].id == 1
    assert waits == [2.0]


def test_sessions_are_per_thread():
    import threading

    c = SteamGridDB("k")
    seen = []
    t = threading.Thread(target=lambda: seen.append(c.http))
    t.start(); t.join()
    assert seen[0] is not c.http


def test_suggestions_use_the_folder_for_file_names():
    from steam_artwork_finder.sgdb import looks_like_file, suggestions

    assert looks_like_file("SB.exe") and not looks_like_file("Celeste")
    assert suggestions("SB.exe", "/home/a/Games/Super Bomberman R/SB.exe") == [
        ("Super Bomberman R", "folder"), ("SB", "file")]
    win = '"C:\\Games\\Super_Bomberman_R\\Binaries\\Win64\\SB.exe"'
    assert suggestions("SB.exe", win)[0] == ("Super Bomberman R", "folder")
    assert suggestions("game.exe", "/home/acid/game.exe") == [("game", "file")]  # a home folder is no name
    assert suggestions("Celeste - GOG", "/home/a/Games/Celeste/Celeste") == [("Celeste", "name")]


def test_generic_titles_search_by_folder():
    from steam_artwork_finder.sgdb import generic_name, search_name, suggestions

    hk = '"C:\\Games\\Hollow Knight\\game.exe"'
    assert generic_name("game.exe", hk) and search_name("game.exe", hk) == "Hollow Knight"
    assert generic_name("Launcher", "/home/u/Games/Hades II/Launcher.exe")
    assert search_name("Launcher", "/home/u/Games/Hades II/Launcher.exe") == "Hades II"
    assert generic_name("hollow_knight", "/home/u/Games/Hollow Knight/hollow_knight.x86_64")
    assert suggestions("Launcher", "/g/Hades II/Launcher.exe")[0] == ("Hades II", "folder")
    # Real titles stay as they are, even when the program has the game's name.
    assert not generic_name("Celeste", "/home/deck/Games/Celeste/Celeste")
    assert not generic_name("Hollow Knight™", "/opt/hk/hollow_knight.x86_64")
    assert search_name("Celeste", "/home/deck/Games/Celeste/Celeste") == "Celeste"
    assert search_name("Launcher") == "Launcher"  # nothing better to go on
