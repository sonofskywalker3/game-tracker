"""GET /api/slots schedule enrichment (windows + active_now + rank)."""
import datetime

import slot_schedule
import models


def _slot(conn, label, sort_order):
    conn.execute("INSERT INTO slots (label, sort_order) VALUES (?, ?)", (label, sort_order))
    return conn.execute("SELECT id FROM slots WHERE label=?", (label,)).fetchone()[0]


def _window(conn, slot_id, days, start, end):
    conn.execute("INSERT INTO slot_schedule_window (slot_id, days, start_min, end_min) "
                 "VALUES (?, ?, ?, ?)", (slot_id, days, start, end))


def test_slots_payload_includes_windows_and_active_flags(client, monkeypatch):
    conn = models.get_db()
    # Clear seeded slots so the assertion is deterministic.
    conn.execute("DELETE FROM slots")
    lunch = _slot(conn, "Lunch", 0)
    _slot(conn, "Anytime", 1)
    _window(conn, lunch, 1 << 0, 720, 780)   # Monday 12:00-13:00
    conn.commit()
    conn.close()

    # Freeze "now" to Monday 12:30 (weekday 0, minute 750).
    monkeypatch.setattr(slot_schedule, "now_weekday_minute", lambda: (0, 750))

    data = client.get("/api/slots").get_json()
    by_label = {s["label"]: s for s in data["slots"]}
    assert by_label["Lunch"]["windows"][0]["start_min"] == 720
    assert by_label["Lunch"]["active_now"] is True
    assert by_label["Lunch"]["restrictiveness_rank"] == 0
    assert by_label["Anytime"]["active_now"] is True            # zero windows = anytime
    assert by_label["Anytime"]["restrictiveness_rank"] == 1     # less restrictive -> after


def test_inactive_slot_has_null_rank(client, monkeypatch):
    conn = models.get_db()
    conn.execute("DELETE FROM slots")
    lunch = _slot(conn, "Lunch", 0)
    _window(conn, lunch, 1 << 0, 720, 780)   # Monday 12:00-13:00
    conn.commit()
    conn.close()
    monkeypatch.setattr(slot_schedule, "now_weekday_minute", lambda: (0, 60))  # Mon 01:00
    data = client.get("/api/slots").get_json()
    lunch_slot = next(s for s in data["slots"] if s["label"] == "Lunch")
    assert lunch_slot["active_now"] is False
    assert lunch_slot["restrictiveness_rank"] is None


def test_now_weekday_minute_uses_given_datetime():
    dt = datetime.datetime(2026, 6, 25, 14, 30)  # a Thursday
    assert slot_schedule.now_weekday_minute(dt) == (3, 14 * 60 + 30)
