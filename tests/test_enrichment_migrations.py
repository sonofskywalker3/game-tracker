"""upc_review + upc_enrichment_state migrations: present, shaped, idempotent."""
import models


def _cols(conn, table):
    return {c[1] for c in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_upc_review_table_present_and_shaped(temp_db):
    conn = models.get_db()
    cols = _cols(conn, "upc_review")
    assert {"id", "game_id", "platform", "upc", "product_title",
            "cover_url", "status", "reason", "created_at"} <= cols
    conn.close()


def test_upc_enrichment_state_table_present(temp_db):
    conn = models.get_db()
    cols = _cols(conn, "upc_enrichment_state")
    assert {"id", "last_run_date", "last_run_count"} <= cols
    conn.close()


def test_status_check_constraint_rejects_bad_status(temp_db):
    import sqlite3
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('G', 'g')")
    gid = conn.execute("SELECT id FROM games WHERE normalized_title='g'").fetchone()[0]
    try:
        conn.execute(
            "INSERT INTO upc_review (game_id, platform, status) VALUES (?, 'Switch', 'bogus')",
            (gid,))
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised, "CHECK(status IN (...)) should reject an unknown status"
    conn.close()


def test_migrations_are_idempotent(temp_db):
    conn = models.get_db()
    models.migrate_upc_review(conn)          # second run is a no-op
    models.migrate_upc_enrichment_state(conn)
    assert _cols(conn, "upc_review")          # still present, no error
    assert _cols(conn, "upc_enrichment_state")
    conn.close()
