import models
import decider


def _add(conn, title, **cols):
    conn.execute("INSERT INTO games (title, normalized_title, session_length) VALUES (?, ?, ?)",
                 (title, models.normalize_title(title), cols.get("session_length")))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO user_ratings (game_id, status, priority) VALUES (?, ?, ?)",
                 (gid, cols.get("status", "backlog"), cols.get("priority", 5)))
    conn.commit()
    return gid


def test_snapshot_includes_every_game_with_id_and_fields(temp_db):
    conn = models.get_db()
    gid = _add(conn, "Hades", session_length="short", status="playing", priority=8)
    snap = decider.build_library_snapshot(conn)
    line = next(ln for ln in snap.splitlines() if "Hades" in ln)
    assert f"#{gid}" in line
    assert "short" in line
    assert "playing" in line
    conn.close()


def test_snapshot_is_deterministic(temp_db):
    conn = models.get_db()
    _add(conn, "Hades")
    _add(conn, "Celeste")
    assert decider.build_library_snapshot(conn) == decider.build_library_snapshot(conn)
    conn.close()
