import scrape_service

# The import endpoint is now scraper-token-only (owner-directed lockdown): a
# request must bear the import token even when the auth gate is off. These tests
# supply it so they exercise the pipeline behavior, not the token gate itself
# (the token gate has its own tests below).
_IMPORT_TOKEN = "test-import-tok"
_IMPORT_HEADERS = {"Authorization": f"Bearer {_IMPORT_TOKEN}"}


def _with_token(monkeypatch):
    monkeypatch.setenv("BACKLOGQUEST_IMPORT_TOKEN", _IMPORT_TOKEN)


def _payload():
    return {"source": "xbox", "count": 1, "games": [
        {"title": "Halo Infinite", "platform": "Xbox", "source": "xbox",
         "external_id": "9ABC", "kind": "game"}]}


def test_import_pushed_runs_pipeline(client, monkeypatch):
    _with_token(monkeypatch)
    seen = {}
    def fake_pipeline(conn, vendor, games, *, store_resolvers=True, **kw):
        seen["vendor"] = vendor
        seen["store_resolvers"] = store_resolvers
        seen["n"] = len(games)
        return {"vendor": vendor, "new_games": len(games)}
    monkeypatch.setattr(scrape_service, "_run_pipeline", fake_pipeline)

    res = client.post("/api/import/scrape", json=_payload(), headers=_IMPORT_HEADERS)
    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is True
    assert body["summary"]["new_games"] == 1
    assert seen == {"vendor": "xbox", "store_resolvers": False, "n": 1}


def test_import_scrape_rejects_unknown_source(client, monkeypatch):
    _with_token(monkeypatch)
    res = client.post("/api/import/scrape", json={"source": "bogus", "games": []},
                      headers=_IMPORT_HEADERS)
    assert res.status_code == 400


def test_import_scrape_rejects_missing_games(client, monkeypatch):
    _with_token(monkeypatch)
    res = client.post("/api/import/scrape", json={"source": "xbox"},
                      headers=_IMPORT_HEADERS)
    assert res.status_code == 400


def test_import_scrape_requires_import_token_when_secured(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csecret")
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
