import scrape_service


def _payload():
    return {"source": "xbox", "count": 1, "games": [
        {"title": "Halo Infinite", "platform": "Xbox", "source": "xbox",
         "external_id": "9ABC", "kind": "game"}]}


def test_import_pushed_runs_pipeline(client, monkeypatch):
    seen = {}
    def fake_pipeline(conn, vendor, games, *, store_resolvers=True, **kw):
        seen["vendor"] = vendor
        seen["store_resolvers"] = store_resolvers
        seen["n"] = len(games)
        return {"vendor": vendor, "new_games": len(games)}
    monkeypatch.setattr(scrape_service, "_run_pipeline", fake_pipeline)

    res = client.post("/api/import/scrape", json=_payload())
    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is True
    assert body["summary"]["new_games"] == 1
    assert seen == {"vendor": "xbox", "store_resolvers": False, "n": 1}


def test_import_scrape_rejects_unknown_source(client):
    res = client.post("/api/import/scrape", json={"source": "bogus", "games": []})
    assert res.status_code == 400


def test_import_scrape_rejects_missing_games(client):
    res = client.post("/api/import/scrape", json={"source": "xbox"})
    assert res.status_code == 400


def test_import_scrape_requires_import_token_when_secured(client, monkeypatch):
    from werkzeug.security import generate_password_hash
    monkeypatch.setenv("BACKLOGQUEST_PASSWORD_HASH", generate_password_hash("pw"))
    monkeypatch.setenv("BACKLOGQUEST_IMPORT_TOKEN", "imp")
    import app as app_module
    app_module.app.secret_key = "s"
    # No token -> blocked by the gate.
    assert client.post("/api/import/scrape", json=_payload()).status_code == 401
    # Correct import token -> allowed (pipeline stubbed to avoid real work).
    monkeypatch.setattr(scrape_service, "_run_pipeline",
                        lambda *a, **k: {"new_games": 0})
    ok = client.post("/api/import/scrape", json=_payload(),
                     headers={"Authorization": "Bearer imp"})
    assert ok.status_code == 200
