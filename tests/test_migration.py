import sqlite3

from models import migrate_platform_category


def _old_schema_conn():
    """A platforms table WITHOUT the category column (pre-migration shape)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE platforms (id INTEGER PRIMARY KEY, name TEXT, short_name TEXT)"
    )
    conn.executemany(
        "INSERT INTO platforms (name, short_name) VALUES (?, ?)",
        [("PlayStation 4", "PS4"), ("PC", "PC"), ("PlayStation 3", "PS3")],
    )
    return conn


def test_migration_adds_column_and_backfills():
    conn = _old_schema_conn()
    migrate_platform_category(conn)
    cats = {r["short_name"]: r["category"]
            for r in conn.execute("SELECT short_name, category FROM platforms")}
    assert cats == {"PS4": "modern_console", "PC": "pc", "PS3": "legacy_console"}


def test_migration_is_idempotent():
    conn = _old_schema_conn()
    migrate_platform_category(conn)
    first = {r["short_name"]: r["category"]
             for r in conn.execute("SELECT short_name, category FROM platforms")}
    migrate_platform_category(conn)  # second run must not error or change values
    second = {r["short_name"]: r["category"]
              for r in conn.execute("SELECT short_name, category FROM platforms")}
    assert first == second
