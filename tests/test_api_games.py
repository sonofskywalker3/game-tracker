import pytest

import models


def set_mode(client, mode):
    return client.put('/api/profile', json={'collection_display_mode': mode})


@pytest.fixture
def seed_compilation(client):
    """Container id=1, member id=2 (parent_collection_id=1), standalone id=9."""
    conn = models.get_db()
    conn.executemany(
        "INSERT INTO games (id, title, normalized_title) VALUES (?, ?, ?)",
        [(1, "Container Collection", "container collection"),
         (2, "Member Game", "member game"),
         (9, "Standalone Game", "standalone game")],
    )
    conn.execute("UPDATE games SET parent_collection_id = 1 WHERE id = 2")
    conn.commit()
    conn.close()


def _ensure_platform(name, short, category):
    conn = models.get_db()
    conn.execute(
        "INSERT OR IGNORE INTO platforms (name, short_name, category) VALUES (?, ?, ?)",
        (name, short, category),
    )
    conn.commit()
    conn.close()


def _insert_game(title, short_name, physical=False):
    conn = models.get_db()
    conn.execute(
        "INSERT INTO games (title, normalized_title) VALUES (?, ?)",
        (title, models.normalize_title(title)),
    )
    gid = conn.execute("SELECT id FROM games WHERE title = ?", (title,)).fetchone()[0]
    pid = conn.execute(
        "SELECT id FROM platforms WHERE short_name = ?", (short_name,)
    ).fetchone()[0]
    fmt = "physical" if physical else "digital"
    conn.execute(
        "INSERT INTO game_platforms (game_id, platform_id, format) VALUES (?, ?, ?)",
        (gid, pid, fmt),
    )
    conn.execute("INSERT INTO user_ratings (game_id, status) VALUES (?, 'backlog')", (gid,))
    conn.commit()
    conn.close()
    return gid


def test_normalize_endpoint_is_display_only_and_collision_safe(client):
    # Two rows whose cleaned display titles collide. The endpoint must re-clean
    # the display title without recomputing normalized_title, so the
    # UNIQUE(normalized_title) constraint can't fire (no 500).
    conn = models.get_db()
    conn.executemany(
        "INSERT INTO games (title, normalized_title) VALUES (?, ?)",
        [
            ("Fantasy Life i", "fantasy life i"),
            ("Fantasy Life i - Nintendo Switch 2 Edition",
             "fantasy life i nintendo switch 2 edition"),
        ],
    )
    conn.commit()
    conn.close()

    resp = client.post("/api/games/normalize", json={})
    assert resp.status_code == 200
    assert resp.get_json()["cleaned_count"] == 1  # only the edition row's display changes

    conn = models.get_db()
    rows = {r["normalized_title"]: r["title"]
            for r in conn.execute("SELECT normalized_title, title FROM games")}
    conn.close()
    assert rows == {
        "fantasy life i": "Fantasy Life i",
        "fantasy life i nintendo switch 2 edition": "Fantasy Life i",
    }


def test_api_games_default_sort_is_alphabetical(client):
    # The default sort goes strictly by the game's own title. (The retired series
    # system used to sort by COALESCE(series.name, title), which could pull a
    # "Z..." game ahead of a "B..." one; that concept no longer exists.)
    conn = models.get_db()
    conn.executemany(
        "INSERT INTO games (title, normalized_title) VALUES (?, ?)",
        [("Zelda: Breath of the Wild", "zelda breath of the wild"),
         ("Beta Game", "beta game")],
    )
    conn.commit()
    conn.close()

    r = client.get('/api/games')
    assert r.status_code == 200
    games = r.get_json()
    titles = [g['title'] for g in games]
    assert titles == sorted(titles, key=str.lower)
    assert all('series_name' not in g for g in games)


def test_api_games_exposes_categories_and_physical(client):
    _ensure_platform("PlayStation 4", "PS4", "modern_console")
    _ensure_platform("PlayStation 3", "PS3", "legacy_console")
    # "PC" is seeded by init_db as category 'pc'.
    _insert_game("Modern Disc Game", "PS4", physical=True)
    _insert_game("Retro Game", "PS3")
    _insert_game("Desktop Game", "PC")

    rows = client.get("/api/games").get_json()
    by_title = {g["title"]: g for g in rows}

    assert by_title["Modern Disc Game"]["categories"] == ["modern_console"]
    assert by_title["Modern Disc Game"]["physical"] is True
    assert by_title["Modern Disc Game"]["physical_media"] == ["disc"]   # PS4 -> disc
    assert by_title["Retro Game"]["categories"] == ["legacy_console"]
    assert by_title["Retro Game"]["physical"] is False
    assert by_title["Retro Game"]["physical_media"] == []              # digital -> no media
    assert by_title["Desktop Game"]["categories"] == ["pc"]


