import models


def test_games_has_psn_addons_synced_at(temp_db):
    conn = models.get_db()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(games)")}
    conn.close()
    assert "psn_addons_synced_at" in cols


def test_psn_marker_defaults_null(temp_db):
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('G', 'g')")
    val = conn.execute("SELECT psn_addons_synced_at FROM games WHERE title='G'").fetchone()[0]
    conn.close()
    assert val is None
