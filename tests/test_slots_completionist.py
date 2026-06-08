import models
import slots


def _add_game(conn, title, status):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO user_ratings (game_id, status) VALUES (?, ?)", (gid, status))
    conn.commit()
    return gid


def _titles(candidates):
    return {c["game"]["title"] for c in candidates}


def test_normal_slot_excludes_completed(temp_db):
    conn = models.get_db()
    _add_game(conn, "Beaten Game", "completed")
    _add_game(conn, "Backlog Game", "backlog")
    cands = slots.rank_candidates(conn, {"id": None, "completionist": 0})
    assert "Backlog Game" in _titles(cands)
    assert "Beaten Game" not in _titles(cands)
    conn.close()


def test_completionist_slot_includes_completed(temp_db):
    conn = models.get_db()
    _add_game(conn, "Beaten Game", "completed")
    _add_game(conn, "Backlog Game", "backlog")
    cands = slots.rank_candidates(conn, {"id": None, "completionist": 1})
    assert "Beaten Game" in _titles(cands)
    assert "Backlog Game" in _titles(cands)
    conn.close()


def test_completionist_slot_still_excludes_100_and_dropped(temp_db):
    conn = models.get_db()
    _add_game(conn, "Platinumed", "100")
    _add_game(conn, "Bailed", "dropped")
    cands = slots.rank_candidates(conn, {"id": None, "completionist": 1})
    assert "Platinumed" not in _titles(cands)
    assert "Bailed" not in _titles(cands)
    conn.close()


def test_completionist_boosts_completed_with_reason(temp_db):
    conn = models.get_db()
    _add_game(conn, "Beaten Game", "completed")
    cands = slots.rank_candidates(conn, {"id": None, "completionist": 1})
    beaten = next(c for c in cands if c["game"]["title"] == "Beaten Game")
    assert beaten["score"] >= 50 + slots.COMPLETION_BOOST
    assert "Beaten — chase 100%" in beaten["reasons"]
    conn.close()