def test_api_games_exposes_created_at_and_newest_sort(client):
    conn = models.get_db()
    conn.executemany(
        "INSERT INTO games (title, normalized_title, created_at) VALUES (?, ?, ?)",
        [("Older Game", "older game", "2020-01-01 00:00:00"),
         ("Newer Game", "newer game", "2024-01-01 00:00:00")],
    )
    conn.commit()
    conn.close()

    rows = client.get("/api/games?sort=newest&order=desc").get_json()
    titles = [g["title"] for g in rows]
    assert titles.index("Newer Game") < titles.index("Older Game")
    assert all("created_at" in g for g in rows)


def test_duplicates_endpoint_lists_definite_and_candidates(client):
    conn = models.get_db()
    conn.executemany(
        "INSERT INTO games (title, normalized_title) VALUES (?, ?)",
        [("Brotato", "brotato"),
         ("Brotato - Nintendo Switch 2 Edition", "brotato nsw2"),
         ("The Outer Worlds", "the outer worlds"),
         ("The Outer Worlds: Spacer's Choice Edition", "the outer worlds spacers choice edition")],
    )
    conn.commit()
    conn.close()

    body = client.get("/api/duplicates").get_json()
    keys = {g["id"]: g for g in body["games"]}
    assert any(sorted(group) for group in body["definite"])      # Brotato pair
    assert any(c["reason"] == "edition" for c in body["candidates"])
    # each referenced game is described for the modal
    some_id = body["definite"][0][0]
    assert "title" in keys[some_id] and "platforms" in keys[some_id]


def test_merge_endpoint_merges_and_refreshes(client):
    conn = models.get_db()
    conn.executemany(
        "INSERT INTO games (title, normalized_title) VALUES (?, ?)",
        [("Disco Elysium", "disco elysium"),
         ("Disco Elysium: The Final Cut", "disco elysium the final cut")],
    )
    rows = {r["title"]: r["id"] for r in conn.execute("SELECT id, title FROM games")}
    survivor = rows["Disco Elysium"]
    drop = rows["Disco Elysium: The Final Cut"]
    conn.execute("INSERT INTO user_ratings (game_id, status) VALUES (?, 'backlog')", (survivor,))
    conn.execute("INSERT INTO user_ratings (game_id, status) VALUES (?, 'backlog')", (drop,))
    conn.commit()
    conn.close()

    resp = client.post("/api/games/merge", json={
        "survivor_id": survivor, "drop_ids": [drop], "title": "Disco Elysium"})
    assert resp.status_code == 200
    conn = models.get_db()
    titles = [r["title"] for r in conn.execute("SELECT title FROM games")]
    conn.close()
    assert titles == ["Disco Elysium"]


def test_dismiss_endpoint_rejects_unknown_game(client):
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('Solo', 'solo')")
    gid = conn.execute("SELECT id FROM games").fetchone()["id"]
    conn.commit()
    conn.close()
    resp = client.post("/api/duplicates/dismiss", json={"game_id_a": gid, "game_id_b": 999999})
    assert resp.status_code == 400


def test_dismiss_endpoint_records_not_duplicate(client):
    conn = models.get_db()
    conn.executemany("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                     [("Don't Starve", "dont starve"),
                      ("Don't Starve Together", "dont starve together")])
    rows = {r["title"]: r["id"] for r in conn.execute("SELECT id, title FROM games")}
    a, b = rows["Don't Starve"], rows["Don't Starve Together"]
    conn.commit()
    conn.close()

    resp = client.post("/api/duplicates/dismiss", json={"game_id_a": a, "game_id_b": b})
    assert resp.status_code == 200
    assert resp.get_json()["count"] == 1
    conn = models.get_db()
    cnt = conn.execute("SELECT COUNT(*) FROM not_duplicates WHERE game_id_lo = ? AND game_id_hi = ?",
                       (min(a, b), max(a, b))).fetchone()[0]
    conn.close()
    assert cnt == 1


