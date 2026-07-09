import scrape_libraries


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
    def raise_for_status(self):
        pass
    def json(self):
        return self._payload


def test_push_scrape_posts_to_import_endpoint(monkeypatch):
    captured = {}
    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, json=json, headers=headers)
        return _FakeResponse({"success": True, "summary": {"new_games": 2}})
    monkeypatch.setattr(scrape_libraries.requests, "post", fake_post)

    payload = {"source": "xbox", "games": [{"title": "A"}]}
    result = scrape_libraries.push_scrape(payload, "https://games.example.org/", "tok")

    assert captured["url"] == "https://games.example.org/api/import/scrape"
    assert captured["headers"]["Authorization"] == "Bearer tok"
    assert captured["json"] == payload
    assert result["summary"]["new_games"] == 2
