"""collect() 3-tier ladder: config creds -> session token -> honest error;
userdata fetched after games with one cache-busted retry when empty."""
import base64
import json

import pytest

from scrapers import steam


def _jwt(claims: dict) -> str:
    seg = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{seg}.sig"


TOKEN_PAYLOAD = {"data": {"webapi_token": _jwt({"sub": "76561198012345678"})}}
GAMES_PAYLOAD = {"response": {"games": [{"appid": 620, "name": "Portal 2"}]}}
USERDATA_PAYLOAD = {"rgOwnedApps": [620, 730]}


class FakeResponse:
    def __init__(self, payload=None, status=200):
        self._payload, self.status, self.ok = payload, status, status == 200

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakePage:
    """Doubles page.request.get + wait_for_timeout; responses served per URL prefix, in order."""
    def __init__(self, routes: dict[str, list[FakeResponse]]):
        self._routes, self.calls, self.waits = routes, [], []
        self.request = self

    def get(self, url, params=None):
        self.calls.append((url, params))
        for prefix, queue in self._routes.items():
            if url.startswith(prefix):
                return queue.pop(0) if len(queue) > 1 else queue[0]
        raise AssertionError(f"unexpected URL: {url}")

    def wait_for_timeout(self, ms):
        self.waits.append(ms)


@pytest.fixture
def no_creds(monkeypatch):
    monkeypatch.setattr(steam.config, "get_steam_credentials", lambda: (None, None))


def test_session_path_returns_games_and_carriers(no_creds):
    page = FakePage({
        steam.TOKEN_CONFIG_URL: [FakeResponse(TOKEN_PAYLOAD)],
        steam.OWNED_GAMES_URL: [FakeResponse(GAMES_PAYLOAD)],
        steam.USERDATA_URL: [FakeResponse(USERDATA_PAYLOAD)],
    })
    got = steam.collect(page, captured=[])
    titles = [g.title for g in got if g.kind == "game"]
    carriers = [g.external_id for g in got if g.kind == "addon"]
    assert titles == ["Portal 2"] and carriers == ["620", "730"]
    owned_call = next(c for c in page.calls if c[0].startswith(steam.OWNED_GAMES_URL))
    assert owned_call[1]["access_token"] == TOKEN_PAYLOAD["data"]["webapi_token"]
    assert owned_call[1]["steamid"] == "76561198012345678"
    assert "key" not in owned_call[1]


def test_no_creds_no_session_raises_login_message(no_creds):
    page = FakePage({steam.TOKEN_CONFIG_URL: [FakeResponse(status=401)]})
    with pytest.raises(RuntimeError, match="Log into Steam in the browser window"):
        steam.collect(page, captured=[])


def test_token_present_but_unparseable_sub_raises(no_creds):
    page = FakePage({steam.TOKEN_CONFIG_URL: [FakeResponse({"data": {"webapi_token": "junk"}})]})
    with pytest.raises(RuntimeError, match="Log into Steam"):
        steam.collect(page, captured=[])


def test_session_owned_games_http_error_raises_with_status(no_creds):
    page = FakePage({
        steam.TOKEN_CONFIG_URL: [FakeResponse(TOKEN_PAYLOAD)],
        steam.OWNED_GAMES_URL: [FakeResponse(status=429)],
    })
    with pytest.raises(RuntimeError, match="429"):
        steam.collect(page, captured=[])


def test_userdata_empty_then_retry_with_cache_buster(no_creds):
    page = FakePage({
        steam.TOKEN_CONFIG_URL: [FakeResponse(TOKEN_PAYLOAD)],
        steam.OWNED_GAMES_URL: [FakeResponse(GAMES_PAYLOAD)],
        steam.USERDATA_URL: [FakeResponse({"rgOwnedApps": []}), FakeResponse(USERDATA_PAYLOAD)],
    })
    got = steam.collect(page, captured=[])
    assert [g.external_id for g in got if g.kind == "addon"] == ["620", "730"]
    userdata_calls = [u for u, _ in page.calls if u.startswith(steam.USERDATA_URL)]
    assert len(userdata_calls) == 2 and "?v=" in userdata_calls[1]
    assert page.waits  # slept between attempts


def test_userdata_empty_twice_is_nonfatal(no_creds):
    page = FakePage({
        steam.TOKEN_CONFIG_URL: [FakeResponse(TOKEN_PAYLOAD)],
        steam.OWNED_GAMES_URL: [FakeResponse(GAMES_PAYLOAD)],
        steam.USERDATA_URL: [FakeResponse({"rgOwnedApps": []}), FakeResponse({"rgOwnedApps": []})],
    })
    got = steam.collect(page, captured=[])
    assert [g.title for g in got] == ["Portal 2"]


def test_config_creds_path_short_circuits_session(monkeypatch):
    monkeypatch.setattr(steam.config, "get_steam_credentials", lambda: ("KEY", "7656"))

    def fake_get(url, params=None, timeout=None):
        assert params["key"] == "KEY" and params["steamid"] == "7656"
        return FakeResponse(GAMES_PAYLOAD)

    monkeypatch.setattr(FakeResponse, "raise_for_status", lambda self: None, raising=False)
    monkeypatch.setattr(steam.requests, "get", fake_get)
    page = FakePage({steam.USERDATA_URL: [FakeResponse(USERDATA_PAYLOAD)]})
    got = steam.collect(page, captured=[])
    assert [g.title for g in got if g.kind == "game"] == ["Portal 2"]
    assert not any(u.startswith(steam.TOKEN_CONFIG_URL) for u, _ in page.calls)


def test_progress_reports_base_game_count_only(no_creds):
    page = FakePage({
        steam.TOKEN_CONFIG_URL: [FakeResponse(TOKEN_PAYLOAD)],
        steam.OWNED_GAMES_URL: [FakeResponse(GAMES_PAYLOAD)],
        steam.USERDATA_URL: [FakeResponse(USERDATA_PAYLOAD)],
    })
    seen = []
    steam.collect(page, captured=[], progress=seen.append)
    assert seen == [1]
