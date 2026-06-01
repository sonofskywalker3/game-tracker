import models
import slots


def _add_game(conn, title, status="backlog"):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO user_ratings (game_id, status) VALUES (?, ?)", (gid, status))
    conn.commit()
    return gid


def _slot_id(conn, label):
    return conn.execute("SELECT id FROM slots WHERE label=?", (label,)).fetchone()[0]


def _status(conn, gid):
    return conn.execute("SELECT status FROM user_ratings WHERE game_id=?", (gid,)).fetchone()[0]


def test_pin_sets_current_game_and_goal(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Hades")
    sid = _slot_id(conn, "Switch · Quick")
    slots.pin_game(conn, sid, gid, "beat it")
    row = conn.execute("SELECT current_game_id, goal FROM slots WHERE id=?", (sid,)).fetchone()
    assert row["current_game_id"] == gid
    assert row["goal"] == "beat it"
    conn.close()


def test_beat_then_shelve_frees_slot_and_logs(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Hades")
    sid = _slot_id(conn, "Switch · Quick")
    slots.pin_game(conn, sid, gid, "beat it")
    slots.apply_outcome(conn, sid, "beat", chase=False)
    assert _status(conn, gid) == "completed"
    assert conn.execute("SELECT current_game_id FROM slots WHERE id=?", (sid,)).fetchone()[0] is None
    h = conn.execute("SELECT outcome FROM slot_history WHERE game_id=?", (gid,)).fetchone()
    assert h["outcome"] == "shelved"
    conn.close()


def test_beat_then_chase_keeps_slot_and_rewrites_goal(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Hades")
    sid = _slot_id(conn, "Switch · Quick")
    slots.pin_game(conn, sid, gid, "beat it")
    slots.apply_outcome(conn, sid, "beat", chase=True, new_goal="get the plat")
    row = conn.execute("SELECT current_game_id, goal FROM slots WHERE id=?", (sid,)).fetchone()
    assert row["current_game_id"] == gid          # stays slotted
    assert row["goal"] == "get the plat"
    assert _status(conn, gid) == "completed"
    assert conn.execute("SELECT COUNT(*) FROM slot_history WHERE game_id=?", (gid,)).fetchone()[0] == 0
    conn.close()


def test_complete_frees_slot_sets_100(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Hades")
    sid = _slot_id(conn, "Switch · Quick")
    slots.pin_game(conn, sid, gid, "100%")
    slots.apply_outcome(conn, sid, "complete")
    assert _status(conn, gid) == "100"
    assert conn.execute("SELECT outcome FROM slot_history WHERE game_id=?", (gid,)).fetchone()["outcome"] == "completed"
    conn.close()


def test_dropped_frees_slot_sets_dropped(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Hades")
    sid = _slot_id(conn, "Switch · Quick")
    slots.pin_game(conn, sid, gid, "beat it")
    slots.apply_outcome(conn, sid, "dropped")
    assert _status(conn, gid) == "dropped"
    assert conn.execute("SELECT outcome FROM slot_history WHERE game_id=?", (gid,)).fetchone()["outcome"] == "dropped"
    conn.close()


def test_swap_frees_slot_no_history_no_status_change(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Hades", status="playing")
    sid = _slot_id(conn, "Switch · Quick")
    slots.pin_game(conn, sid, gid, "beat it")
    slots.apply_outcome(conn, sid, "swap")
    assert conn.execute("SELECT current_game_id FROM slots WHERE id=?", (sid,)).fetchone()[0] is None
    assert _status(conn, gid) == "playing"   # unchanged
    assert conn.execute("SELECT COUNT(*) FROM slot_history WHERE game_id=?", (gid,)).fetchone()[0] == 0
    conn.close()


def test_get_slots_state_includes_current_game_and_candidates(temp_db):
    conn = models.get_db()
    state = slots.get_slots_state(conn)
    assert len(state) == 4
    assert "candidates" in state[0]
    assert "current_game" in state[0]
    conn.close()


def test_recently_finished_lists_outcomes(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Hades")
    sid = _slot_id(conn, "Switch · Quick")
    slots.pin_game(conn, sid, gid, "beat it")
    slots.apply_outcome(conn, sid, "complete")
    rf = slots.recently_finished(conn)
    assert rf[0]["title"] == "Hades"
    assert rf[0]["outcome"] == "completed"
    conn.close()
