# tests/test_auth_gate.py
from unittest.mock import patch

import pytest

import oauth


@pytest.fixture
def secure_env(monkeypatch):
    """Turn the auth gate ON by configuring Google OAuth, with a real session
    secret and an API bearer token for the native-client path."""
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("BACKLOGQUEST_API_TOKEN", "apitoken")
    monkeypatch.setenv("BACKLOGQUEST_SESSION_SECRET", "test-secret")
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
    # No OAuth env in dev/tests -> app behaves as today (no redirect).
    assert client.get("/api/stats").status_code == 200


def test_api_blocked_without_auth(client, secure_env):
    res = client.get("/api/stats")
    assert res.status_code == 401


def test_html_redirects_to_login(client, secure_env):
    res = client.get("/")
    assert res.status_code == 302
    assert "/login" in res.headers["Location"]


def test_callback_is_public_under_gate(client, secure_env):
    # /auth/callback must bypass the gate or OAuth login could never complete.
    # Authlib is mocked to fail verification -> clean 401 (not a redirect to
    # /login), proving the gate let the request through to the handler.
    with patch("oauth.verify_google_callback", side_effect=oauth.OAuthError("x")):
        res = client.get("/auth/callback?code=x&state=y")
    assert res.status_code == 401


def test_session_user_grants_access(client, secure_env):
    # A bound web session (set by the OAuth callback) carries past the gate.
    with client.session_transaction() as s:
        s["user_id"] = 1
    assert client.get("/api/stats").status_code == 200


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


def test_check_oauth_config_rejects_half_configured(monkeypatch):
    import app as app_module
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    with pytest.raises(RuntimeError):
        app_module.check_oauth_config()


def test_check_oauth_config_allows_both_or_neither(monkeypatch):
    import app as app_module
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    app_module.check_oauth_config()  # neither -> ok
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csecret")
    app_module.check_oauth_config()  # both -> ok
