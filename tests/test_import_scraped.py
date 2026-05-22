import models
import import_scraped as imp


def _add_existing_game(title, platform_short="PS4", category="modern_console",
                       status="playing", rating=4):
    """Insert a curated game the way the live DB holds one."""
    conn = models.get_db()
    conn.execute(
        "INSERT OR IGNORE INTO platforms (name, short_name, category) VALUES (?, ?, ?)",
        (platform_short, platform_short, category),
    )
    display = models.clean_title(title)
    norm = models.normalize_title(display)
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)", (display, norm))
    gid = conn.execute("SELECT id FROM games WHERE normalized_title = ?", (norm,)).fetchone()[0]
    pid = conn.execute("SELECT id FROM platforms WHERE short_name = ?", (platform_short,)).fetchone()[0]
    conn.execute("INSERT OR IGNORE INTO game_platforms (game_id, platform_id) VALUES (?, ?)", (gid, pid))
    conn.execute("INSERT INTO user_ratings (game_id, status, rating) VALUES (?, ?, ?)", (gid, status, rating))
    conn.commit()
    conn.close()
    return gid


def _g(title, platform, source="playstation", external_id=None, cover_url=None):
    return {"title": title, "platform": platform, "source": source,
            "external_id": external_id, "cover_url": cover_url, "source_title": title}


def test_new_legacy_game_creates_legacy_platform(temp_db):
    conn = models.get_db()
    stats = imp.import_games(conn, [_g("Tomba", "PS3", external_id="X1")], "playstation")
    conn.commit()
    assert stats.new_games == 1
    cat = conn.execute("SELECT category FROM platforms WHERE short_name = 'PS3'").fetchone()[0]
    assert cat == "legacy_console"
    conn.close()


def test_external_id_match_survives_rename(temp_db):
    conn = models.get_db()
    imp.import_games(conn, [_g("NieR:Automata", "PS4", external_id="CUSA07")], "playstation")
    conn.commit()
    conn.execute("UPDATE games SET title = 'Nier', normalized_title = ?",
                 (models.normalize_title("Nier"),))
    conn.commit()
    stats = imp.import_games(conn, [_g("NieR:Automata", "PS4", external_id="CUSA07")], "playstation")
    conn.commit()
    assert stats.external_id_matches == 1
    assert stats.new_games == 0
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
    conn.close()


def test_existing_curation_is_preserved(temp_db):
    gid = _add_existing_game("Hades", "PS4", status="completed", rating=4)
    conn = models.get_db()
    imp.import_games(conn, [_g("Hades", "PS4", external_id="C1")], "playstation")
    conn.commit()
    row = conn.execute("SELECT status, rating FROM user_ratings WHERE game_id = ?", (gid,)).fetchone()
    assert row["status"] == "completed"
    assert row["rating"] == 4
    conn.close()


def test_import_is_idempotent(temp_db):
    conn = models.get_db()
    games = [_g("Celeste", "PS4", external_id="C2")]
    imp.import_games(conn, games, "playstation")
    conn.commit()
    stats = imp.import_games(conn, games, "playstation")
    conn.commit()
    assert stats.new_games == 0
    assert stats.platform_links_added == 0
    assert stats.external_ids_added == 0
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
    conn.close()


def test_cross_vendor_unifies_into_one_game(temp_db):
    conn = models.get_db()
    imp.import_games(conn, [_g("Hades", "PS5", "playstation", external_id="P1")], "playstation")
    conn.commit()
    imp.import_games(conn, [_g("Hades", "Xbox", "xbox", external_id="X9")], "xbox")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
    gid = conn.execute("SELECT id FROM games").fetchone()[0]
    plats = {r[0] for r in conn.execute(
        "SELECT p.short_name FROM platforms p "
        "JOIN game_platforms gp ON gp.platform_id = p.id WHERE gp.game_id = ?", (gid,))}
    assert plats == {"PS5", "Xbox"}
    assert conn.execute("SELECT COUNT(*) FROM game_external_ids WHERE game_id = ?", (gid,)).fetchone()[0] == 2
    conn.close()


def test_dry_run_writes_nothing(temp_db):
    conn = models.get_db()
    stats = imp.import_games(conn, [_g("Bastion", "PS4", external_id="B1")], "playstation", dry_run=True)
    conn.commit()
    assert stats.new_games == 1
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0
    conn.close()


def test_fuzzy_confirm_merges(temp_db):
    _add_existing_game("The Legend of Zelda: Breath of the Wild", "Switch")
    conn = models.get_db()
    stats = imp.import_games(
        conn,
        [_g("Legend of Zelda Breath of the Wild", "Switch", "nintendo", external_id="N1")],
        "nintendo",
        confirm_fn=lambda *a: True,
    )
    conn.commit()
    assert stats.fuzzy_confirmed == 1
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
    conn.close()


def test_fuzzy_reject_creates_new(temp_db):
    _add_existing_game("The Legend of Zelda: Breath of the Wild", "Switch")
    conn = models.get_db()
    stats = imp.import_games(
        conn,
        [_g("Legend of Zelda Breath of the Wild", "Switch", "nintendo", external_id="N1")],
        "nintendo",
        confirm_fn=lambda *a: False,
    )
    conn.commit()
    assert stats.fuzzy_rejected == 1
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 2
    conn.close()
