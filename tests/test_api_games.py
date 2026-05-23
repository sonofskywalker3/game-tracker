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