def test_duplicates_endpoint_groups_related_candidates(client):
    # A real family clusters via edition + contains (numeral-independent, so it
    # survives the "differ only by a number" rule); Celeste/Celest is a separate
    # similar pair.
    conn = models.get_db()
    conn.executemany(
        "INSERT INTO games (title, normalized_title) VALUES (?, ?)",
        [("Don't Starve", "dont starve"),
         ("Don't Starve: Console Edition", "dont starve console edition"),
         ("Don't Starve Together", "dont starve together"),
         ("Celeste", "celeste"),
         ("Celest", "celest")],
    )
    conn.commit()
    conn.close()

    body = client.get("/api/duplicates").get_json()
    assert "groups" in body
    sizes = sorted(len(g["members"]) for g in body["groups"])
    # the three Don't Starve rows form one family; Celeste/Celest a pair
    assert sizes == [2, 3]
    big = max(body["groups"], key=lambda g: len(g["members"]))
    assert big["pairs"]  # internal pairs are exposed for bulk dismiss


def test_dismiss_endpoint_bulk_pairs(client):
    conn = models.get_db()
    conn.executemany("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                     [("A", "a"), ("B", "b"), ("C", "c")])
    rows = {r["title"]: r["id"] for r in conn.execute("SELECT id, title FROM games")}
    a, b, c = rows["A"], rows["B"], rows["C"]
    conn.commit()
    conn.close()

    resp = client.post("/api/duplicates/dismiss",
                       json={"pairs": [[a, b], [a, c], [b, c]]})
    assert resp.status_code == 200
    assert resp.get_json()["count"] == 3
    conn = models.get_db()
    cnt = conn.execute("SELECT COUNT(*) FROM not_duplicates").fetchone()[0]
    conn.close()
    assert cnt == 3


def test_series_routes_removed(client):
    assert client.get('/api/series').status_code == 404
    assert client.get('/series').status_code == 404


def test_not_a_game_records_exclusion_and_deletes(client, tmp_path, monkeypatch):
    import import_scraped as imp
    monkeypatch.setattr(imp, "EXCLUDED_GAMES_PATH", tmp_path / "excluded_games.json")
    _ensure_platform("PlayStation 4", "PS4", "modern_console")
    gid = _insert_game("Animal Crossing Island Transfer Tool", "PS4")
    conn = models.get_db()
    conn.execute("INSERT INTO game_external_ids (game_id, source, external_id) "
                 "VALUES (?, 'playstation', 'TOOL1')", (gid,))
    conn.commit()
    conn.close()

    resp = client.post(f"/api/games/{gid}/not-a-game")
    assert resp.status_code == 200

    conn = models.get_db()
    assert conn.execute("SELECT COUNT(*) FROM games WHERE id = ?", (gid,)).fetchone()[0] == 0
    conn.close()
    assert imp.is_excluded("playstation", "TOOL1", "Animal Crossing Island Transfer Tool") is True


def test_not_a_game_404_for_missing(client):
    assert client.post("/api/games/999999/not-a-game").status_code == 404


def test_get_game_includes_dlc(client, temp_db):
    import models
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('G', 'g')")
    gid = conn.execute("SELECT id FROM games WHERE title='G'").fetchone()[0]
    conn.execute("INSERT INTO dlc (game_id, name, kind, owned, source) "
                 "VALUES (?, 'Pack A', 'dlc', 1, 'igdb')", (gid,))
    conn.commit()
    conn.close()
    resp = client.get(f"/api/games/{gid}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["dlc"] == [{"id": data["dlc"][0]["id"], "name": "Pack A",
                            "kind": "dlc", "owned": True, "source": "igdb"}]


def _make_game_with_dlc(name="Pack A"):
    import models
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('G', 'g')")
    gid = conn.execute("SELECT id FROM games WHERE title='G'").fetchone()[0]
    conn.execute("INSERT INTO dlc (game_id, name, source) VALUES (?, ?, 'igdb')", (gid, name))
    did = conn.execute("SELECT id FROM dlc WHERE game_id=?", (gid,)).fetchone()[0]
    conn.commit()
    conn.close()
    return gid, did


