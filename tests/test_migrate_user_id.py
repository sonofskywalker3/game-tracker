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
