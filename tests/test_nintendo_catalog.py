import json
from pathlib import Path

from scrapers import nintendo_catalog as nc

FIXTURE = Path(__file__).parent / "fixtures" / "nintendo_dlc_list_sample.json"


def test_sku_from_nsuid_base_and_dlc():
    # sku = nsuid[0] + nsuid[3] + nsuid[6:]
    assert nc.sku_from_nsuid("70010000059002") == "7100059002"   # base game
    assert nc.sku_from_nsuid("70050000042414") == "7500042414"   # DLC


def test_parse_dlc_list_returns_only_addons():
    body = FIXTURE.read_text(encoding="utf-8")
    entries = nc.parse_dlc_list(body)
    # the 7005 add-ons only -- the 7001 base game is excluded
    nsuids = {e.nsuid for e in entries}
    assert nsuids == {"70050000042414", "70050000061841"}
    by_id = {e.nsuid: e for e in entries}
    assert by_id["70050000042414"].name == "Vampire Survivors: Tides of the Foscari"


def test_parse_dlc_list_empty_on_garbage():
    assert nc.parse_dlc_list("{}") == []
    assert nc.parse_dlc_list('{"pageProps": {}}') == []


class _FakeFetch:
    """Injected catalogue fetch: sku -> Algolia game record, slug -> dlc.json dict."""
    def __init__(self, games, dlc_bodies):
        self._games = games
        self._dlc = dlc_bodies

    def game(self, sku):
        return self._games.get(sku)

    def dlc_json(self, slug):
        return self._dlc.get(slug)


def test_build_parent_map_links_owned_dlc_to_parent_game():
    fetch = _FakeFetch(
        games={"7100059002": {"urlKey": "vampire-survivors-switch", "title": "Vampire Survivors"}},
        dlc_bodies={"vampire-survivors-switch": json.loads(FIXTURE.read_text(encoding="utf-8"))},
    )
    pm = nc.build_parent_map(["70010000059002"], fetch=fetch)
    assert set(pm) == {"70050000042414", "70050000061841"}
    ref = pm["70050000042414"]
    assert ref.product_id == "70010000059002"   # parent GAME nsuid (matches game_external_ids)
    assert ref.name == "Vampire Survivors"


def test_build_parent_map_skips_unknown_or_dlc_less_games():
    fetch = _FakeFetch(games={}, dlc_bodies={})           # nothing resolves
    assert nc.build_parent_map(["70010000059002"], fetch=fetch) == {}


# --- live glue smoke test (bootstrap + LiveFetch wired, no real network) ---

class _Resp:
    def __init__(self, ok, data):
        self.ok = ok
        self._data = data

    def json(self):
        return self._data


class _Req:
    def __init__(self, routes):
        self._routes = routes

    def get(self, url, headers=None):
        for frag, data in self._routes.items():
            if frag in url:
                return _Resp(True, data)
        return _Resp(False, None)


class _Page:
    def __init__(self, routes, build_id):
        self.request = _Req(routes)
        self._build_id = build_id

    def goto(self, url):
        pass

    def evaluate(self, _js):
        return self._build_id

    def wait_for_timeout(self, _ms):
        pass


def test_collect_parent_map_end_to_end_offline():
    routes = {
        f"/{nc.GAME_INDEX}/7100059002": {"urlKey": "vampire-survivors-switch",
                                         "title": "Vampire Survivors"},
        "vampire-survivors-switch/dlc.json": json.loads(FIXTURE.read_text(encoding="utf-8")),
    }
    page = _Page(routes, build_id="testbuild123")
    captured = [{"url": "https://u3b6gr4ua3-dsn.algolia.net/1/indexes/store_game_en_us/query",
                 "request_headers": {"X-Algolia-API-Key": "live-key-abc"}}]
    pm = nc.collect_parent_map(page, captured, ["70010000059002"])
    assert set(pm) == {"70050000042414", "70050000061841"}
    assert pm["70050000061841"].name == "Vampire Survivors"


def test_bootstrap_falls_back_to_embedded_key():
    # no live key in the capture -> use the embedded public key, buildId from page
    page = _Page({}, build_id="b1")
    key, build_id = nc.bootstrap(page, captured=[])
    assert key == nc.ALGOLIA_KEY and build_id == "b1"


def test_bootstrap_raises_without_build_id():
    page = _Page({}, build_id=None)
    try:
        nc.bootstrap(page, captured=[])
    except RuntimeError as exc:
        assert "buildId" in str(exc)
    else:
        raise AssertionError("expected bootstrap to raise without a buildId")