def test_toggle_dlc_owned(client, temp_db):
    gid, did = _make_game_with_dlc()
    resp = client.post(f"/api/dlc/{did}/owned", json={"owned": True})
    assert resp.status_code == 200 and resp.get_json()["owned"] is True
    import models
    conn = models.get_db()
    assert conn.execute("SELECT owned FROM dlc WHERE id=?", (did,)).fetchone()[0] == 1
    conn.close()
    assert client.post("/api/dlc/99999/owned", json={"owned": True}).status_code == 404


def test_add_manual_dlc_and_duplicate_noop(client, temp_db):
    gid, _ = _make_game_with_dlc()
    resp = client.post(f"/api/games/{gid}/dlc", json={"name": "Manual X"})
    assert resp.status_code == 201
    row = resp.get_json()
    assert row["name"] == "Manual X" and row["source"] == "manual"
    resp2 = client.post(f"/api/games/{gid}/dlc", json={"name": "Manual X"})
    assert resp2.status_code == 200 and resp2.get_json()["id"] == row["id"]
    assert client.post(f"/api/games/{gid}/dlc", json={"name": "  "}).status_code == 400


def test_delete_dlc(client, temp_db):
    gid, did = _make_game_with_dlc()
    assert client.delete(f"/api/dlc/{did}").status_code == 200
    import models
    conn = models.get_db()
    assert conn.execute("SELECT COUNT(*) FROM dlc WHERE id=?", (did,)).fetchone()[0] == 0
    conn.close()
    assert client.delete(f"/api/dlc/{did}").status_code == 404


def test_refresh_dlc_from_igdb(client, temp_db, monkeypatch):
    import config
    import igdb_dlc
    import models
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('G', 'g')")
    gid = conn.execute("SELECT id FROM games WHERE title='G'").fetchone()[0]
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "get_twitch_credentials", lambda: ("cid", "sec"))
    # The endpoint calls igdb_dlc.get_access_token (igdb_dlc imported it by name),
    # so patch that binding — patching fetch_covers.get_access_token would not take.
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda *a, **k: "tok")
    monkeypatch.setattr(igdb_dlc, "_igdb_query",
                        lambda q, c, t: [{"id": 7, "name": "G", "dlcs": [{"id": 1, "name": "P"}]}])
    resp = client.post(f"/api/games/{gid}/dlc/refresh")
    assert resp.status_code == 200
    names = [d["name"] for d in resp.get_json()["dlc"]]
    assert names == ["P"]


def test_refresh_dlc_without_credentials_400(client, temp_db, monkeypatch):
    import config
    import models
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('G', 'g')")
    gid = conn.execute("SELECT id FROM games WHERE title='G'").fetchone()[0]
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "get_twitch_credentials", lambda: (None, None))
    assert client.post(f"/api/games/{gid}/dlc/refresh").status_code == 400


def test_pin_igdb_identity_sets_cover_and_dlc(client, temp_db, monkeypatch):
    import config
    import igdb_dlc
    import models
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('G', 'g')")
    gid = conn.execute("SELECT id FROM games WHERE title='G'").fetchone()[0]
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "get_twitch_credentials", lambda: ("cid", "sec"))
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda *a, **k: "tok")
    monkeypatch.setattr(igdb_dlc, "_igdb_query",
                        lambda q, c, t: [{"id": 50, "name": "G", "slug": "g",
                                          "cover": {"url": "//img/t_thumb/co.jpg"},
                                          "expansions": [{"id": 2, "name": "Exp"}]}])
    resp = client.post(f"/api/games/{gid}/igdb",
                       json={"url": "https://www.igdb.com/games/g"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["game"]["igdb_id"] == 50
    assert data["game"]["cover_url"] == "https://img/t_cover_big/co.jpg"
    assert [d["name"] for d in data["dlc"]] == ["Exp"]


def test_pin_igdb_rejects_non_igdb_url(client, temp_db):
    import models
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('G', 'g')")
    gid = conn.execute("SELECT id FROM games WHERE title='G'").fetchone()[0]
    conn.commit()
    conn.close()
    assert client.post(f"/api/games/{gid}/igdb",
                       json={"url": "https://example.com/x.png"}).status_code == 400


def _one_game():
    import models
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('G', 'g')")
    gid = conn.execute("SELECT id FROM games WHERE title='G'").fetchone()[0]
    conn.commit()
    conn.close()
    return gid


def test_dlc_endpoints_unknown_game_404(client, temp_db):
    assert client.post("/api/games/99999/dlc", json={"name": "X"}).status_code == 404
    assert client.post("/api/games/99999/dlc/refresh").status_code == 404
    # pin: a valid IGDB URL on an unknown game still 404s (URL passes validation first)
    assert client.post("/api/games/99999/igdb",
                       json={"url": "https://www.igdb.com/games/g"}).status_code == 404


def test_pin_igdb_no_match_404(client, temp_db, monkeypatch):
    import config
    import igdb_dlc
    gid = _one_game()
    monkeypatch.setattr(config, "get_twitch_credentials", lambda: ("cid", "sec"))
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda *a, **k: "tok")
    monkeypatch.setattr(igdb_dlc, "_igdb_query", lambda q, c, t: [])  # no IGDB match
    resp = client.post(f"/api/games/{gid}/igdb",
                       json={"url": "https://www.igdb.com/games/nope"})
    assert resp.status_code == 404


def test_refresh_dlc_network_error_502(client, temp_db, monkeypatch):
    import config
    import igdb_dlc
    import requests
    gid = _one_game()
    monkeypatch.setattr(config, "get_twitch_credentials", lambda: ("cid", "sec"))
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda *a, **k: "tok")

    def boom(*a, **k):
        raise requests.RequestException("network down")

    monkeypatch.setattr(igdb_dlc, "_igdb_query", boom)
    resp = client.post(f"/api/games/{gid}/dlc/refresh")
    assert resp.status_code == 502


