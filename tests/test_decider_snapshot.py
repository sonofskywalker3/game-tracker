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


def test_snapshot_platforms_alphabetical_with_dash_fallback(temp_db):
    """Locks the exact platform formatting so the single-query rewrite stays
    byte-identical (the snapshot is a cached prompt prefix)."""
    conn = models.get_db()
    gid = _add(conn, "Multiplat")
    for sn in ("Switch", "PS3"):
        pid = conn.execute("SELECT id FROM platforms WHERE short_name=?", (sn,)).fetchone()[0]
        conn.execute("INSERT INTO game_platforms (game_id, platform_id) VALUES (?, ?)",
                     (gid, pid))
    _add(conn, "No Platform Game")
    conn.commit()
    snap = decider.build_library_snapshot(conn)
    multi = next(ln for ln in snap.splitlines() if "Multiplat" in ln)
    assert "plat:PS3/Switch" in multi
    bare_line = next(ln for ln in snap.splitlines() if "No Platform Game" in ln)
    assert "plat:- " in bare_line or "plat:- |" in bare_line
    conn.close()


def test_snapshot_is_deterministic(temp_db):
    conn = models.get_db()
    _add(conn, "Hades")
    _add(conn, "Celeste")
    assert decider.build_library_snapshot(conn) == decider.build_library_snapshot(conn)
    conn.close()
