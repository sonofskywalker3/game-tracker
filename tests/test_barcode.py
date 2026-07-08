import requests

import barcode
import igdb_match
import import_scraped
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


def test_owned_game_id_matches_stored_match_key(temp_db):
    """games.normalized_title stores normalize_title(clean_title(...)) (the
    import_scraped.match_key composition), so ownership detection must apply the
    same composed key: an IGDB name carrying a platform-edition suffix still
    matches the stored game."""
    conn = models.get_db()
    conn.execute(
        "INSERT INTO games (title, normalized_title) VALUES (?, ?)",
        ("Minecraft", import_scraped.match_key("Minecraft: Nintendo Switch Edition")))
    conn.commit()
    gid = conn.execute("SELECT id FROM games").fetchone()[0]
    assert barcode._owned_game_id(conn, "Minecraft: Nintendo Switch Edition") == gid
    # Plain titles keep matching too.
    assert barcode._owned_game_id(conn, "Minecraft") == gid
    conn.close()


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


def test_split_trailing_platform_3ds_suffix():
    # A bare trailing "3D"/"3DS" suffix is the 3DS platform + stripped title.
    assert barcode.split_trailing_platform(
        "Theatrhythm Final Fantasy: Curtain Call 3D") == (
        "3DS", "Theatrhythm Final Fantasy: Curtain Call")
    assert barcode.split_trailing_platform("Mario Kart 7 3DS") == ("3DS", "Mario Kart 7")
    assert barcode.split_trailing_platform("Ballz 3D") == ("3DS", "Ballz")


def test_split_trailing_platform_leaves_mid_name_3d_alone():
    # "3D" mid-title is part of the name, not a platform suffix.
    assert barcode.split_trailing_platform("Super Mario 3D World") == (
        None, "Super Mario 3D World")
    assert barcode.split_trailing_platform("Celeste") == (None, "Celeste")


def test_resolve_3ds_trailing_token_strips_title_and_detects_platform(temp_db, monkeypatch):
    # "...Curtain Call 3D": the bare 3D suffix must be stripped for the IGDB search
    # AND read as the 3DS platform, so the owned game is found.
    owned_id = _seed_game("Theatrhythm Final Fantasy: Curtain Call")
    monkeypatch.setattr(barcode, "lookup_product_title",
                        lambda upc: "Theatrhythm Final Fantasy: Curtain Call 3D")
    monkeypatch.setattr(barcode, "PRODUCT_SOURCES", (barcode.lookup_product_title,))
    seen_titles = []

    def fake_candidates_for(title, platform_ids, *a, **k):
        seen_titles.append(title)
        # IGDB only matches the clean name, never the "...3D" retail string.
        if "3d" in title.lower():
            return []
        return [{"igdb_id": 5151, "name": "Theatrhythm Final Fantasy: Curtain Call",
                 "cover_url": "https://img/cc.jpg", "platforms": [37], "source": "search"}]

    monkeypatch.setattr(igdb_match, "candidates_for", fake_candidates_for)
    monkeypatch.setattr(igdb_match, "short_names_for", lambda ids: ["3DS"])
    conn = models.get_db()
    result = barcode.resolve(conn, "662248914152", client_id="cid", token="tok")
    conn.close()
    assert result["scanned_platform"] == "3DS"
    cand = result["candidates"][0]
    assert cand["igdb_id"] == 5151
    assert cand["platform"] == "3DS"
    assert cand["owned_game_id"] == owned_id
    # The search was issued with the "3D" stripped off.
    assert all("3d" not in t.lower() for t in seen_titles)


def test_resolve_drops_edition_variants_when_base_present(temp_db, monkeypatch):
    # A physical scan should resolve to the base game, not its edition SKUs.
    monkeypatch.setattr(barcode, "lookup_product_title",
                        lambda upc: "Dragon Quest Monsters: The Dark Prince")
    monkeypatch.setattr(barcode, "PRODUCT_SOURCES", (barcode.lookup_product_title,))
    monkeypatch.setattr(igdb_match, "candidates_for", lambda *a, **k: [
        {"igdb_id": 1, "name": "Dragon Quest Monsters: The Dark Prince",
         "platforms": [], "game_type": 0},
        {"igdb_id": 2, "name": "Dragon Quest Monsters: The Dark Prince - Digital Deluxe Edition",
         "platforms": [], "game_type": 0},
        {"igdb_id": 3, "name": "Dragon Quest Monsters: The Dark Prince - Master Edition",
         "platforms": [], "game_type": 0},
    ])
    monkeypatch.setattr(igdb_match, "short_names_for", lambda ids: [])
    conn = models.get_db()
    result = barcode.resolve(conn, "111", client_id="cid", token="tok")
    conn.close()
    names = [c["title"] for c in result["candidates"]]
    assert names == ["Dragon Quest Monsters: The Dark Prince"]


