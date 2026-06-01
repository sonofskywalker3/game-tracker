import models


def _add_game(title):
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO user_ratings (game_id, status) VALUES (?, 'backlog')", (gid,))
    conn.commit()
    conn.close()
    return gid


def test_put_sets_time_to_beat_override(client):
    gid = _add_game("Tunic")
    assert client.put(f"/api/games/{gid}", json={"time_to_beat_override_minutes": 660}).status_code == 200
    data = client.get(f"/api/games/{gid}").get_json()
    assert data["time_to_beat_override_minutes"] == 660


def test_put_clears_time_to_beat_override_with_null(client):
    gid = _add_game("Tunic")
    client.put(f"/api/games/{gid}", json={"time_to_beat_override_minutes": 660})
    client.put(f"/api/games/{gid}", json={"time_to_beat_override_minutes": None})
    assert client.get(f"/api/games/{gid}").get_json()["time_to_beat_override_minutes"] is None


def test_get_game_includes_hltb_main(client):
    gid = _add_game("Tunic")
    conn = models.get_db()
    conn.execute("UPDATE games SET hltb_main_minutes=720 WHERE id=?", (gid,))
    conn.commit()
    conn.close()
    assert client.get(f"/api/games/{gid}").get_json()["hltb_main_minutes"] == 720