def test_igdb_candidates_and_pick(client, temp_db, monkeypatch):
    import igdb_dlc
    import igdb_match
    conn = models.get_db()
    conn.execute("INSERT INTO games (id,title,normalized_title,collection_name) "
                 "VALUES (1,'Mega Man 2','mega man 2','MM LC2')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda *a, **k: "tok")
    import config
    monkeypatch.setattr(config, "get_twitch_credentials", lambda: ("c", "s"))
    monkeypatch.setattr(igdb_match, "candidates_for", lambda *a, **k: [
        {"igdb_id": 1711, "name": "Mega Man 2", "cover_url": "https://x/t_cover_big/2.jpg",
         "platforms": [18], "source": "bundle"}])

    r = client.get("/api/games/1/igdb-candidates")
    assert r.status_code == 200 and r.get_json()["candidates"][0]["igdb_id"] == 1711

    r = client.post("/api/games/1/igdb-pick", json={"igdb_id": 1711,
                    "cover_url": "https://x/t_cover_big/2.jpg"})
    assert r.status_code == 200
    conn = models.get_db()
    row = conn.execute(
        "SELECT igdb_id, cover_url, igdb_locked, needs_igdb_review FROM games WHERE id=1"
    ).fetchone()
    conn.close()
    assert row["igdb_id"] == 1711 and row["igdb_locked"] == 1 and row["needs_igdb_review"] == 0
    assert row["cover_url"] == "https://x/t_cover_big/2.jpg"


def test_igdb_pick_coalesce_preserves_existing_cover(client, temp_db):
    """Picking with cover_url=None must NOT overwrite an existing cover_url (COALESCE)."""
    conn = models.get_db()
    conn.execute(
        "INSERT INTO games (id, title, normalized_title, cover_url) "
        "VALUES (2, 'Test Game', 'test game', 'https://existing/cover.jpg')"
    )
    conn.commit()
    conn.close()

    r = client.post("/api/games/2/igdb-pick", json={"igdb_id": 9999, "cover_url": None})
    assert r.status_code == 200

    conn = models.get_db()
    row = conn.execute(
        "SELECT igdb_id, cover_url, igdb_locked, needs_igdb_review FROM games WHERE id=2"
    ).fetchone()
    conn.close()
    assert row["igdb_id"] == 9999
    assert row["igdb_locked"] == 1
    assert row["needs_igdb_review"] == 0
    assert row["cover_url"] == "https://existing/cover.jpg"


def test_igdb_pick_clears_review_reason_and_list_surfaces_it(client, temp_db):
    """List surfaces igdb_review_reason; igdb-pick clears flag + reason."""
    # a flagged game with a reason
    conn = models.get_db()
    conn.execute(
        "INSERT INTO games (id,title,normalized_title,needs_igdb_review,igdb_review_reason) "
        "VALUES (1,'Mega Man X','mega man x',1,'bundle')")
    conn.commit()
    conn.close()

    # the list surfaces the reason
    r = client.get('/api/games')
    assert r.status_code == 200
    game = next(g for g in r.get_json() if g['id'] == 1)
    assert game['needs_igdb_review'] is True
    assert game['igdb_review_reason'] == 'bundle'

    # picking an identity clears flag + reason
    r = client.post('/api/games/1/igdb-pick',
                    json={'igdb_id': 1741, 'cover_url': 'https://x/t_cover_big/r.jpg'})
    assert r.status_code == 200
    conn = models.get_db()
    row = conn.execute(
        "SELECT needs_igdb_review, igdb_review_reason FROM games WHERE id=1").fetchone()
    conn.close()
    assert row['needs_igdb_review'] == 0 and row['igdb_review_reason'] is None


def test_pin_igdb_clears_review_reason(client, temp_db, monkeypatch):
    """Pinning an IGDB identity clears needs_igdb_review and igdb_review_reason."""
    import config
    import igdb_dlc
    conn = models.get_db()
    conn.execute(
        "INSERT INTO games (title, normalized_title, needs_igdb_review, igdb_review_reason) "
        "VALUES ('G', 'g', 1, 'bundle')"
    )
    gid = conn.execute("SELECT id FROM games WHERE title='G'").fetchone()[0]
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "get_twitch_credentials", lambda: ("cid", "sec"))
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda *a, **k: "tok")
    monkeypatch.setattr(igdb_dlc, "_igdb_query",
                        lambda q, c, t: [{"id": 50, "name": "G", "slug": "g",
                                          "cover": {"url": "//img/t_thumb/co.jpg"},
                                          "expansions": [{"id": 2, "name": "Exp"}]}])
    resp = client.post(f"/api/games/{gid}/igdb",
                       json={"url": "https://www.igdb.com/games/g"})
    assert resp.status_code == 200
    conn = models.get_db()
    row = conn.execute(
        "SELECT needs_igdb_review, igdb_review_reason FROM games WHERE id = ?", (gid,)
    ).fetchone()
    conn.close()
    assert row["needs_igdb_review"] == 0
    assert row["igdb_review_reason"] is None


