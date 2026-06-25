import barcode


def test_clean_product_title_strips_publisher_and_catalog_numbers():
    cases = {
        "Super Mario 3D All-Stars Nintendo 045496596743": "Super Mario 3D All-Stars",
        "The Legend of Zelda: Link's Awakening 110249": "The Legend of Zelda: Link's Awakening",
        "Bravely Default II 045496596842": "Bravely Default II",
    }
    for raw, expected in cases.items():
        assert barcode.clean_product_title(raw) == expected


def test_clean_product_title_preserves_short_title_numbers():
    assert barcode.clean_product_title("1942") == "1942"
    assert barcode.clean_product_title("FIFA 23 (PS5)") == "FIFA 23"


def test_clean_product_title_strips_retail_noise():
    cases = {
        "Mario Kart 8 Deluxe racing video game (Nintendo Switch)": "Mario Kart 8 Deluxe",
        "Animal Crossing: New Horizons  Nintendo Switch  [Physical] - U.S. Version":
            "Animal Crossing: New Horizons",
        "The Legend of Zelda: Breath of the Wild - Nintendo Switch":
            "The Legend of Zelda: Breath of the Wild",
        "Marvel's Spider-Man 2 - PS5": "Marvel's Spider-Man 2",
    }
    for raw, expected in cases.items():
        assert barcode.clean_product_title(raw) == expected


def test_clean_product_title_handles_empty():
    assert barcode.clean_product_title(None) == ""
    assert barcode.clean_product_title("") == ""


def test_resolve_uses_cleaned_title_for_prefill(client, monkeypatch):
    # UPC lookup returns a noisy retail title; force the IGDB match to miss so we
    # hit the manual-search branch, which must hand back the cleaned name.
    monkeypatch.setattr(
        barcode, "lookup_product_title",
        lambda upc: "Mario Kart 8 Deluxe racing video game (Nintendo Switch)",
    )
    # Pin the chain to the mocked source so no real network call falls through.
    monkeypatch.setattr(barcode, "PRODUCT_SOURCES", (barcode.lookup_product_title,))
    monkeypatch.setattr(barcode.igdb_match, "candidates_for",
                        lambda *a, **k: [])
    resp = client.get("/api/barcode/resolve?upc=12345")
    body = resp.get_json()
    assert body["source"] == "upc_api"
    assert body["candidates"] == []
    assert body["product_title"] == "Mario Kart 8 Deluxe"


def test_resolve_requires_upc(client):
    resp = client.get("/api/barcode/resolve")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "upc required"


def test_resolve_returns_cache_hit(client, monkeypatch):
    import models
    conn = models.get_db()
    barcode.registry_put(conn, "abc", igdb_id=42, title="Halo", platform="xbox", game_id=None)
    conn.commit()
    conn.close()

    resp = client.get("/api/barcode/resolve?upc=abc")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["source"] == "cache"
    assert body["candidates"][0]["title"] == "Halo"


def test_resolve_miss_is_source_none(client, monkeypatch):
    # No Twitch creds in the temp config -> client_id None -> never calls IGDB;
    # force the UPC lookup to miss so we get source 'none'.
    # Neutralize the whole chain so no real network call falls through to Wikidata.
    monkeypatch.setattr(barcode, "PRODUCT_SOURCES", (lambda u: None,))
    resp = client.get("/api/barcode/resolve?upc=999")
    assert resp.status_code == 200
    assert resp.get_json() == {"upc": "999", "source": "none", "candidates": [], "scanned_platform": None}


def test_post_game_with_upc_writes_cache(client):
    import models
    resp = client.post("/api/games", json={"title": "Celeste", "upc": "abc123"})
    assert resp.status_code == 201
    gid = resp.get_json()["game_id"]

    conn = models.get_db()
    row = barcode.registry_get(conn, "abc123")
    conn.close()
    assert row is not None
    assert row["game_id"] == gid
    assert row["title"] == "Celeste"


def test_post_existing_game_with_upc_links_existing(client):
    import models
    first = client.post("/api/games", json={"title": "Hades"})
    gid = first.get_json()["game_id"]
    # Same title again -> 409 existing, but the UPC should still map to it.
    dup = client.post("/api/games", json={"title": "Hades", "upc": "ean999"})
    assert dup.status_code == 409

    conn = models.get_db()
    row = barcode.registry_get(conn, "ean999")
    conn.close()
    assert row["game_id"] == gid


def test_post_game_without_upc_writes_no_cache_row(client):
    import models
    client.post("/api/games", json={"title": "Tunic"})
    conn = models.get_db()
    count = conn.execute("SELECT COUNT(*) FROM barcode_registry").fetchone()[0]
    conn.close()
    assert count == 0


