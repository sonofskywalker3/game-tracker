import models


def test_stats_includes_dlc_counts(client):
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('G', 'g')")
    gid = conn.execute("SELECT id FROM games WHERE title='G'").fetchone()[0]
    conn.execute("INSERT INTO dlc (game_id, name, owned, source) VALUES (?, 'A', 1, 'igdb')", (gid,))
    conn.execute("INSERT INTO dlc (game_id, name, owned, source) VALUES (?, 'B', 0, 'igdb')", (gid,))
    conn.commit()
    conn.close()
    data = client.get("/api/stats").get_json()
    assert data["dlc_total"] == 2
    assert data["dlc_owned"] == 1
