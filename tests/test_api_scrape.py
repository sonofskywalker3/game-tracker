import scrape_service


def test_scrape_status_shape(client):
    data = client.get("/api/scrape/status").get_json()
    assert "phase" in data


def test_scrape_start_bad_vendor(client):
    res = client.post("/api/scrape/start", json={"vendor": "bogus"})
    assert res.status_code == 400


def test_scrape_start_ok(client, monkeypatch):
    monkeypatch.setattr(scrape_service, "start", lambda v: (True, "started"))
    res = client.post("/api/scrape/start", json={"vendor": "xbox"})
    assert res.status_code == 200
    assert res.get_json()["success"] is True


def test_scrape_start_conflict(client, monkeypatch):
    monkeypatch.setattr(scrape_service, "start", lambda v: (False, "already running"))
    res = client.post("/api/scrape/start", json={"vendor": "xbox"})
    assert res.status_code == 409


def test_scrape_continue_and_cancel(client, monkeypatch):
    calls = {"continue": 0, "cancel": 0}
    monkeypatch.setattr(scrape_service, "signal_continue",
                        lambda: calls.__setitem__("continue", calls["continue"] + 1))
    monkeypatch.setattr(scrape_service, "cancel",
                        lambda: calls.__setitem__("cancel", calls["cancel"] + 1))
    assert client.post("/api/scrape/continue").status_code == 200
    assert client.post("/api/scrape/cancel").status_code == 200
    assert calls == {"continue": 1, "cancel": 1}


def test_scrape_disabled_in_cloud_mode(client, monkeypatch):
    monkeypatch.setenv("BACKLOGQUEST_CLOUD", "1")
    assert client.post("/api/scrape/start", json={"vendor": "xbox"}).status_code == 409
    assert client.post("/api/scrape/continue").status_code == 409
    assert client.post("/api/scrape/cancel").status_code == 409
    assert client.get("/api/scrape/status").get_json()["phase"] == "disabled"