def test_refresh_psn_nulls_marker_and_starts_scrape(client, temp_db, monkeypatch):
    import app as app_module
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title, psn_addons_synced_at) "
                 "VALUES ('G', 'g', '2020-01-01 00:00:00')")
    gid = conn.execute("SELECT id FROM games WHERE title='G'").fetchone()[0]
    conn.execute("INSERT INTO game_external_ids (game_id, source, external_id) "
                 "VALUES (?, 'playstation', 'UP0082-PPSA10664_00-FF16SIEA00000002')", (gid,))
    conn.commit()
    conn.close()

    started = {}

    def _fake_start(vendor: str, **kw) -> tuple[bool, str]:
        started["vendor"] = vendor
        return (True, "started")

    monkeypatch.setattr(app_module.scrape_service, "start", _fake_start)
    resp = client.post(f"/api/games/{gid}/dlc/refresh-psn")
    assert resp.status_code == 200
    conn = models.get_db()
    val = conn.execute("SELECT psn_addons_synced_at FROM games WHERE id=?", (gid,)).fetchone()[0]
    conn.close()
    assert val is None
    assert started["vendor"] == "playstation"


def test_refresh_psn_404_without_psn_id(client, temp_db):
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('G', 'g')")
    conn.commit()
    gid = conn.execute("SELECT id FROM games WHERE title='G'").fetchone()[0]
    conn.close()
    assert client.post(f"/api/games/{gid}/dlc/refresh-psn").status_code == 404


def test_refresh_psn_409_when_scrape_busy(client, temp_db, monkeypatch):
    import app as app_module
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title, psn_addons_synced_at) "
                 "VALUES ('G', 'g', '2020-01-01 00:00:00')")
    gid = conn.execute("SELECT id FROM games WHERE title='G'").fetchone()[0]
    conn.execute("INSERT INTO game_external_ids (game_id, source, external_id) "
                 "VALUES (?, 'playstation', 'UP0082-PPSA10664_00-FF16SIEA00000002')", (gid,))
    conn.commit()
    conn.close()
    monkeypatch.setattr(app_module.scrape_service, "start",
                        lambda vendor, **kw: (False, "a scrape is already running"))
    resp = client.post(f"/api/games/{gid}/dlc/refresh-psn")
    assert resp.status_code == 409
    assert resp.get_json()["started"] is False


