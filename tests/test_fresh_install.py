"""Fresh-install startup: init_db alone is stale; ensure_db must bring a brand-new
DB fully up to date (init + full migrate), and one-time reconciles must not re-run."""
import models
import app as app_module


def test_ensure_db_builds_full_schema_on_fresh_path(tmp_path, monkeypatch):
    db_path = tmp_path / "fresh.db"
    monkeypatch.setattr(models, "DB_PATH", db_path)
    monkeypatch.setattr(app_module, "DB_PATH", db_path)
    app_module.ensure_db()
    conn = models.get_db()
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"slots", "slot_history", "slot_dismissals", "dlc", "dlc_external_ids",
            "barcode_registry", "upc_review", "series", "not_duplicates",
            "decider_chats"} <= tables
    game_cols = {c[1] for c in conn.execute("PRAGMA table_info(games)").fetchall()}
    assert {"igdb_id", "session_length", "series_role", "hltb_main_minutes",
            "time_to_beat_override_minutes"} <= game_cols
    conn.close()


def test_ensure_db_is_idempotent_on_existing_db(tmp_path, monkeypatch):
    db_path = tmp_path / "fresh.db"
    monkeypatch.setattr(models, "DB_PATH", db_path)
    monkeypatch.setattr(app_module, "DB_PATH", db_path)
    app_module.ensure_db()
    app_module.ensure_db()   # second run must not raise or duplicate seeds
    conn = models.get_db()
    n_slots = conn.execute("SELECT COUNT(*) FROM slots").fetchone()[0]
    assert n_slots == 4   # seed_default_slots seeds once
    conn.close()


def test_series_id_index_created(tmp_path, monkeypatch):
    db_path = tmp_path / "fresh.db"
    monkeypatch.setattr(models, "DB_PATH", db_path)
    monkeypatch.setattr(app_module, "DB_PATH", db_path)
    app_module.ensure_db()
    conn = models.get_db()
    indexes = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    assert "idx_user_ratings_series_id" in indexes
    conn.close()


def test_tagged_physical_reconcile_does_not_rerun(temp_db):
    """The legacy-Physical-tag reconcile is one-time: after it has run, a
    deliberate physical->digital edit must survive the next startup."""
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('G', 'g')")
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    pid = conn.execute("SELECT id FROM platforms WHERE short_name='Switch'").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) "
                 "VALUES (?, ?, 'digital')", (gid, pid))
    conn.execute("INSERT OR IGNORE INTO tags (name, category) VALUES ('Physical', 'custom')")
    tag_id = conn.execute("SELECT id FROM tags WHERE name='Physical'").fetchone()[0]
    conn.execute("INSERT INTO game_tags (game_id, tag_id) VALUES (?, ?)", (gid, tag_id))
    # The fixture's migrate_db already stamped the one-time flag on the empty DB;
    # clear it to simulate a pre-flag database seeing its first reconcile.
    conn.execute("DELETE FROM schema_flags WHERE name = ?",
                 (models.TAGGED_PHYSICAL_FLAG,))
    conn.commit()

    models.migrate_tagged_games_to_physical(conn)
    fmt = conn.execute("SELECT format FROM game_platforms WHERE game_id=?",
                       (gid,)).fetchone()[0]
    assert fmt == "physical"   # first run reconciles the legacy tag

    conn.execute("UPDATE game_platforms SET format='digital' WHERE game_id=?", (gid,))
    conn.commit()
    models.migrate_tagged_games_to_physical(conn)   # startup re-run
    fmt = conn.execute("SELECT format FROM game_platforms WHERE game_id=?",
                       (gid,)).fetchone()[0]
    assert fmt == "digital"   # deliberate edit survives
    conn.close()
