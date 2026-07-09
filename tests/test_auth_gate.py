# tests/test_auth_gate.py
import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def secure_env(monkeypatch):
    monkeypatch.setenv("GAMETRACKER_PASSWORD_HASH", generate_password_hash("pw"))
    monkeypatch.setenv("GAMETRACKER_API_TOKEN", "apitoken")
    monkeypatch.setenv("GAMETRACKER_SESSION_SECRET", "test-secret")
    import app as app_module
    original = app_module.app.secret_key
    app_module.app.secret_key = "test-secret"
    yield monkeypatch
    app_module.app.secret_key = original


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


def test_check_session_secret_rejects_default_when_auth_enabled(secure_env):
    import app as app_module
    app_module.app.secret_key = "dev-insecure-secret"
    with pytest.raises(RuntimeError):
        app_module.check_session_secret()


def test_check_session_secret_allows_real_secret_when_auth_enabled(secure_env):
    import app as app_module
    app_module.app.secret_key = "a-real-secret"
    app_module.check_session_secret()  # must not raise


def test_check_session_secret_allows_default_when_auth_disabled(client):
    import app as app_module
    original = app_module.app.secret_key
    app_module.app.secret_key = "dev-insecure-secret"
    try:
        app_module.check_session_secret()  # must not raise: auth is off
    finally:
        app_module.app.secret_key = original


def test_login_sets_persistent_session_cookie(client, secure_env):
    res = client.post("/login", data={"password": "pw"}, follow_redirects=False)
    assert res.status_code == 302
    set_cookie_headers = res.headers.get_all("Set-Cookie")
    session_cookie = next(h for h in set_cookie_headers if h.startswith("session="))
    assert "Max-Age" in session_cookie or "Expires" in session_cookie