def test_add_platform_to_existing_game(client):
    import models
    import barcode
    conn = models.get_db()
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (1,'A','a')")
    conn.execute("INSERT OR IGNORE INTO platforms (name, short_name, category) "
                 "VALUES ('Nintendo Switch','Switch','modern_console')")
    conn.commit()
    conn.close()

    resp = client.put("/api/games/1", json={
        "add_platform": {"short_name": "Switch", "format": "physical", "upc": "U1"}})
    assert resp.status_code == 200

    conn = models.get_db()
    fmt = conn.execute(
        "SELECT gp.format FROM game_platforms gp JOIN platforms p "
        "ON p.id=gp.platform_id WHERE gp.game_id=1 AND p.short_name='Switch'"
    ).fetchone()[0]
    reg = barcode.registry_get(conn, "U1")
    conn.close()
    assert fmt == "physical"
    assert reg["game_id"] == 1 and reg["platform"] == "Switch"


def test_game_detail_platforms_include_format_and_market(client):
    import models
    conn = models.get_db()
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (1, 'A', 'a')")
    conn.execute("INSERT INTO platforms (name, short_name, category, has_digital_market) "
                 "VALUES ('PlayStation 5', 'PS5', 'modern_console', 1)")
    pid = conn.execute("SELECT id FROM platforms WHERE short_name='PS5'").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) "
                 "VALUES (1, ?, 'digital')", (pid,))
    conn.commit()
    conn.close()

    p = client.get("/api/games/1").get_json()["platforms"][0]
    assert p["short_name"] == "PS5"
    assert p["format"] == "digital"
    assert p["has_digital_market"] == 1
    assert p["category"] == "modern_console"


def test_platforms_replace_preserves_format(client):
    import models
    _ensure_platform("PlayStation 5", "PS5", "modern_console")
    _ensure_platform("Nintendo Switch", "Switch", "modern_console")

    conn = models.get_db()
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (1, 'A', 'a')")
    pid = conn.execute("SELECT id FROM platforms WHERE short_name='PS5'").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) "
                 "VALUES (1, ?, 'physical')", (pid,))
    conn.commit()
    conn.close()

    # Add Switch via the full-replace path; PS5's existing format must survive.
    resp = client.put("/api/games/1", json={"platforms": ["PS5", "Switch"]})
    assert resp.status_code == 200

    conn = models.get_db()
    fmts = {r[0]: r[1] for r in conn.execute(
        "SELECT p.short_name, gp.format FROM game_platforms gp "
        "JOIN platforms p ON p.id = gp.platform_id WHERE gp.game_id = 1").fetchall()}
    conn.close()
    assert fmts["PS5"] == "physical"   # preserved, not wiped
    assert fmts["Switch"] is None      # newly added, no format yet


def test_platform_formats_setter_updates_without_membership_change(client):
    import models
    conn = models.get_db()
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (1, 'A', 'a')")
    conn.execute("INSERT INTO platforms (name, short_name, category) "
                 "VALUES ('PlayStation 5', 'PS5', 'modern_console')")
    pid = conn.execute("SELECT id FROM platforms WHERE short_name='PS5'").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) "
                 "VALUES (1, ?, 'digital')", (pid,))
    conn.commit()
    conn.close()

    resp = client.put("/api/games/1", json={
        "platform_formats": {"PS5": "physical", "Bogus": "physical", "PS5_bad": "weird"}})
    assert resp.status_code == 200

    conn = models.get_db()
    rows = conn.execute(
        "SELECT p.short_name, gp.format FROM game_platforms gp "
        "JOIN platforms p ON p.id = gp.platform_id WHERE gp.game_id = 1").fetchall()
    conn.close()
    assert len(rows) == 1                 # membership unchanged
    assert dict(rows)["PS5"] == "physical"  # format updated