def test_resolve_keeps_edition_when_no_base_sibling(temp_db, monkeypatch):
    # An edition with no plain-base sibling is the only match -> keep it.
    monkeypatch.setattr(barcode, "lookup_product_title", lambda upc: "Some Game Deluxe Edition")
    monkeypatch.setattr(barcode, "PRODUCT_SOURCES", (barcode.lookup_product_title,))
    monkeypatch.setattr(igdb_match, "candidates_for", lambda *a, **k: [
        {"igdb_id": 9, "name": "Some Game - Deluxe Edition", "platforms": [], "game_type": 0},
    ])
    monkeypatch.setattr(igdb_match, "short_names_for", lambda ids: [])
    conn = models.get_db()
    result = barcode.resolve(conn, "222", client_id="cid", token="tok")
    conn.close()
    assert [c["title"] for c in result["candidates"]] == ["Some Game - Deluxe Edition"]


def test_clean_product_title_strips_leading_reseller_condition_words():
    # UPCitemdb often prefixes condition/reseller/platform-shorthand noise; IGDB's
    # title search returns 0 hits when the query starts with it.
    assert barcode.clean_product_title(
        "NSW - FANTASIAN Neo Dimension - Nintendo Switch") == "FANTASIAN Neo Dimension"
    assert barcode.clean_product_title(
        "Refurbished Nintendo Super Mario Maker 2 (Nintendo Switch)"
    ) == "Nintendo Super Mario Maker 2"


def test_clean_product_title_leading_platform_and_new_words_protected():
    # "new" and platform words are NOT leading noise — real titles start with them
    # ("New Super Mario Bros.", "Wii Sports", "Nintendo Switch Sports").
    assert barcode.clean_product_title(
        "New Super Mario Bros. U - Nintendo Switch").startswith("New Super Mario Bros")
    assert barcode.clean_product_title("Wii Sports") == "Wii Sports"
    cleaned = barcode.clean_product_title("Nintendo Switch Sports - Nintendo Switch")
    assert "Switch Sports" in cleaned
    assert cleaned.strip()


def test_strip_leading_publisher():
    assert barcode._strip_leading_publisher(
        "Nintendo Super Mario Maker 2") == "Super Mario Maker 2"
    assert barcode._strip_leading_publisher(
        "Square Enix Fantasian Neo Dimension") == "Fantasian Neo Dimension"
    assert barcode._strip_leading_publisher(
        "Super Mario Maker 2") == "Super Mario Maker 2"


def test_scan_candidates_falls_back_to_leading_publisher_strip(monkeypatch):
    # All normal ladder steps miss; stripping a leading publisher token and
    # retrying unrestricted is the last resort, only reached when nothing else hit.
    seen_titles = []

    def fake_candidates_for(title, platform_ids, *a, **k):
        seen_titles.append(title)
        if title == "Super Mario Maker 2":
            return [{"igdb_id": 115276, "name": "Super Mario Maker 2",
                     "platforms": [130], "source": "search"}]
        return []

    monkeypatch.setattr(igdb_match, "candidates_for", fake_candidates_for)
    raw, used_hint = barcode._scan_candidates(
        "Nintendo Super Mario Maker 2", "Nintendo Super Mario Maker 2", set(),
        "cid", "tok")
    assert raw and raw[0]["igdb_id"] == 115276
    assert used_hint is True
    assert "Super Mario Maker 2" in seen_titles


def test_scan_candidates_does_not_strip_publisher_when_plain_search_hits(monkeypatch):
    # A title that already matches as-is must never reach the publisher fallback.
    def fake_candidates_for(title, platform_ids, *a, **k):
        if title == "Nintendo Switch Sports":
            return [{"igdb_id": 1, "name": "Nintendo Switch Sports",
                     "platforms": [130], "source": "search"}]
        raise AssertionError(f"should not search for stripped title: {title!r}")

    monkeypatch.setattr(igdb_match, "candidates_for", fake_candidates_for)
    raw, used_hint = barcode._scan_candidates(
        "Nintendo Switch Sports", "Nintendo Switch Sports", set(), "cid", "tok")
    assert raw[0]["igdb_id"] == 1
    assert used_hint is True


def test_resolve_trailing_3d_falls_back_to_unstripped_for_other_platform(temp_db, monkeypatch):
    # "Ballz 3D" is a real Genesis game where "3D" is the NAME, not the platform.
    # Stripping finds nothing as 3DS, so resolve must retry the un-stripped title and
    # NOT stamp the 3DS guess onto the result.
    monkeypatch.setattr(barcode, "lookup_product_title", lambda upc: "Ballz 3D")
    monkeypatch.setattr(barcode, "PRODUCT_SOURCES", (barcode.lookup_product_title,))

    def fake_candidates_for(title, platform_ids, *a, **k):
        if title.lower() == "ballz 3d":          # only the un-stripped name matches
            return [{"igdb_id": 700, "name": "Ballz 3D",
                     "cover_url": "https://img/b.jpg", "platforms": [29], "source": "search"}]
        return []                                # stripped "Ballz" finds nothing

    monkeypatch.setattr(igdb_match, "candidates_for", fake_candidates_for)
    monkeypatch.setattr(igdb_match, "short_names_for",
                        lambda ids: ["Genesis"] if 29 in (ids or []) else [])
    conn = models.get_db()
    result = barcode.resolve(conn, "012345678905", client_id="cid", token="tok")
    conn.close()
    cand = result["candidates"][0]
    assert cand["igdb_id"] == 700
    assert cand["platform"] == "Genesis"      # candidate's own platform, not the 3DS guess
    assert result["scanned_platform"] is None  # wrong 3DS hint dropped
