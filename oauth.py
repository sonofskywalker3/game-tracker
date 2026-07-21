"""Google OpenID Connect transport (Authlib).

Single responsibility: talk to Google. Identity resolution and the email
allowlist live in ``app.py``/``identity.py`` — this module only starts the
authorize redirect and turns a callback into verified claims. On any failure it
raises :class:`OAuthError` (the original cause is logged, never surfaced to the
client) so the caller can render a clean error without leaking a stack trace.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from authlib.integrations.base_client.errors import AuthlibBaseError as _AuthlibError
from authlib.integrations.flask_client import OAuth

log = logging.getLogger(__name__)

# Google's OIDC discovery document; Authlib fetches it lazily on first use and
# caches it — importing this module never makes a network call.
_GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
_GOOGLE_SCOPE = "openid email profile"

_oauth: OAuth | None = None


class OAuthError(Exception):
    """Raised when Google sign-in fails (bad state, token exchange, or claims)."""


def init_app(app: Any) -> None:
    """Register the Google OIDC client against the Flask app (idempotent).

    Safe to call when OAuth is unconfigured: the client is registered with empty
    creds and simply never used (the auth gate stays off in that case)."""
    global _oauth
    _oauth = OAuth(app)
    _oauth.register(
        name="google",
        client_id=os.environ.get("GOOGLE_OAUTH_CLIENT_ID"),
        client_secret=os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET"),
        server_metadata_url=_GOOGLE_DISCOVERY_URL,
        client_kwargs={"scope": _GOOGLE_SCOPE},
    )


def _client() -> Any:
    if _oauth is None:
        raise OAuthError("OAuth client not initialized")
    return _oauth.google


def authorize_redirect(redirect_uri: str) -> Any:
    """Begin the Google consent flow: a 302 to Google's authorize endpoint.

    Stores the CSRF ``state`` in the Flask session for the callback to verify."""
    try:
        return _client().authorize_redirect(redirect_uri)
    except _AuthlibError as exc:
        log.warning("OAuth authorize redirect failed: %s", exc)
        raise OAuthError("authorize redirect failed") from exc


def verify_google_callback(request: Any) -> dict[str, Any]:
    """Exchange the callback code for tokens and return verified identity claims.

    Returns ``{"sub", "email", "name"}``. Raises :class:`OAuthError` on any
    state/token/claims failure. ``request`` is accepted for a stable call site;
    Authlib reads the callback params from the Flask request/session context."""
    try:
        token = _client().authorize_access_token()
    except _AuthlibError as exc:
        log.warning("OAuth token exchange failed: %s", exc)
        raise OAuthError("token exchange failed") from exc

    userinfo = token.get("userinfo") if isinstance(token, dict) else None
    if not userinfo:
        try:
            userinfo = _client().userinfo(token=token)
        except _AuthlibError as exc:
            log.warning("OAuth userinfo fetch failed: %s", exc)
            raise OAuthError("userinfo fetch failed") from exc

    sub = userinfo.get("sub")
    email = userinfo.get("email")
    if not sub or not email:
        raise OAuthError("verified claims missing sub/email")
    return {"sub": sub, "email": email, "name": userinfo.get("name")}
