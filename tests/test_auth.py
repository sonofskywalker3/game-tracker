# tests/test_auth.py
import pytest
import auth


@pytest.fixture
def env(monkeypatch):
    for var in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET",
                "BACKLOGQUEST_API_TOKEN", "BACKLOGQUEST_IMPORT_TOKEN",
                "BACKLOGQUEST_CLOUD"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def test_auth_disabled_when_no_oauth(env):
    assert auth.auth_enabled() is False


def test_auth_enabled_requires_both_oauth_creds(env):
    # Partial config is treated as disabled here (startup fails closed on it).
    env.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    assert auth.auth_enabled() is False
    env.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csecret")
    assert auth.auth_enabled() is True


def test_bearer_token_parsing(env):
    assert auth.bearer_token({"Authorization": "Bearer abc123"}) == "abc123"
    assert auth.bearer_token({"Authorization": "Basic abc123"}) is None
    assert auth.bearer_token({}) is None


def test_is_authenticated_session_or_api_token(env):
    env.setenv("BACKLOGQUEST_API_TOKEN", "tok")
    assert auth.is_authenticated({}, authed_session=True) is True
    assert auth.is_authenticated({"Authorization": "Bearer tok"}, authed_session=False) is True
    assert auth.is_authenticated({"Authorization": "Bearer nope"}, authed_session=False) is False
    assert auth.is_authenticated({}, authed_session=False) is False


def test_is_import_authorized_accepts_import_or_api_token(env):
    env.setenv("BACKLOGQUEST_IMPORT_TOKEN", "imp")
    env.setenv("BACKLOGQUEST_API_TOKEN", "api")
    assert auth.is_import_authorized({"Authorization": "Bearer imp"}) is True
    assert auth.is_import_authorized({"Authorization": "Bearer api"}) is True
    assert auth.is_import_authorized({"Authorization": "Bearer x"}) is False


def test_cloud_mode(env):
    assert auth.cloud_mode() is False
    env.setenv("BACKLOGQUEST_CLOUD", "1")
    assert auth.cloud_mode() is True
