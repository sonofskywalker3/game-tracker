# tests/test_auth.py
import pytest
from werkzeug.security import generate_password_hash
import auth


@pytest.fixture
def env(monkeypatch):
    for var in ("GAMETRACKER_PASSWORD_HASH", "GAMETRACKER_API_TOKEN",
                "GAMETRACKER_IMPORT_TOKEN", "GAMETRACKER_CLOUD"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def test_auth_disabled_when_no_hash(env):
    assert auth.auth_enabled() is False
    assert auth.check_password("anything") is False


def test_check_password_matches_hash(env):
    env.setenv("GAMETRACKER_PASSWORD_HASH", generate_password_hash("hunter2"))
    assert auth.auth_enabled() is True
    assert auth.check_password("hunter2") is True
    assert auth.check_password("wrong") is False


def test_bearer_token_parsing(env):
    assert auth.bearer_token({"Authorization": "Bearer abc123"}) == "abc123"
    assert auth.bearer_token({"Authorization": "Basic abc123"}) is None
    assert auth.bearer_token({}) is None


def test_is_authenticated_session_or_api_token(env):
    env.setenv("GAMETRACKER_API_TOKEN", "tok")
    assert auth.is_authenticated({}, authed_session=True) is True
    assert auth.is_authenticated({"Authorization": "Bearer tok"}, authed_session=False) is True
    assert auth.is_authenticated({"Authorization": "Bearer nope"}, authed_session=False) is False
    assert auth.is_authenticated({}, authed_session=False) is False


def test_is_import_authorized_accepts_import_or_api_token(env):
    env.setenv("GAMETRACKER_IMPORT_TOKEN", "imp")
    env.setenv("GAMETRACKER_API_TOKEN", "api")
    assert auth.is_import_authorized({"Authorization": "Bearer imp"}) is True
    assert auth.is_import_authorized({"Authorization": "Bearer api"}) is True
    assert auth.is_import_authorized({"Authorization": "Bearer x"}) is False


def test_cloud_mode(env):
    assert auth.cloud_mode() is False
    env.setenv("GAMETRACKER_CLOUD", "1")
    assert auth.cloud_mode() is True
