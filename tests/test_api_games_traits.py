import models


def _make_game(client, title="Test Game"):
    resp = client.post("/api/games", json={"title": title})
    return resp.get_json()["game_id"]


def test_put_sets_session_length_manual(client):
    gid = _make_game(client)
    r = client.put(f"/api/games/{gid}", json={"session_length": "short"})
    assert r.status_code == 200
    g = client.get(f"/api/games/{gid}").get_json()
    assert g["session_length"] == "short"
    assert g["session_length_source"] == "manual"


def test_put_sets_series_role_manual(client):
    gid = _make_game(client)
    client.put(f"/api/games/{gid}", json={"series_role": "spinoff"})
    g = client.get(f"/api/games/{gid}").get_json()
    assert g["series_role"] == "spinoff"
    assert g["series_role_source"] == "manual"


def test_put_clears_trait_on_empty(client):
    gid = _make_game(client)
    client.put(f"/api/games/{gid}", json={"session_length": "long"})
    client.put(f"/api/games/{gid}", json={"session_length": ""})
    g = client.get(f"/api/games/{gid}").get_json()
    assert g["session_length"] is None
    assert g["session_length_source"] is None


def test_put_ignores_invalid_trait_value(client):
    gid = _make_game(client)
    client.put(f"/api/games/{gid}", json={"session_length": "bogus"})
    g = client.get(f"/api/games/{gid}").get_json()
    assert g["session_length"] is None  # invalid enum ignored, stays null


def test_get_returns_trait_fields(client):
    gid = _make_game(client)
    g = client.get(f"/api/games/{gid}").get_json()
    for key in ("session_length", "session_length_source", "series_role", "series_role_source"):
        assert key in g


def test_create_applies_catalog(monkeypatch, client):
    monkeypatch.setattr(models, "load_game_traits",
                        lambda: {"celeste": {"session_length": "short"}})
    gid = _make_game(client, title="Celeste")
    g = client.get(f"/api/games/{gid}").get_json()
    assert g["session_length"] == "short"
    assert g["session_length_source"] == "catalog"
