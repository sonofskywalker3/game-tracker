import requests

import barcode
import igdb_match
import models


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._payload


def test_lookup_returns_first_item_title(monkeypatch):
    def fake_get(url, params, timeout):
        assert params["upc"] == "711719541028"
        return _FakeResp({"items": [{"title": "Marvel's Spider-Man 2 - PS5"}]})

    monkeypatch.setattr(barcode.requests, "get", fake_get)
    assert barcode.lookup_product_title("711719541028") == "Marvel's Spider-Man 2 - PS5"


def test_lookup_returns_none_when_no_items(monkeypatch):
    monkeypatch.setattr(barcode.requests, "get", lambda url, params, timeout: _FakeResp({"items": []}))
    assert barcode.lookup_product_title("000") is None


def test_lookup_degrades_to_none_on_network_error(monkeypatch):
    def boom(url, params, timeout):
        raise requests.Timeout("slow")

    monkeypatch.setattr(barcode.requests, "get", boom)
    assert barcode.lookup_product_title("000") is None


def _seed_game(title, platform_short=None):
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT id FROM games WHERE title = ?", (title,)).fetchone()[0]
    conn.commit()
    conn.close()
    return gid


def test_registry_put_then_get_roundtrip(temp_db):
    conn = models.get_db()
    game_id = _seed_game("Halo")
    barcode.registry_put(conn, "abc", igdb_id=42, title="Halo", platform="xbox", game_id=game_id)
    conn.commit()
    row = barcode.registry_get(conn, "abc")
    conn.close()
    assert row["igdb_id"] == 42
    assert row["title"] == "Halo"
    assert row["platform"] == "xbox"
    assert row["game_id"] == game_id


def test_resolve_returns_cache_hit_without_calling_api(temp_db, monkeypatch):
    conn = models.get_db()
    game_id = _seed_game("Halo")
    barcode.registry_put(conn, "abc", igdb_id=42, title="Halo", platform="xbox", game_id=game_id)
    conn.commit()

    def fail(*a, **k):
        raise AssertionError("API must not be called on a cache hit")

    monkeypatch.setattr(barcode, "lookup_product_title", fail)
    result = barcode.resolve(conn, "abc")
    conn.close()
    assert result["source"] == "cache"
    assert result["candidates"][0]["title"] == "Halo"


def test_resolve_miss_returns_source_none(temp_db, monkeypatch):
    # Neutralize the whole chain so no real network call falls through to Wikidata.
    monkeypatch.setattr(barcode, "PRODUCT_SOURCES", (lambda u: None,))
    conn = models.get_db()
    result = barcode.resolve(conn, "999")
    conn.close()
    assert result == {"upc": "999", "source": "none", "candidates": [], "scanned_platform": None}


def test_resolve_via_api_maps_candidates_and_flags_ownership(temp_db, monkeypatch):
    owned_id = _seed_game("Marvel's Spider-Man 2")
    monkeypatch.setattr(barcode, "lookup_product_title",
                        lambda upc: "Marvel's Spider-Man 2 - PS5")
    # Pin the chain to the mocked source so no real network call falls through.
    monkeypatch.setattr(barcode, "PRODUCT_SOURCES", (barcode.lookup_product_title,))
    monkeypatch.setattr(igdb_match, "candidates_for", lambda *a, **k: [
        {"igdb_id": 119171, "name": "Marvel's Spider-Man 2",
         "cover_url": "https://img/x.jpg", "platforms": [167], "source": "search"},
    ])
    monkeypatch.setattr(igdb_match, "short_names_for", lambda ids: ["ps5"])
    conn = models.get_db()
    result = barcode.resolve(conn, "711719541028", client_id="cid", token="tok")
    conn.close()
    assert result["source"] == "upc_api"
    cand = result["candidates"][0]
    assert cand["igdb_id"] == 119171
    assert cand["platform"] == "PS5"
    assert cand["owned_game_id"] == owned_id


def test_resolve_api_hit_no_igdb_match_returns_product_title(temp_db, monkeypatch):
    monkeypatch.setattr(barcode, "lookup_product_title", lambda upc: "Some Obscure Game")
    # Pin the chain to the mocked source so no real network call falls through.
    monkeypatch.setattr(barcode, "PRODUCT_SOURCES", (barcode.lookup_product_title,))
    monkeypatch.setattr(igdb_match, "candidates_for", lambda *a, **k: [])
    conn = models.get_db()
    result = barcode.resolve(conn, "555", client_id="cid", token="tok")
    conn.close()
    assert result["source"] == "upc_api"
    assert result["candidates"] == []
    assert result["product_title"] == "Some Obscure Game"
