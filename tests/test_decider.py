"""Tests for completionist-aware suppression and slot-context in decider.py."""
import models
import decider


def _add_game(conn, title: str, status: str) -> int:
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO user_ratings (game_id, status) VALUES (?, ?)", (gid, status))
    conn.commit()
    return gid


def test_normal_slot_suppresses_completed(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Beaten Game", "completed")
    suppressed = decider._suppressed_suggestion_ids(conn, [{"role": "user", "content": "what next"}])
    assert gid in suppressed
    conn.close()


def test_completionist_slot_allows_completed(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Beaten Game", "completed")
    suppressed = decider._suppressed_suggestion_ids(
        conn, [{"role": "user", "content": "what next"}], completionist=True)
    assert gid not in suppressed
    conn.close()


def test_completionist_slot_still_suppresses_100_and_dropped(temp_db):
    conn = models.get_db()
    plat = _add_game(conn, "Platinumed", "100")
    bail = _add_game(conn, "Bailed", "dropped")
    suppressed = decider._suppressed_suggestion_ids(
        conn, [{"role": "user", "content": "what next"}], completionist=True)
    assert plat in suppressed
    assert bail in suppressed
    conn.close()


def test_replay_intent_lifts_dropped_suppression(temp_db):
    """INSTRUCTIONS permit recommending a dropped game on explicit replay intent;
    the backstop must agree or the reply prose promises chips that never render."""
    conn = models.get_db()
    bail = _add_game(conn, "Anthem", "dropped")
    suppressed = decider._suppressed_suggestion_ids(
        conn, [{"role": "user", "content": "I want to replay Anthem, I dropped it too early"}])
    assert bail not in suppressed
    conn.close()


def test_completionist_slot_context_names_real_status(temp_db):
    """The slot context must quote the status string the snapshot actually emits
    ('completed'), not 'complete', and explicitly override the global never-rule."""
    conn = models.get_db()
    text = decider.build_slot_context(conn, {"label": "Grind", "completionist": 1})
    assert "'completed'" in text
    # no bare 'complete' quote left behind
    assert "'complete'" not in text.replace("'completed'", "")
    conn.close()


def test_slot_context_mentions_completionist(temp_db):
    conn = models.get_db()
    text = decider.build_slot_context(conn, {"label": "Grind", "completionist": 1})
    assert "Completionist" in text
    conn.close()


def test_slot_context_omits_completionist_when_off(temp_db):
    conn = models.get_db()
    text = decider.build_slot_context(conn, {"label": "Quick", "completionist": 0})
    assert "Completionist" not in text
    conn.close()
