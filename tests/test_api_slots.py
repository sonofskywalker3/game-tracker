def test_get_slots_returns_four(client):
    resp = client.get("/api/slots")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["slots"]) == 4
    assert "candidates" in data["slots"][0]


def test_create_slot(client):
    resp = client.post("/api/slots", json={
        "label": "Deck · Anywhere", "platforms": ["Steam"],
        "max_session_minutes": 45, "streamable_only": 0,
        "context_notes": "handheld in bed"})
    assert resp.status_code == 201
    assert len(client.get("/api/slots").get_json()["slots"]) == 5


def test_patch_slot(client):
    sid = client.get("/api/slots").get_json()["slots"][0]["id"]
    resp = client.patch(f"/api/slots/{sid}", json={"label": "Renamed"})
    assert resp.status_code == 200
    labels = [s["label"] for s in client.get("/api/slots").get_json()["slots"]]
    assert "Renamed" in labels


def test_patch_slot_streamable_only(client):
    sid = client.get("/api/slots").get_json()["slots"][0]["id"]
    resp = client.patch(f"/api/slots/{sid}", json={"streamable_only": 1})
    assert resp.status_code == 200
    slot = next(s for s in client.get("/api/slots").get_json()["slots"] if s["id"] == sid)
    assert slot["streamable_only"] == 1


def test_delete_slot(client):
    sid = client.get("/api/slots").get_json()["slots"][0]["id"]
    resp = client.delete(f"/api/slots/{sid}")
    assert resp.status_code == 200
    assert len(client.get("/api/slots").get_json()["slots"]) == 3
