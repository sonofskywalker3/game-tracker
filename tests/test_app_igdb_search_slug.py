"""GET /api/igdb/search returns slug + igdb_url for each result."""
from __future__ import annotations

from unittest.mock import patch

import pytest

import app as flask_app


class _MockResp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"http {self.status_code}")

    def json(self):
        return self._data


@pytest.fixture
def igdb_client(monkeypatch):
    """Flask test client + mocked Twitch credentials/token (no real network)."""
    flask_app.app.config["TESTING"] = True
    # Patch get_access_token and get_twitch_credentials
    monkeypatch.setattr("fetch_covers.get_access_token",
                        lambda *a, **k: "tok", raising=False)
    monkeypatch.setattr("app.get_twitch_credentials",
                        lambda: ("cid", "csec"), raising=False)
    with flask_app.app.test_client() as cl:
        yield cl


def test_search_includes_slug_and_igdb_url(igdb_client):
    mock_payload = [
        {"name": "Vampire Survivors", "slug": "vampire-survivors",
         "cover": {"url": "//images.igdb.com/.../t_thumb/abc.jpg"}}
    ]
    # Patch requests.post — the route does `import requests` inside the function
    with patch("requests.post", return_value=_MockResp(mock_payload)):
        res = igdb_client.get("/api/igdb/search?q=vampire")

    body = res.get_json()
    assert isinstance(body, list)
    assert body[0]["name"] == "Vampire Survivors"
    assert body[0]["slug"] == "vampire-survivors"
    assert body[0]["igdb_url"] == "https://www.igdb.com/games/vampire-survivors"
    assert body[0]["cover_url"].startswith("https://")


def test_search_maps_platforms_to_short_names(igdb_client):
    """IGDB platform ids are mapped to the short_names we model."""
    mock_payload = [
        {"name": "Mario Kart 7", "slug": "mario-kart-7", "platforms": [37]},  # 37 = 3DS
    ]
    with patch("requests.post", return_value=_MockResp(mock_payload)):
        res = igdb_client.get("/api/igdb/search?q=mario")
    assert res.get_json()[0]["platforms"] == ["3DS"]


def test_search_platforms_empty_when_absent(igdb_client):
    mock_payload = [{"name": "Xyz", "slug": "xyz"}]
    with patch("requests.post", return_value=_MockResp(mock_payload)):
        res = igdb_client.get("/api/igdb/search?q=xyz")
    assert res.get_json()[0]["platforms"] == []


def test_search_handles_missing_slug(igdb_client):
    """Some IGDB results may not have a slug; the route should still return
    `slug` (empty) and `igdb_url` (empty) keys, not omit them."""
    mock_payload = [
        {"name": "Some Game",
         "cover": {"url": "//images.igdb.com/.../t_thumb/x.jpg"}}
    ]
    with patch("requests.post", return_value=_MockResp(mock_payload)):
        res = igdb_client.get("/api/igdb/search?q=some")

    body = res.get_json()
    assert body[0]["slug"] == ""
    assert body[0]["igdb_url"] == ""
