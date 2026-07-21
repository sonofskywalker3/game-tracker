import sqlite3
import models


def _legacy_games_db():
    """A pre-migration games table with the old single-column UNIQUE."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE games (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
        normalized_title TEXT NOT NULL, UNIQUE(normalized_title))""")
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('Celeste','celeste')")
    conn.commit()
    models.migrate_users(conn)
    return conn


def test_backfills_user_id_to_owner():
    conn = _legacy_games_db()
    models.migrate_add_user_id_games(conn)
    assert conn.execute("SELECT user_id FROM games WHERE normalized_title='celeste'").fetchone()["user_id"] == 1


def test_two_users_can_own_same_title():
    conn = _legacy_games_db()
    models.migrate_add_user_id_games(conn)
    conn.execute("INSERT INTO users (google_sub,email) VALUES ('s','t@e.com')")
    uid = conn.execute("SELECT id FROM users WHERE email='t@e.com'").fetchone()["id"]
    conn.execute("INSERT INTO games (title,normalized_title,user_id) VALUES ('Celeste','celeste',?)", (uid,))
    conn.commit()  # must NOT raise UNIQUE violation
    assert conn.execute("SELECT COUNT(*) c FROM games WHERE normalized_title='celeste'").fetchone()["c"] == 2


def test_idempotent():
    conn = _legacy_games_db()
    models.migrate_add_user_id_games(conn)
    models.migrate_add_user_id_games(conn)  # second run is a no-op, no raise


def _matured_games_db():
    """A real-world games table: day-one columns PLUS several later
    ALTER TABLE ... ADD COLUMN migrations layered on top (collection_name,
    needs_igdb_review, hltb_id). Reproduces the shape of the owner's live DB,
    where migrate_add_user_id_games runs long after those ALTERs happened."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE games (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        normalized_title TEXT NOT NULL,
        cover_url TEXT,
        metacritic_score INTEGER,
        opencritic_score INTEGER,
        igdb_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        psn_addons_synced_at TIMESTAMP,
        UNIQUE(normalized_title))""")
    conn.execute("ALTER TABLE games ADD COLUMN collection_name TEXT")
    conn.execute("ALTER TABLE games ADD COLUMN needs_igdb_review INTEGER NOT NULL DEFAULT 0")
    conn.execute("ALTER TABLE games ADD COLUMN hltb_id TEXT")
    conn.execute(
        "INSERT INTO games (title, normalized_title, collection_name, "
        "needs_igdb_review, hltb_id) VALUES "
        "('Celeste','celeste','Matt Makes Games Bundle',1,'12345')"
    )
    conn.commit()
    models.migrate_users(conn)
    return conn


def test_matured_db_with_alter_added_columns_survives_rebuild():
    """The rebuild must preserve every ALTER-added column, not just the
    original 9 from init_db(). This is the exact shape of the owner's real
    games.db and is what a fresh init_db()-created test DB never exercises."""
    conn = _matured_games_db()
    models.migrate_add_user_id_games(conn)

    row = conn.execute(
        "SELECT user_id, collection_name, needs_igdb_review, hltb_id "
        "FROM games WHERE normalized_title='celeste'"
    ).fetchone()
    assert row["user_id"] == 1
    assert row["collection_name"] == "Matt Makes Games Bundle"
    assert row["needs_igdb_review"] == 1
    assert row["hltb_id"] == "12345"

    tables = [
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]
    assert "games_new" not in tables
