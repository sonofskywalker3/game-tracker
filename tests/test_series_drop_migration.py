"""Drop migration for the retired home-rolled series system.

Covers the brief's idempotency contract plus the two paths that matter in
production: a FRESH install (init_db + migrate_db must never create series
schema) and an EXISTING DB carrying the old series table/columns (must end
clean AND still accept user_ratings writes, which a dangling series FK would
otherwise break)."""
import sqlite3

import models


def test_migrate_drop_series_is_idempotent(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE series(id INTEGER PRIMARY KEY, name TEXT);"
        "CREATE TABLE games(id INTEGER PRIMARY KEY, title TEXT, series_role TEXT, series_role_source TEXT);"
    )
    conn.commit()
    models.migrate_drop_series(conn)
    models.migrate_drop_series(conn)  # idempotent
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert 'series' not in tables
    cols = {r[1] for r in conn.execute("PRAGMA table_info(games)")}
    assert 'series_role' not in cols and 'series_role_source' not in cols
    conn.close()


def _seed_old_series_schema(db_path):
    """Build a DB shaped like the pre-removal schema: series table, user_ratings
    with the indexed + FK-pinned series columns, games.series_role, slots.focus_series_id."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE series(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE);
        CREATE TABLE games(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT,
                           series_role TEXT, series_role_source TEXT);
        CREATE TABLE user_ratings(
            game_id INTEGER PRIMARY KEY,
            status TEXT DEFAULT 'backlog',
            rating INTEGER,
            notes TEXT,
            priority INTEGER DEFAULT 5,
            hours_played REAL DEFAULT 0,
            started_at DATE,
            completed_at DATE,
            sort_order INTEGER,
            series_id INTEGER,
            series_order INTEGER,
            series_source TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE,
            FOREIGN KEY (series_id) REFERENCES series(id) ON DELETE SET NULL
        );
        CREATE INDEX idx_user_ratings_series_id ON user_ratings(series_id);
        CREATE INDEX idx_user_ratings_status ON user_ratings(status);
        CREATE TABLE slots(id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT,
                           focus_series_id INTEGER REFERENCES series(id) ON DELETE SET NULL);
    """)
    conn.execute("INSERT INTO series(id, name) VALUES(1, 'Halo')")
    conn.execute("INSERT INTO games(id, title, series_role) VALUES(1, 'Halo 3', 'mainline')")
    conn.execute(
        "INSERT INTO user_ratings(game_id, status, sort_order, series_id, series_order, series_source) "
        "VALUES(1, 'playing', 7, 1, 3, 'catalog')")
    conn.commit()
    return conn


def test_existing_db_ends_with_no_series_and_writes_still_work(tmp_path):
    """The full retire path (rebuild + drop) must leave NO series table/columns and
    keep user_ratings writable — a leftover series FK bricks every insert."""
    db = tmp_path / "old.db"
    conn = _seed_old_series_schema(db)

    models._rebuild_user_ratings_without_series(conn)
    models.migrate_drop_series(conn)
    # idempotent second pass
    models._rebuild_user_ratings_without_series(conn)
    models.migrate_drop_series(conn)

    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert 'series' not in tables

    ur_cols = {r[1] for r in conn.execute("PRAGMA table_info(user_ratings)")}
    assert not ({'series_id', 'series_order', 'series_source'} & ur_cols)
    games_cols = {r[1] for r in conn.execute("PRAGMA table_info(games)")}
    assert not ({'series_role', 'series_role_source'} & games_cols)
    slot_cols = {r[1] for r in conn.execute("PRAGMA table_info(slots)")}
    assert 'focus_series_id' not in slot_cols

    idx = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert 'idx_user_ratings_series_id' not in idx
    assert 'idx_user_ratings_status' in idx  # non-series index preserved

    # Surviving row data is intact.
    row = conn.execute("SELECT status, sort_order FROM user_ratings WHERE game_id=1").fetchone()
    assert row == ('playing', 7)

    # The bricking case: a fresh insert must succeed with FK enforcement on.
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("INSERT INTO games(id, title) VALUES(2, 'Doom')")
    conn.execute("INSERT INTO user_ratings(game_id, status) VALUES(2, 'backlog')")
    conn.commit()
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_fresh_install_never_creates_series(tmp_path, monkeypatch):
    """init_db + migrate_db on a brand-new DB must produce no series schema at all."""
    import app as app_module
    db_path = tmp_path / "fresh.db"
    monkeypatch.setattr(models, "DB_PATH", db_path)
    monkeypatch.setattr(app_module, "DB_PATH", db_path)
    app_module.ensure_db()
    conn = models.get_db()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert 'series' not in tables
    games_cols = {r[1] for r in conn.execute("PRAGMA table_info(games)")}
    assert not ({'series_role', 'series_role_source'} & games_cols)
    ur_cols = {r[1] for r in conn.execute("PRAGMA table_info(user_ratings)")}
    assert not ({'series_id', 'series_order', 'series_source'} & ur_cols)
    slot_cols = {r[1] for r in conn.execute("PRAGMA table_info(slots)")}
    assert 'focus_series_id' not in slot_cols
    idx = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert 'idx_user_ratings_series_id' not in idx
    conn.close()
