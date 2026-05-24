import sqlite3

import models


def test_init_creates_dlc_schema(temp_db):
    conn = models.get_db()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "dlc" in tables
    cols = {c[1] for c in conn.execute("PRAGMA table_info(games)")}
    assert "igdb_id" in cols
    conn.close()


def test_migrate_dlc_adds_to_legacy_db():
    # A bare DB with a games table missing igdb_id and no dlc table.
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE games (id INTEGER PRIMARY KEY, title TEXT)")
    models.migrate_dlc(conn)
    cols = {c[1] for c in conn.execute("PRAGMA table_info(games)")}
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "igdb_id" in cols and "dlc" in tables
    # idempotent: a second run does not error
    models.migrate_dlc(conn)
    conn.close()
