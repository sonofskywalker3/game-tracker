"""enrich_game writes HLTB durations to the games row; batch skips already-enriched."""
from unittest.mock import patch

import models
import hltb


def _seed_game(conn, title):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_enrich_game_writes_durations(temp_db):
    conn = models.get_db()
    gid = _seed_game(conn, "Hades")
    conn.commit()
    fake = {"hltb_id": "68151", "hltb_main_minutes": 360,
            "hltb_main_extra_minutes": 1320, "hltb_completionist_minutes": 5460}
    with patch("hltb.fetch_durations", return_value=fake):
        ok = hltb.enrich_game(conn, gid)
    assert ok is True
    row = conn.execute("SELECT hltb_id, hltb_main_minutes FROM games WHERE id=?", (gid,)).fetchone()
    assert row["hltb_id"] == "68151"
    assert row["hltb_main_minutes"] == 360
    conn.close()


def test_enrich_game_no_match_leaves_nulls(temp_db):
    conn = models.get_db()
    gid = _seed_game(conn, "Nope")
    conn.commit()
    with patch("hltb.fetch_durations", return_value=None):
        ok = hltb.enrich_game(conn, gid)
    assert ok is False
    row = conn.execute("SELECT hltb_main_minutes FROM games WHERE id=?", (gid,)).fetchone()
    assert row["hltb_main_minutes"] is None
    conn.close()


def test_enrich_missing_skips_already_enriched(temp_db):
    conn = models.get_db()
    g1 = _seed_game(conn, "Hades")
    g2 = _seed_game(conn, "Celeste")
    conn.execute("UPDATE games SET hltb_id='x' WHERE id=?", (g1,))  # already enriched
    conn.commit()
    calls = []
    def fake_enrich(c, gid):
        calls.append(gid)
        return True
    with patch("hltb.enrich_game", side_effect=fake_enrich):
        hltb.enrich_missing(conn)
    assert calls == [g2]  # only the un-enriched game
    conn.close()
