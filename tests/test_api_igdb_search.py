import app
import fetch_covers
import requests


def test_igdb_search_returns_year_and_igdb_url(client, monkeypatch):
    monkeypatch.setattr(app, "get_twitch_credentials", lambda: ("cid", "secret"))
    monkeypatch.setattr(fetch_covers, "get_access_token", lambda cid, sec: "tok")

    class FakeResp:
        def raise_for_status(self): pass
        def json(self):
            return [{"name": "Halo", "slug": "halo",
                     "cover": {"url": "//x/t_thumb/c.jpg"},
                     "platforms": [], "first_release_date": 1037750400}]  # 2002-11-20

    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResp())
    body = client.get("/api/igdb/search?q=halo").get_json()
    assert body[0]["year"] == 2002
    assert body[0]["igdb_url"] == "https://www.igdb.com/games/halo"
