"""POST /api/games supports legacy platforms and an optional physical flag.

Legacy platforms are seeded by init_db (via the client fixture), so a manually
added 3DS game can attach to one straight away.
"""


def test_create_game_with_legacy_platform(client):
    resp = client.post("/api/games", json={"title": "Mario Kart 7", "platforms": ["3DS"]})
    assert resp.status_code == 201
    gid = resp.get_json()["game_id"]

    game = client.get(f"/api/games/{gid}").get_json()
    shorts = [p["short_name"] for p in game["platforms"]]
    assert "3DS" in shorts


def test_create_game_marks_physical(client):
    resp = client.post(
        "/api/games",
        json={"title": "Fire Emblem Awakening", "platforms": ["3DS"], "physical": True},
    )
    assert resp.status_code == 201
    gid = resp.get_json()["game_id"]

    game = client.get(f"/api/games/{gid}").get_json()
    assert any(t["name"] == "Physical" for t in game["tags"])


def test_create_game_not_physical_by_default(client):
    resp = client.post(
        "/api/games", json={"title": "Some Digital 3DS Game", "platforms": ["3DS"]}
    )
    assert resp.status_code == 201
    gid = resp.get_json()["game_id"]

    game = client.get(f"/api/games/{gid}").get_json()
    assert not any(t["name"] == "Physical" for t in game.get("tags", []))
