"""Slot schedule-window CRUD endpoints."""
import models


def _slot(conn):
    conn.execute("INSERT INTO slots (label, sort_order) VALUES ('S', 0)")
    return conn.execute("SELECT id FROM slots WHERE label='S'").fetchone()[0]


def test_create_window(client):
    conn = models.get_db()
    sid = _slot(conn)
    conn.commit()
    conn.close()
    r = client.post(f"/api/slots/{sid}/windows",
                    json={"days": 0b0011111, "start_min": 720, "end_min": 780})
    assert r.status_code == 201
    wid = r.get_json()["id"]
    conn = models.get_db()
    row = conn.execute("SELECT days, start_min, end_min FROM slot_schedule_window "
                       "WHERE id=?", (wid,)).fetchone()
    assert (row["days"], row["start_min"], row["end_min"]) == (0b0011111, 720, 780)
    conn.close()


def test_create_window_missing_slot_404(client):
    r = client.post("/api/slots/99999/windows",
                    json={"days": 1, "start_min": 0, "end_min": 60})
    assert r.status_code == 404


def test_create_window_rejects_equal_start_end(client):
    conn = models.get_db()
    sid = _slot(conn)
    conn.commit()
    conn.close()
    r = client.post(f"/api/slots/{sid}/windows",
                    json={"days": 1, "start_min": 600, "end_min": 600})
    assert r.status_code == 400


def test_create_window_rejects_out_of_range(client):
    conn = models.get_db()
    sid = _slot(conn)
    conn.commit()
    conn.close()
    r = client.post(f"/api/slots/{sid}/windows",
                    json={"days": 999, "start_min": 0, "end_min": 60})
    assert r.status_code == 400


def test_update_window(client):
    conn = models.get_db()
    sid = _slot(conn)
    conn.execute("INSERT INTO slot_schedule_window (slot_id, days, start_min, end_min) "
                 "VALUES (?, 1, 0, 60)", (sid,))
    wid = conn.execute("SELECT id FROM slot_schedule_window").fetchone()[0]
    conn.commit()
    conn.close()
    r = client.put(f"/api/slots/{sid}/windows/{wid}",
                   json={"days": 0b1100000, "start_min": 480, "end_min": 600})
    assert r.status_code == 200
    conn = models.get_db()
    row = conn.execute("SELECT days, start_min FROM slot_schedule_window WHERE id=?",
                       (wid,)).fetchone()
    assert (row["days"], row["start_min"]) == (0b1100000, 480)
    conn.close()


def test_update_window_wrong_slot_404(client):
    conn = models.get_db()
    sid = _slot(conn)
    conn.commit()
    conn.close()
    r = client.put(f"/api/slots/{sid}/windows/99999",
                   json={"days": 1, "start_min": 0, "end_min": 60})
    assert r.status_code == 404


def test_delete_window(client):
    conn = models.get_db()
    sid = _slot(conn)
    conn.execute("INSERT INTO slot_schedule_window (slot_id, days, start_min, end_min) "
                 "VALUES (?, 1, 0, 60)", (sid,))
    wid = conn.execute("SELECT id FROM slot_schedule_window").fetchone()[0]
    conn.commit()
    conn.close()
    r = client.delete(f"/api/slots/{sid}/windows/{wid}")
    assert r.status_code == 200
    conn = models.get_db()
    assert conn.execute("SELECT COUNT(*) FROM slot_schedule_window").fetchone()[0] == 0
    conn.close()


def test_delete_window_missing_404(client):
    conn = models.get_db()
    sid = _slot(conn)
    conn.commit()
    conn.close()
    r = client.delete(f"/api/slots/{sid}/windows/99999")
    assert r.status_code == 404
