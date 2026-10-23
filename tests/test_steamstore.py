"""Steam's official store artwork: finding the game and its pieces, without the network."""

from steam_artwork_finder.steamstore import CDN, ITEMS, SEARCH, SteamStore


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body or {}
        self.ok = status < 400

    def json(self):
        return self._body


class Session:
    def __init__(self, items=None, search=None, exists=()):
        self.items, self.search, self.exists = items, search or {}, set(exists)
        self.calls, self.heads = [], []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        if url == SEARCH:
            hits = {k.casefold(): v for k, v in self.search.items()}
            return Resp(200, {"items": hits.get(params["term"].casefold(), [])})
        if url == ITEMS:
            if self.items is None:
                return Resp(500)
            return Resp(200, {"response": {"store_items": [self.items]}})
        return Resp(404)

    def head(self, url, timeout=None, allow_redirects=True):
        self.heads.append(url)
        return Resp(200 if url in self.exists else 404)


def store(session):
    s = SteamStore(session)
    s.sleep = lambda secs: None
    return s


def test_match_takes_only_the_same_name():
    s = store(Session(search={
        "Hades": [{"type": "app", "id": 1145360, "name": "Hades"}, {"type": "app", "id": 2, "name": "Hades II"}],
        "Hollow Knight": [{"type": "app", "id": 367520, "name": "Hollow Knight: Silksong"}],
        "Celeste - GOG": [], "Celeste": [{"type": "app", "id": 504230, "name": "Celeste"}],
    }))
    assert s.match("HADES™").appid == 1145360  # case and symbols don't count
    assert s.match("Hollow Knight") is None  # a near name waits for the user
    assert s.match("Celeste - GOG").appid == 504230  # launcher tags come off, as for SteamGridDB


def test_assets_use_the_listed_files_for_newer_games():
    fmt = "steam/apps/9/abc123${FILENAME}?t=5"
    s = store(Session(items={"appid": 9, "assets": {
        "asset_url_format": fmt, "library_capsule_2x": "/library_600x900_2x.jpg",
        "library_capsule": "/library_600x900.jpg", "header": "/header.jpg", "library_hero": "/library_hero.jpg",
        "community_icon": "deadbeef"}}))  # 32 × 32: never offered
    a = s.assets(9)
    assert a["grid"].url == CDN + "steam/apps/9/abc123/library_600x900_2x.jpg?t=5"
    assert a["grid"].thumb.endswith("/library_600x900.jpg?t=5") and a["grid"].mime == "image/jpeg"
    assert a["hero"].url.endswith("/library_hero.jpg?t=5") and a["wide"].url.endswith("/header.jpg?t=5")
    assert "logo" not in a  # not listed and not at the plain name
    assert "icon" not in a
    assert all(x.author == "Steam" and x.id < 0 for x in a.values())
    assert len({x.id for x in a.values()}) == len(a)


def test_assets_fall_back_to_the_plain_names():
    plain = CDN + "steam/apps/7/"
    sess = Session(items=None, exists={plain + "logo.png", plain + "header.jpg"})
    a = store(sess).assets(7)
    assert sorted(a) == ["logo", "wide"]
    assert a["logo"].mime == "image/png"


def test_store_calls_are_spaced_out():
    waits = []
    s = SteamStore(Session(search={"a": [], "b": []}))
    s.sleep = waits.append
    s.gap = 5
    s.search("a")
    s.search("b")
    s.search("a")  # remembered: no call
    assert len(waits) == 1 and waits[0] > 4


def test_store_failure_is_not_remembered():
    import pytest
    from steam_artwork_finder.steamstore import SteamStore, SteamStoreError

    class Down:
        def get(self, *a, **k):
            import requests
            raise requests.ConnectionError("offline")

        def head(self, *a, **k):
            import requests
            raise requests.ConnectionError("offline")

    store = SteamStore(session=Down())
    store.sleep = lambda s: None
    with pytest.raises(SteamStoreError):
        store.assets(620)
    assert 620 not in store._assets


def test_a_network_blip_on_a_plain_name_is_not_remembered():
    import requests

    plain = CDN + "steam/apps/8/"

    class Blip(Session):
        def head(self, url, timeout=None, allow_redirects=True):
            if url.endswith("logo.png") and not self.heads.count(url):
                self.heads.append(url)
                raise requests.ConnectionError("blip")
            return super().head(url, timeout, allow_redirects)

    sess = Blip(items={"assets": {}}, exists={plain + "logo.png"})
    s = store(sess)
    assert "logo" not in s.assets(8)  # couldn't tell this time
    assert "logo" in s.assets(8)  # so it's asked again, and found
