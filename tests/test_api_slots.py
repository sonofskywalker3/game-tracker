import models


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


def _add_backlog_game(title):
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO user_ratings (game_id, status) VALUES (?, 'backlog')", (gid,))
    conn.commit()
    conn.close()
    return gid


def test_pin_and_outcome_flow(client):
    gid = _add_backlog_game("Hades")
    sid = client.get("/api/slots").get_json()["slots"][0]["id"]
    assert client.post(f"/api/slots/{sid}/pin", json={"game_id": gid, "goal": "beat it"}).status_code == 200
    state = client.get("/api/slots").get_json()["slots"]
    pinned = next(s for s in state if s["id"] == sid)
    assert pinned["current_game"]["id"] == gid
    assert pinned["goal"] == "beat it"
    # complete it -> slot frees
    assert client.post(f"/api/slots/{sid}/outcome", json={"outcome": "complete"}).status_code == 200
    pinned = next(s for s in client.get("/api/slots").get_json()["slots"] if s["id"] == sid)
    assert pinned["current_game"] is None


def test_setting_finished_status_frees_slot(client):
    # Setting a game's status to a finished state from the game modal (PUT
    # /api/games/<id>) should drop it from any Pick slot it occupies, matching
    # the slate's own Complete/100%/Dropped buttons.
    gid = _add_backlog_game("Hades")
    sid = client.get("/api/slots").get_json()["slots"][0]["id"]
    client.post(f"/api/slots/{sid}/pin", json={"game_id": gid, "goal": "beat it"})
    assert client.put(f"/api/games/{gid}", json={"status": "100"}).status_code == 200
    pinned = next(s for s in client.get("/api/slots").get_json()["slots"] if s["id"] == sid)
    assert pinned["current_game"] is None


def test_setting_unfinished_status_keeps_slot(client):
    # A non-finishing status change (e.g. -> playing) must NOT free the slot.
    gid = _add_backlog_game("Celeste")
    sid = client.get("/api/slots").get_json()["slots"][0]["id"]
    client.post(f"/api/slots/{sid}/pin", json={"game_id": gid, "goal": "beat it"})
    assert client.put(f"/api/games/{gid}", json={"status": "playing"}).status_code == 200
    pinned = next(s for s in client.get("/api/slots").get_json()["slots"] if s["id"] == sid)
    assert pinned["current_game"]["id"] == gid


def test_edit_goal(client):
    gid = _add_backlog_game("Celeste")
    sid = client.get("/api/slots").get_json()["slots"][0]["id"]
    client.post(f"/api/slots/{sid}/pin", json={"game_id": gid, "goal": "beat"})
    assert client.patch(f"/api/slots/{sid}/goal", json={"goal": "C-sides"}).status_code == 200
    pinned = next(s for s in client.get("/api/slots").get_json()["slots"] if s["id"] == sid)
    assert pinned["goal"] == "C-sides"


def test_hltb_refresh_route(client, monkeypatch):
    import hltb
    monkeypatch.setattr(hltb, "enrich_missing", lambda conn: {"matched": 3, "missed": 1})
    resp = client.post("/api/hltb/refresh")
    assert resp.status_code == 200
    assert resp.get_json() == {"matched": 3, "missed": 1}


def test_patch_slot_prioritize_started(client):
    sid = client.get("/api/slots").get_json()["slots"][0]["id"]
    assert client.patch(f"/api/slots/{sid}", json={"prioritize_started": 0}).status_code == 200
    slot = next(s for s in client.get("/api/slots").get_json()["slots"] if s["id"] == sid)
    assert slot["prioritize_started"] == 0


def test_patch_slot_completionist(client):
    sid = client.get("/api/slots").get_json()["slots"][0]["id"]
    assert client.patch(f"/api/slots/{sid}", json={"completionist": 1}).status_code == 200
    slot = next(s for s in client.get("/api/slots").get_json()["slots"] if s["id"] == sid)
    assert slot["completionist"] == 1


def _add_switch_backlog_game(title):
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    pid = conn.execute("SELECT id FROM platforms WHERE short_name='Switch'").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id) VALUES (?, ?)", (gid, pid))
    conn.execute("INSERT INTO user_ratings (game_id, status) VALUES (?, 'backlog')", (gid,))
    conn.commit()
    conn.close()
    return gid


def test_dismiss_removes_from_candidates(client):
    gid = _add_switch_backlog_game("Dismissable")
    slots_data = client.get("/api/slots").get_json()["slots"]
    sid = next(s["id"] for s in slots_data if s["label"] == "Switch · Quick")
    # candidate before dismiss
    slot = next(s for s in slots_data if s["id"] == sid)
    assert any(c["game"]["id"] == gid for c in slot["candidates"])
    assert client.post(f"/api/slots/{sid}/dismiss", json={"game_id": gid}).status_code == 200
    slot2 = next(s for s in client.get("/api/slots").get_json()["slots"] if s["id"] == sid)
    assert all(c["game"]["id"] != gid for c in slot2["candidates"])
