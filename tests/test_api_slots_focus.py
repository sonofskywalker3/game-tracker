import models


def _series(client, name="Zelda"):
    conn = models.get_db()
    conn.execute("INSERT INTO series (name) VALUES (?)", (name,))
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return sid


def _slot_focus(label):
    conn = models.get_db()
    row = conn.execute("SELECT focus_series_id FROM slots WHERE label=?", (label,)).fetchone()
    conn.close()
    return row["focus_series_id"] if row else None


def test_create_slot_persists_focus_series_id(client):
    sid = _series(client)
    client.post("/api/slots", json={"label": "Focus Slot", "platforms": [], "focus_series_id": sid})
    assert _slot_focus("Focus Slot") == sid


def test_patch_slot_sets_and_clears_focus_series_id(client):
    sid = _series(client)
    # The seed slot "Switch · Quick" exists from seed_default_slots.
    conn = models.get_db()
    slot_id = conn.execute("SELECT id FROM slots WHERE label='Switch · Quick'").fetchone()["id"]
    conn.close()
    client.patch(f"/api/slots/{slot_id}", json={"focus_series_id": sid})
    assert _slot_focus("Switch · Quick") == sid
    client.patch(f"/api/slots/{slot_id}", json={"focus_series_id": None})
    assert _slot_focus("Switch · Quick") is None