def test_add_platform_marks_both_when_format_differs(client):
    import models
    conn = models.get_db()
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (1,'A','a')")
    conn.execute("INSERT OR IGNORE INTO platforms (name, short_name, category) "
                 "VALUES ('Nintendo Switch','Switch','modern_console')")
    pid = conn.execute("SELECT id FROM platforms WHERE short_name='Switch'").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) "
                 "VALUES (1, ?, 'digital')", (pid,))
    conn.commit()
    conn.close()
    # Buy the physical copy of a digitally-owned game.
    r = client.put("/api/games/1", json={
        "add_platform": {"short_name": "Switch", "format": "physical"}})
    assert r.status_code == 200
    conn = models.get_db()
    fmt = conn.execute("SELECT gp.format FROM game_platforms gp JOIN platforms p "
                       "ON p.id=gp.platform_id WHERE gp.game_id=1 AND p.short_name='Switch'"
                       ).fetchone()[0]
    conn.close()
    assert fmt == "both"


def test_add_platform_keeps_both_when_readding_single_format(client):
    import models
    conn = models.get_db()
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (1,'A','a')")
    conn.execute("INSERT OR IGNORE INTO platforms (name, short_name, category) "
                 "VALUES ('Nintendo Switch','Switch','modern_console')")
    pid = conn.execute("SELECT id FROM platforms WHERE short_name='Switch'").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) "
                 "VALUES (1, ?, 'both')", (pid,))
    conn.commit()
    conn.close()
    # Re-add the physical copy when you already own it in both formats.
    r = client.put("/api/games/1", json={
        "add_platform": {"short_name": "Switch", "format": "physical"}})
    assert r.status_code == 200
    conn = models.get_db()
    fmt = conn.execute("SELECT gp.format FROM game_platforms gp JOIN platforms p "
                       "ON p.id=gp.platform_id WHERE gp.game_id=1 AND p.short_name='Switch'"
                       ).fetchone()[0]
    conn.close()
    assert fmt == "both"   # not downgraded to 'physical'


def test_physical_media_derived_from_format_not_tag(client):
    """physical/physical_media come from game_platforms.format, never the tag."""
    _ensure_platform("Nintendo Switch", "Switch", "modern_console")
    _ensure_platform("PlayStation 5", "PS5", "modern_console")

    conn = models.get_db()
    # A) cartridge-only physical (Switch)
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (1,'Cart','cart')")
    sid = conn.execute("SELECT id FROM platforms WHERE short_name='Switch'").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) "
                 "VALUES (1, ?, 'physical')", (sid,))
    # B) owned on Switch (physical) AND PS5 (disc) -> both media, deduped + sorted
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (2,'Mixed','mixed')")
    pid = conn.execute("SELECT id FROM platforms WHERE short_name='PS5'").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) "
                 "VALUES (2, ?, 'both')", (sid,))   # Switch 'both' counts as physical
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) "
                 "VALUES (2, ?, 'physical')", (pid,))
    # C) has the legacy 'Physical' TAG but every platform is digital -> NOT physical
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (3,'Tagged','tagged')")
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) "
                 "VALUES (3, ?, 'digital')", (sid,))
    conn.execute("INSERT OR IGNORE INTO tags (name, category) VALUES ('Physical','custom')")
    tid = conn.execute("SELECT id FROM tags WHERE name='Physical'").fetchone()[0]
    conn.execute("INSERT INTO game_tags (game_id, tag_id) VALUES (3, ?)", (tid,))
    conn.commit()
    conn.close()

    by_id = {g["id"]: g for g in client.get("/api/games").get_json()}
    assert by_id[1]["physical"] is True
    assert by_id[1]["physical_media"] == ["cartridge"]
    assert by_id[2]["physical"] is True
    assert by_id[2]["physical_media"] == ["cartridge", "disc"]   # sorted, deduped
    assert by_id[3]["physical"] is False        # tag ignored; format is digital
    assert by_id[3]["physical_media"] == []


def test_games_mode_members_hides_container(client, seed_compilation):
    set_mode(client, 'members')
    ids = {g['id'] for g in client.get('/api/games').get_json()}
    assert 1 not in ids and 2 in ids and 9 in ids


def test_games_mode_collection_hides_members(client, seed_compilation):
    set_mode(client, 'collection')
    ids = {g['id'] for g in client.get('/api/games').get_json()}
    assert 1 in ids and 2 not in ids and 9 in ids


def test_games_mode_both_shows_all(client, seed_compilation):
    set_mode(client, 'both')
    ids = {g['id'] for g in client.get('/api/games').get_json()}
    assert {1, 2, 9} <= ids