def test_existing_game_with_upc_caches_igdb_id(client):
    import models
    conn = models.get_db()
    conn.execute(
        "INSERT INTO games (title, normalized_title, igdb_id) VALUES (?, ?, ?)",
        ("Owned RPG", models.normalize_title("Owned RPG"), 555),
    )
    gid = conn.execute("SELECT id FROM games WHERE title = 'Owned RPG'").fetchone()[0]
    conn.commit()
    conn.close()

    resp = client.post("/api/games", json={"title": "Owned RPG", "upc": "upc-existing"})
    assert resp.status_code == 409
    assert resp.get_json()["game_id"] == gid

    conn = models.get_db()
    row = barcode.registry_get(conn, "upc-existing")
    conn.close()
    assert row["game_id"] == gid
    assert row["igdb_id"] == 555


def test_post_game_with_upc_caches_igdb_id_from_enrichment(client, monkeypatch):
    import app
    import igdb_dlc
    import models

    monkeypatch.setattr(app, "get_twitch_credentials", lambda: ("cid", "secret"))
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda client_id, secret: "tok")

    def fake_enrich(conn, game_id, client_id, token):
        conn.execute("UPDATE games SET igdb_id = ? WHERE id = ?", (424242, game_id))

    monkeypatch.setattr(igdb_dlc, "enrich_game", fake_enrich)

    resp = client.post("/api/games", json={"title": "Tunic Scan", "upc": "upc-enrich"})
    assert resp.status_code == 201
    gid = resp.get_json()["game_id"]

    conn = models.get_db()
    row = barcode.registry_get(conn, "upc-enrich")
    conn.close()
    assert row["game_id"] == gid
    assert row["igdb_id"] == 424242


def test_parse_retail_platform():
    assert barcode.parse_retail_platform(
        "Mario Kart 8 Deluxe (Nintendo Switch)") == "Switch"
    assert barcode.parse_retail_platform(
        "God of War Ragnarok - PlayStation 5") == "PS5"
    assert barcode.parse_retail_platform("Some PC Game") == "PC"
    assert barcode.parse_retail_platform("No platform here") is None


def test_owned_platforms_for_includes_format_and_market(temp_db):
    import models
    conn = models.get_db()
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (3, 'Q', 'q')")
    conn.execute("INSERT INTO platforms (name, short_name, category, has_digital_market) "
                 "VALUES ('PlayStation 5','PS5','modern_console',1)")
    pid = conn.execute("SELECT id FROM platforms WHERE short_name='PS5'").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) "
                 "VALUES (3, ?, 'physical')", (pid,))
    conn.commit()
    got = barcode.owned_platforms_for(conn, 3)
    conn.close()
    assert got == [{"short_name": "PS5", "format": "physical", "has_digital_market": 1}]


def test_resolve_records_nothing_for_unmatched_scan(client, monkeypatch):
    import models
    monkeypatch.setattr(barcode, "lookup_product_title",
                        lambda upc: "Totally Unknown Game (Nintendo Switch)")
    monkeypatch.setattr(barcode, "PRODUCT_SOURCES", (barcode.lookup_product_title,))
    monkeypatch.setattr(barcode.igdb_match, "candidates_for", lambda *a, **k: [])
    resp = client.get("/api/barcode/resolve?upc=NEW123")
    assert resp.get_json()["scanned_platform"] == "Switch"
    conn = models.get_db()
    row = barcode.registry_get(conn, "NEW123")
    count = conn.execute("SELECT COUNT(*) FROM barcode_registry").fetchone()[0]
    conn.close()
    assert row is None        # resolve must not poison the registry
    assert count == 0


def test_resolve_single_match_writes_nothing(client, monkeypatch):
    import models
    monkeypatch.setattr(barcode, "lookup_product_title", lambda upc: "Celeste (PS5)")
    monkeypatch.setattr(barcode, "PRODUCT_SOURCES", (barcode.lookup_product_title,))
    monkeypatch.setattr(barcode.igdb_match, "candidates_for", lambda *a, **k: [
        {"igdb_id": 1, "name": "Celeste", "platforms": [167], "cover_url": "c",
         "source": "search", "score": 99, "game_type": 0}])
    monkeypatch.setattr(barcode.igdb_match, "short_names_for", lambda ids: ["PS5"])
    client.get("/api/barcode/resolve?upc=NOWRITE1")
    conn = models.get_db()
    count = conn.execute("SELECT COUNT(*) FROM barcode_registry").fetchone()[0]
    conn.close()
    assert count == 0


def test_resolve_cache_hit_includes_owned_platforms(client):
    import models
    conn = models.get_db()
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (8,'Halo','halo')")
    conn.execute("INSERT OR IGNORE INTO platforms (name, short_name, category, has_digital_market) "
                 "VALUES ('Xbox','Xbox','modern_console',1)")
    pid = conn.execute("SELECT id FROM platforms WHERE short_name='Xbox'").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) "
                 "VALUES (8, ?, 'physical')", (pid,))
    barcode.registry_put(conn, "HALOUPC", igdb_id=1, title="Halo", platform="Xbox", game_id=8)
    conn.commit()
    conn.close()
    body = client.get("/api/barcode/resolve?upc=HALOUPC").get_json()
    assert body["source"] == "cache"
    cand = body["candidates"][0]
    assert cand["owned_game_id"] == 8
    assert cand["owned_platforms"] == [{"short_name": "Xbox", "format": "physical", "has_digital_market": 1}]


