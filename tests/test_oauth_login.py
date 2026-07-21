"""Google OAuth callback + allowlist binding.

Authlib is FULLY mocked here (``oauth.verify_google_callback`` is patched) — no
network call and no real Google OAuth app is ever touched. The tests exercise the
allowlist gate, the session binding, and the clean OAuthError -> 401 error path.
"""
from unittest.mock import patch

import pytest

import app as flask_app
import identity


@pytest.fixture
def oauth_client(temp_db, monkeypatch):
    """Test client on a migrated temp DB with an email allowlist configured.

    No GOOGLE_OAUTH_* env is set, so the auth *gate* stays OFF — the callback
    route is exercised directly (allowlisting is orthogonal to the request gate).
    """
    monkeypatch.setenv("BACKLOGQUEST_OWNER_EMAIL", "owner@example.com")
    monkeypatch.setenv(
        "BACKLOGQUEST_ALLOWED_EMAILS", "owner@example.com,tester@example.com"
    )
    flask_app.app.config["TESTING"] = True
    return flask_app.app.test_client()


def test_callback_allowlisted_sets_session_user(oauth_client):
    with patch(
        "oauth.verify_google_callback",
        return_value={"sub": "s1", "email": "tester@example.com", "name": "T"},
    ):
        res = oauth_client.get("/auth/callback?code=x&state=y", follow_redirects=False)
    assert res.status_code in (302, 303)
    with oauth_client.session_transaction() as s:
        assert s["user_id"] and s["user_id"] != identity.OWNER_USER_ID


def test_callback_rejects_non_allowlisted(oauth_client):
    with patch(
        "oauth.verify_google_callback",
        return_value={"sub": "s2", "email": "stranger@example.com", "name": "X"},
    ):
        res = oauth_client.get("/auth/callback?code=x&state=y")
    assert res.status_code == 403
    with oauth_client.session_transaction() as s:
        assert "user_id" not in s


def test_callback_oauth_error_returns_401(oauth_client):
    """A verification/state/token failure surfaces a clean 401 with no session and
    no leaked stack trace."""
    import oauth

    with patch("oauth.verify_google_callback", side_effect=oauth.OAuthError("bad state")):
        res = oauth_client.get("/auth/callback?code=x&state=y")
    assert res.status_code == 401
    with oauth_client.session_transaction() as s:
        assert "user_id" not in s


def test_auth_disabled_allows_unauthenticated_request(client, monkeypatch):
    """Regression: with no GOOGLE_OAUTH_* env the gate is a no-op, so a normal
    route is reachable without a session (owner fallback keeps the suite green)."""
    for var in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    assert flask_app.auth.auth_enabled() is False
    assert client.get("/api/stats").status_code == 200
