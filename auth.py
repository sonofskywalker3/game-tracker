# auth.py
"""Env-driven auth helpers for the multi-user cloud deployment.

The auth gate is enforced ONLY when Google OAuth is configured (both
GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET set); with them unset the
app behaves exactly as it did before hosting (keeps the local dev + test suite
unauthenticated, falling back to the owner identity). Web identity now comes from
"Sign in with Google" (OIDC — see oauth.py); the API bearer path is unchanged.
"""
from __future__ import annotations

import hmac
import os
from collections.abc import Mapping

_BEARER_PREFIX = "Bearer "


def _env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def auth_enabled() -> bool:
    """True when Google OAuth is configured (turns the whole gate on).

    Requires BOTH client id and secret; a partial config is treated as disabled
    here and rejected at startup (see app.check_oauth_config)."""
    return (
        _env("GOOGLE_OAUTH_CLIENT_ID") is not None
        and _env("GOOGLE_OAUTH_CLIENT_SECRET") is not None
    )


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
    api_token = _env("BACKLOGQUEST_API_TOKEN")
    tok = bearer_token(headers)
    return api_token is not None and tok is not None and hmac.compare_digest(tok, api_token)


def is_import_authorized(headers: Mapping[str, str]) -> bool:
    """True for the scrape-push import token (API token also accepted)."""
    tok = bearer_token(headers)
    if tok is None:
        return False
    for valid in (_env("BACKLOGQUEST_IMPORT_TOKEN"), _env("BACKLOGQUEST_API_TOKEN")):
        if valid is not None and hmac.compare_digest(tok, valid):
            return True
    return False


def cloud_mode() -> bool:
    """True when running as the cloud deployment (disables the in-app scraper)."""
    return _env("BACKLOGQUEST_CLOUD") == "1"
