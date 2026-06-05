import models


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
    conn.execute(
        "INSERT INTO game_platforms (game_id, platform_id) VALUES (?, ?)", (gid, pid)
    )
    conn.execute("INSERT INTO user_ratings (game_id, status) VALUES (?, 'backlog')", (gid,))
    if physical:
        conn.execute("INSERT OR IGNORE INTO tags (name, category) VALUES ('Physical', 'custom')")
        tid = conn.execute("SELECT id FROM tags WHERE name = 'Physical'").fetchone()[0]
        conn.execute("INSERT INTO game_tags (game_id, tag_id) VALUES (?, ?)", (gid, tid))
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
    assert by_title["Retro Game"]["categories"] == ["legacy_console"]
    assert by_title["Retro Game"]["physical"] is False
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


def test_duplicates_groups_expose_existing_series(client):
    conn = models.get_db()
    conn.executemany(
        "INSERT INTO games (title, normalized_title) VALUES (?, ?)",
        [("Don't Starve", "dont starve"),
         ("Don't Starve: Console Edition", "dont starve console edition"),
         ("SteamWorld Heist", "steamworld heist"),
         ("SteamWorld Heist: Ultimate Edition", "steamworld heist ultimate edition")],
    )
    rows = {r["title"]: r["id"] for r in conn.execute("SELECT id, title FROM games")}
    conn.execute("INSERT INTO series (name) VALUES ('Don''t Starve')")
    sid = conn.execute("SELECT id FROM series WHERE name = 'Don''t Starve'").fetchone()["id"]
    conn.execute("INSERT INTO user_ratings (game_id, series_id, series_order) VALUES (?, ?, 0)",
                 (rows["Don't Starve"], sid))
    conn.commit()
    conn.close()

    groups = client.get("/api/duplicates").get_json()["groups"]
    ds = next(g for g in groups if rows["Don't Starve"] in g["members"])
    sw = next(g for g in groups if rows["SteamWorld Heist"] in g["members"])
    assert ds["existing_series_id"] == sid
    assert ds["existing_series_name"] == "Don't Starve"
    assert sw["existing_series_id"] is None and sw["existing_series_name"] is None


def test_from_group_creates_series_and_assigns(client):
    conn = models.get_db()
    conn.executemany("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                     [("SteamWorld Heist", "steamworld heist"),
                      ("SteamWorld Dig", "steamworld dig")])
    rows = {r["title"]: r["id"] for r in conn.execute("SELECT id, title FROM games")}
    conn.commit()
    conn.close()
    resp = client.post("/api/series/from-group", json={
        "name": "SteamWorld",
        "game_ids": [rows["SteamWorld Heist"], rows["SteamWorld Dig"]], "remember": False})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["created"] is True and body["assigned"] == 2
    conn = models.get_db()
    sid = conn.execute("SELECT id FROM series WHERE name = 'SteamWorld'").fetchone()["id"]
    n = conn.execute("SELECT COUNT(*) FROM user_ratings WHERE series_id = ?", (sid,)).fetchone()[0]
    conn.close()
    assert n == 2


def test_from_group_finds_existing_and_skips_already_assigned(client):
    conn = models.get_db()
    conn.executemany("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                     [("Halo", "halo"), ("Halo 2", "halo 2")])
    rows = {r["title"]: r["id"] for r in conn.execute("SELECT id, title FROM games")}
    conn.execute("INSERT INTO series (name) VALUES ('Halo')")
    sid = conn.execute("SELECT id FROM series WHERE name = 'Halo'").fetchone()["id"]
    conn.execute("INSERT INTO user_ratings (game_id, series_id, series_order) VALUES (?, ?, 0)",
                 (rows["Halo"], sid))
    conn.commit()
    conn.close()
    resp = client.post("/api/series/from-group", json={
        "name": "halo", "game_ids": [rows["Halo"], rows["Halo 2"]], "remember": False})
    body = resp.get_json()
    assert body["created"] is False and body["series_id"] == sid
    assert body["assigned"] == 1


def test_from_group_remember_writes_pattern(client, tmp_path, monkeypatch):
    monkeypatch.setattr(models, "SERIES_PATTERNS_PATH", tmp_path / "series_patterns.json")
    monkeypatch.setattr(models, "SERIES_PATTERNS_DEFAULT_PATH", tmp_path / "missing.json")
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('Ori', 'ori')")
    gid = conn.execute("SELECT id FROM games").fetchone()["id"]
    conn.commit()
    conn.close()
    client.post("/api/series/from-group", json={"name": "Ori", "game_ids": [gid], "remember": True})
    assert models.load_series_patterns().get("Ori") == "Ori"


def test_from_group_requires_name_and_games(client):
    assert client.post("/api/series/from-group", json={"name": "", "game_ids": [1]}).status_code == 400
    assert client.post("/api/series/from-group", json={"name": "X", "game_ids": []}).status_code == 400


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


def test_pick_igdb_series_name_prefers_exact_match():
    from app import pick_igdb_series_name
    results = [{"name": "Final Fantasy VII"}, {"name": "Final Fantasy"}]
    assert pick_igdb_series_name("final fantasy", results) == "Final Fantasy"


def test_pick_igdb_series_name_best_similarity():
    from app import pick_igdb_series_name
    results = [{"name": "SteamWorld Dig"}, {"name": "SteamWorld"}]
    assert pick_igdb_series_name("steamworld build", results) == "SteamWorld"


def test_pick_igdb_series_name_none_when_no_good_match():
    from app import pick_igdb_series_name
    assert pick_igdb_series_name("final fantasy", [{"name": "Halo"}, {"name": "Doom"}]) is None


def test_pick_igdb_series_name_empty():
    from app import pick_igdb_series_name
    assert pick_igdb_series_name("anything", []) is None


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
