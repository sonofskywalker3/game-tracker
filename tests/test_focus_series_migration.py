import models


def test_migrate_slots_adds_focus_series_id(temp_db):
    conn = models.get_db()
    cols = {c[1] for c in conn.execute("PRAGMA table_info(slots)").fetchall()}
    assert "focus_series_id" in cols
    conn.close()


def test_migrate_slots_focus_series_idempotent(temp_db):
    conn = models.get_db()
    models.migrate_slots(conn)
    models.migrate_slots(conn)  # must not raise
    cols = {c[1] for c in conn.execute("PRAGMA table_info(slots)").fetchall()}
    assert "focus_series_id" in cols
    conn.close()


def test_focus_series_id_defaults_null_and_accepts_value(temp_db):
    conn = models.get_db()
    conn.execute("INSERT INTO series (name) VALUES ('Zelda')")
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO slots (label, focus_series_id) VALUES ('S', ?)", (sid,))
    conn.execute("INSERT INTO slots (label) VALUES ('T')")
    conn.commit()
    rows = {r["label"]: r["focus_series_id"]
            for r in conn.execute("SELECT label, focus_series_id FROM slots").fetchall()}
    assert rows["S"] == sid
    assert rows["T"] is None
    conn.close()
