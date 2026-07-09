# tests/test_auth_gate.py
import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def secure_env(monkeypatch):
    monkeypatch.setenv("GAMETRACKER_PASSWORD_HASH", generate_password_hash("pw"))
    monkeypatch.setenv("GAMETRACKER_API_TOKEN", "apitoken")
    monkeypatch.setenv("GAMETRACKER_SESSION_SECRET", "test-secret")
    import app as app_module
    app_module.app.secret_key = "test-secret"
    return monkeypatch


def test_healthz_always_open(client):
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.get_json()["status"] == "ok"


def test_gate_off_when_unconfigured(client):
    # No password hash in env -> app behaves as today (no redirect).
    assert client.get("/api/stats").status_code == 200


def test_api_blocked_without_auth(client, secure_env):
    res = client.get("/api/stats")
    assert res.status_code == 401


def test_html_redirects_to_login(client, secure_env):
    res = client.get("/")
    assert res.status_code == 302
    assert "/login" in res.headers["Location"]


def test_login_with_password_grants_session(client, secure_env):
    res = client.post("/login", data={"password": "pw"}, follow_redirects=False)
    assert res.status_code == 302
    assert client.get("/api/stats").status_code == 200  # session cookie carried


def test_login_json_returns_token(client, secure_env):
    res = client.post("/login", json={"password": "pw"})
    assert res.status_code == 200
    assert res.get_json()["token"] == "apitoken"


def test_login_bad_password_401(client, secure_env):
    res = client.post("/login", json={"password": "nope"})
    assert res.status_code == 401


def test_api_token_grants_access(client, secure_env):
    res = client.get("/api/stats", headers={"Authorization": "Bearer apitoken"})
    assert res.status_code == 200
