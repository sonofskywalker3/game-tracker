"""slot_schedule_window + user_profile migrations (uses temp_db)."""
import models


def test_schedule_window_table_exists_and_cascades(temp_db):
    conn = models.get_db()
    # insert a slot, then a window for it
    conn.execute("INSERT INTO slots (label, sort_order) VALUES ('S', 0)")
    sid = conn.execute("SELECT id FROM slots").fetchone()[0]
    conn.execute(
        "INSERT INTO slot_schedule_window (slot_id, days, start_min, end_min) "
        "VALUES (?, ?, ?, ?)", (sid, 0b0011111, 720, 780))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM slot_schedule_window").fetchone()[0] == 1
    # deleting the slot cascades the window away
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("DELETE FROM slots WHERE id = ?", (sid,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM slot_schedule_window").fetchone()[0] == 0
    conn.close()


def test_user_profile_seeded_single_row(temp_db):
    conn = models.get_db()
    rows = conn.execute("SELECT id, work_start_min, bed_time_min FROM user_profile").fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == 1 and rows[0]["work_start_min"] is None
    conn.close()


def test_migrations_are_idempotent(temp_db):
    conn = models.get_db()
    # running again must not throw or duplicate the seed row
    models.migrate_slot_schedule_window(conn)
    models.migrate_user_profile(conn)
    assert conn.execute("SELECT COUNT(*) FROM user_profile").fetchone()[0] == 1
    conn.close()
