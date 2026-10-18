import pytest

from steam_artwork_finder.sgdb import AuthError, SteamGridDB, clean_name


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.ok = 200 <= status < 300
        self.content = b"img"

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
