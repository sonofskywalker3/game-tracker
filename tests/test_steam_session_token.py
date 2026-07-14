"""Pure helpers for the Steam session-token path (mint token from logged-in session)."""
import base64
import json

from scrapers import steam


def _jwt(claims: dict) -> str:
    """Build an unsigned JWT-shaped token: header.payload.sig (payload is what matters)."""
    seg = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{seg}.sig"


def test_parse_webapi_token_present():
    assert steam.parse_webapi_token({"data": {"webapi_token": "tok123"}}) == "tok123"


def test_parse_webapi_token_absent_or_malformed():
    assert steam.parse_webapi_token({}) == ""
    assert steam.parse_webapi_token({"data": {}}) == ""
    assert steam.parse_webapi_token({"data": "nope"}) == ""
    assert steam.parse_webapi_token({"data": {"webapi_token": 42}}) == ""
    assert steam.parse_webapi_token(None) == ""


def test_steamid_from_token_valid_jwt():
    token = _jwt({"sub": "76561198012345678", "aud": ["web:store"]})
    assert steam.steamid_from_token(token) == "76561198012345678"


def test_steamid_from_token_payload_needs_padding():
    # A short claims dict whose base64 length is not a multiple of 4 once '=' is stripped.
    token = _jwt({"sub": "7656119"})
    assert steam.steamid_from_token(token) == "7656119"


def test_steamid_from_token_garbage():
    assert steam.steamid_from_token("") == ""
    assert steam.steamid_from_token("not-a-jwt") == ""
    assert steam.steamid_from_token("a.!!!notbase64!!!.c") == ""
    assert steam.steamid_from_token("a.aGVsbG8.c") == ""  # payload not JSON


def test_steamid_from_token_missing_or_nonstring_sub():
    assert steam.steamid_from_token(_jwt({"aud": ["web:store"]})) == ""
    assert steam.steamid_from_token(_jwt({"sub": 123})) == ""
