# auth.py
"""Env-driven auth helpers for the single-user cloud deployment.

Auth is enforced ONLY when GAMETRACKER_PASSWORD_HASH is set; with it unset the
app behaves exactly as it did before hosting (keeps the local dev + test suite
unauthenticated). Identity is deliberately abstract here so a future multi-user
"Sign in with Google" (OIDC) path replaces these helpers without touching the
gate or the routes that call them.
"""
from __future__ import annotations

import os
from collections.abc import Mapping

from werkzeug.security import check_password_hash

_BEARER_PREFIX = "Bearer "


def _env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def auth_enabled() -> bool:
    """True when a password hash is configured (turns the whole gate on)."""
    return _env("GAMETRACKER_PASSWORD_HASH") is not None


def check_password(candidate: str) -> bool:
    """True iff auth is enabled and `candidate` matches the configured hash."""
    hashed = _env("GAMETRACKER_PASSWORD_HASH")
    if hashed is None:
        return False
    return check_password_hash(hashed, candidate)


def bearer_token(headers: Mapping[str, str]) -> str | None:
    """Extract the token from an `Authorization: Bearer <token>` header."""
    value = headers.get("Authorization", "")
    if value.startswith(_BEARER_PREFIX):
        token = value[len(_BEARER_PREFIX):].strip()
        return token or None
    return None


def is_authenticated(headers: Mapping[str, str], authed_session: bool) -> bool:
    """True for a logged-in web session or a valid API bearer token."""
    if authed_session:
        return True
    api_token = _env("GAMETRACKER_API_TOKEN")
    return api_token is not None and bearer_token(headers) == api_token


def is_import_authorized(headers: Mapping[str, str]) -> bool:
    """True for the scrape-push import token (API token also accepted)."""
    token = bearer_token(headers)
    if token is None:
        return False
    return token in {t for t in (_env("GAMETRACKER_IMPORT_TOKEN"),
                                 _env("GAMETRACKER_API_TOKEN")) if t is not None}


def cloud_mode() -> bool:
    """True when running as the cloud deployment (disables the in-app scraper)."""
    return _env("GAMETRACKER_CLOUD") == "1"
