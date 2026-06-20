import models


def test_barcode_cache_table_exists(temp_db):
    conn = models.get_db()
    cols = {row[1] for row in conn.execute("PRAGMA table_info(barcode_cache)")}
    conn.close()
    assert cols == {"upc", "igdb_id", "title", "platform", "game_id", "confirmed_at"}


def test_barcode_cache_upc_is_primary_key(temp_db):
    conn = models.get_db()
    conn.execute("INSERT INTO barcode_cache (upc, title) VALUES ('111', 'A')")
    conn.commit()
    # Second insert of same upc must violate the PK.
    import sqlite3
    try:
        conn.execute("INSERT INTO barcode_cache (upc, title) VALUES ('111', 'B')")
        conn.commit()
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    conn.close()
    assert raised
