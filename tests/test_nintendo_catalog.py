import json
from pathlib import Path

from scrapers import nintendo_catalog as nc

ADDON_FIXTURE = Path(__file__).parent / "fixtures" / "nintendo_addon_page_sample.json"


def test_sku_from_nsuid_base_and_dlc():
    # sku = nsuid[0] + nsuid[3] + nsuid[6:]
    assert nc.sku_from_nsuid("70010000059002") == "7100059002"   # base game
    assert nc.sku_from_nsuid("70050000042414") == "7500042414"   # DLC


def test_nsuid_from_sku_inverts_sku_from_nsuid():
    for nsuid in ("70010000059002", "70050000042414", "70070000012345"):
        assert nc.nsuid_from_sku(nc.sku_from_nsuid(nsuid)) == nsuid


# --- parse_base_software: read the "requires this game" parent off an add-on page ---

def test_parse_base_software_returns_parent_nsuid_and_name():
    body = ADDON_FIXTURE.read_text(encoding="utf-8")
    parents = nc.parse_base_software(body)
    # the add-on's baseSoftware points at the base game (sku 7100059002 -> nsuid)
    assert parents == [("70010000059002", "Vampire Survivors")]


def test_parse_base_software_handles_multiple_bases():
    page = {"pageProps": {"initialApolloState": {
        'Product:{"sku":"7100059002"}': {"sku": "7100059002", "name": "Game A"},
        'Product:{"sku":"7100012302"}': {"sku": "7100012302", "name": "Game B"},
        'Product:{"sku":"7500042414"}': {
            "sku": "7500042414", "nsuid": "70050000042414", "name": "Cross-series Pack",
            "baseSoftware": [{"__ref": 'Product:{"sku":"7100059002"}'},
                             {"__ref": 'Product:{"sku":"7100012302"}'}]},
    }}}
    parents = nc.parse_base_software(page)
    assert ("70010000059002", "Game A") in parents
    assert ("70010000012302", "Game B") in parents
    assert len(parents) == 2


def test_parse_base_software_empty_on_garbage():
    assert nc.parse_base_software("{}") == []
    assert nc.parse_base_software('{"pageProps": {}}') == []


# --- build_addon_parent_map: per add-on, fetch its page and resolve the parent ---

class _FakeFetch:
    """Injected catalogue fetch: slug -> add-on page-data dict."""
    def __init__(self, pages):
        self._pages = pages

    def product_json(self, slug):
        return self._pages.get(slug)


def test_build_addon_parent_map_links_addon_to_base():
    fetch = _FakeFetch({"vamp-tides": json.loads(ADDON_FIXTURE.read_text(encoding="utf-8"))})
    pm = nc.build_addon_parent_map([("70050000042414", "vamp-tides")], fetch=fetch)
    assert set(pm) == {"70050000042414"}
    ref = pm["70050000042414"]
    assert ref.product_id == "70010000059002"   # parent GAME nsuid (matches game_external_ids)
    assert ref.name == "Vampire Survivors"


def test_build_addon_parent_map_prefers_owned_base():
    page = {"pageProps": {"initialApolloState": {
        'Product:{"sku":"7100059002"}': {"sku": "7100059002", "name": "Unowned Game"},
        'Product:{"sku":"7100012302"}': {"sku": "7100012302", "name": "Owned Game"},
        'Product:{"sku":"7500017612"}': {
            "sku": "7500017612", "nsuid": "70050000017612", "name": "Series BGM Pack",
            "baseSoftware": [{"__ref": 'Product:{"sku":"7100059002"}'},
                             {"__ref": 'Product:{"sku":"7100012302"}'}]},
    }}}
    fetch = _FakeFetch({"bgm-pack": page})
    pm = nc.build_addon_parent_map(
        [("70050000017612", "bgm-pack")], fetch=fetch,
        owned_game_nsuids={"70010000012302"})
    assert pm["70050000017612"].product_id == "70010000012302"   # the owned one wins
    assert pm["70050000017612"].name == "Owned Game"


def test_build_addon_parent_map_skips_ambiguous_when_none_owned():
    page = {"pageProps": {"initialApolloState": {
        'Product:{"sku":"7100059002"}': {"sku": "7100059002", "name": "Game A"},
        'Product:{"sku":"7100012302"}': {"sku": "7100012302", "name": "Game B"},
        'Product:{"sku":"7500017612"}': {
            "sku": "7500017612", "nsuid": "70050000017612", "name": "Series BGM Pack",
            "baseSoftware": [{"__ref": 'Product:{"sku":"7100059002"}'},
                             {"__ref": 'Product:{"sku":"7100012302"}'}]},
    }}}
    fetch = _FakeFetch({"bgm-pack": page})
    pm = nc.build_addon_parent_map([("70050000017612", "bgm-pack")], fetch=fetch)
    assert pm == {}   # multiple bases, none owned -> left for review


def test_build_addon_parent_map_skips_unresolvable():
    fetch = _FakeFetch({})                       # page fetch misses
    pm = nc.build_addon_parent_map([("70050000099999", "missing")], fetch=fetch)
    assert pm == {}


def test_build_addon_parent_map_skips_addon_without_slug():
    fetch = _FakeFetch({})
    pm = nc.build_addon_parent_map([("70050000099999", None)], fetch=fetch)
    assert pm == {}


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


def test_collect_addon_parents_end_to_end_offline():
    routes = {
        "/products/vamp-tides.json": json.loads(ADDON_FIXTURE.read_text(encoding="utf-8")),
    }
    page = _Page(routes, build_id="testbuild123")
    pm = nc.collect_addon_parents(page, captured=[],
                                  addon_items=[("70050000042414", "vamp-tides")])
    assert set(pm) == {"70050000042414"}
    assert pm["70050000042414"].name == "Vampire Survivors"


def test_bootstrap_returns_build_id():
    page = _Page({}, build_id="b1")
    assert nc.bootstrap(page) == "b1"


def test_bootstrap_raises_without_build_id():
    page = _Page({}, build_id=None)
    try:
        nc.bootstrap(page)
    except RuntimeError as exc:
        assert "buildId" in str(exc)
    else:
        raise AssertionError("expected bootstrap to raise without a buildId")