def test_resolve_reports_owned_bundle_constituents(client, monkeypatch):
    import models
    conn = models.get_db()
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES "
                 "(10, 'Mega Man X', ?)", (models.normalize_title("Mega Man X"),))
    conn.execute("INSERT OR IGNORE INTO platforms (name, short_name, category, has_digital_market) "
                 "VALUES ('Super Nintendo','SNES','legacy_console',0)")
    pid = conn.execute("SELECT id FROM platforms WHERE short_name='SNES'").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) "
                 "VALUES (10, ?, 'physical')", (pid,))
    conn.commit()
    conn.close()

    monkeypatch.setattr(barcode, "lookup_product_title",
                        lambda upc: "Mega Man X Legacy Collection (Nintendo Switch)")
    # Pin the chain to the mocked source so no real network call falls through.
    monkeypatch.setattr(barcode, "PRODUCT_SOURCES", (barcode.lookup_product_title,))
    monkeypatch.setattr(barcode.igdb_match, "candidates_for", lambda *a, **k: [
        {"igdb_id": 500, "name": "Mega Man X Legacy Collection", "platforms": [130],
         "cover_url": "c", "source": "search", "score": 100, "game_type": 3}])
    monkeypatch.setattr(barcode.igdb_match, "bundle_constituents", lambda *a, **k: [
        {"igdb_id": 1, "name": "Mega Man X", "normalized_title": "mega man x",
         "cover_url": "x", "platforms": [19]},
        {"igdb_id": 2, "name": "Mega Man X2", "normalized_title": "mega man x2",
         "cover_url": "y", "platforms": [19]}])

    body = client.get("/api/barcode/resolve?upc=MMX").get_json()
    cons = body["candidates"][0]["constituents"]
    owned = {c["title"]: c["owned_platforms"] for c in cons}
    assert owned["Mega Man X"] == [{"short_name": "SNES", "format": "physical",
                                    "has_digital_market": 0}]
    assert owned["Mega Man X2"] == []


def test_resolve_retries_unrestricted_when_platform_filter_zeroes_out(client, monkeypatch):
    monkeypatch.setattr(barcode, "lookup_product_title",
                        lambda upc: "Obscure Game (Nintendo Switch)")
    # Pin the chain to the mocked source so no real network call falls through.
    monkeypatch.setattr(barcode, "PRODUCT_SOURCES", (barcode.lookup_product_title,))
    monkeypatch.setattr(barcode.igdb_match, "platform_ids_for", lambda shorts: {130})
    monkeypatch.setattr(barcode.igdb_match, "short_names_for", lambda ids: ["Switch"])

    calls = []

    def fake_candidates_for(title, plat_ids, coll, cid, tok, *,
                            drop_fan_types=False, restrict_to_platform=False):
        calls.append(restrict_to_platform)
        if restrict_to_platform:
            return []   # platform-restricted search finds nothing
        return [{"igdb_id": 77, "name": "Obscure Game", "platforms": [130],
                 "cover_url": "c", "source": "search", "score": 50, "game_type": 0}]

    monkeypatch.setattr(barcode.igdb_match, "candidates_for", fake_candidates_for)

    body = client.get("/api/barcode/resolve?upc=OBSCURE1").get_json()
    assert calls == [True, False]          # restricted first, then unrestricted retry
    assert body["candidates"][0]["title"] == "Obscure Game"


def test_registry_stores_and_returns_cover_url(temp_db):
    import models
    conn = models.get_db()
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (5,'G','g')")
    barcode.registry_put(conn, "U1", title="G", platform="Switch",
                         cover_url="http://x/c.jpg", game_id=5)
    conn.commit()
    row = barcode.registry_get(conn, "U1")
    conn.close()
    assert row["cover_url"] == "http://x/c.jpg"


def test_registry_put_does_not_clobber_cover_with_null(temp_db):
    import models
    conn = models.get_db()
    barcode.registry_put(conn, "U2", title="G", platform="Switch", cover_url="http://x/c.jpg")
    barcode.registry_put(conn, "U2", title="G", platform="Switch")  # no cover this time
    conn.commit()
    row = barcode.registry_get(conn, "U2")
    conn.close()
    assert row["cover_url"] == "http://x/c.jpg"   # preserved


def test_cache_hit_returns_platform_and_cover(client, monkeypatch):
    import models
    conn = models.get_db()
    barcode.registry_put(conn, "CACHED1", igdb_id=9, title="Cached Game",
                         platform="Switch", cover_url="http://x/cc.jpg")
    conn.commit()
    conn.close()
    body = client.get("/api/barcode/resolve?upc=CACHED1").get_json()
    assert body["source"] == "cache"
    assert body["scanned_platform"] == "Switch"
    assert body["candidates"][0]["cover_url"] == "http://x/cc.jpg"
    assert body["candidates"][0]["platform"] == "Switch"
