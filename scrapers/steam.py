"""Steam library scraper (3-tier: config Web API creds -> session-minted token -> error).

Owned games come from the official IPlayerService/GetOwnedGames -- keyed when the
user configured a Web API key + SteamID64, otherwise via a webapi_token the
logged-in store session mints for itself (pointssummary/ajaxgetasyncconfig; the
JWT's `sub` claim is the SteamID64). Owned-DLC ownership comes from the session's
dynamicstore/userdata `rgOwnedApps`, fetched after the games step with one
cache-busted retry (flaky right after login), carried as id-only kind="addon"
rows. The DLC catalogue itself is fetched later by steam_dlc (keyless appdetails).
Pure parsers are unit-tested; `collect` wiring is tested with a fake page.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import time
from collections.abc import Callable

import requests

import config
from scrapers.base import ScrapedGame

logger = logging.getLogger(__name__)

VENDOR_URL = "https://store.steampowered.com/account/licenses/"
SOURCE = "steam"
PLATFORM = "Steam"

OWNED_GAMES_URL = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
USERDATA_URL = "https://store.steampowered.com/dynamicstore/userdata/"
TOKEN_CONFIG_URL = "https://store.steampowered.com/pointssummary/ajaxgetasyncconfig"

LOGIN_REQUIRED_MSG = "Log into Steam in the browser window first, then press Continue"
_USERDATA_RETRY_WAIT_MS = 2000
CAPSULE_URL = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"


def parse_owned_games(payload: dict) -> list[ScrapedGame]:
    """Map a GetOwnedGames response to game ScrapedGames (kind='game')."""
    games = ((payload or {}).get("response") or {}).get("games") or []
    out: list[ScrapedGame] = []
    for g in games:
        appid = g.get("appid")
        name = (g.get("name") or "").strip()
        if not appid or not name:
            continue
        out.append(ScrapedGame(
            title=name, platform=PLATFORM, source=SOURCE,
            external_id=str(appid), cover_url=CAPSULE_URL.format(appid=appid),
            source_title=name))
    return out


def parse_userdata(payload: dict) -> list[ScrapedGame]:
    """Map dynamicstore/userdata rgOwnedApps to id-only owned-appid carriers
    (kind='addon'). These ride the scrape payload so the catalogue/ownership step
    knows which appids the user owns; the title is just the appid placeholder."""
    owned = (payload or {}).get("rgOwnedApps") or []
    return [ScrapedGame(title=str(appid), platform=PLATFORM, source=SOURCE,
                        external_id=str(appid), kind="addon")
            for appid in owned]


def parse_webapi_token(payload: dict) -> str:
    """Extract data.webapi_token from the pointssummary config; '' when absent/malformed."""
    data = (payload or {}).get("data") if isinstance(payload, dict) else None
    token = data.get("webapi_token") if isinstance(data, dict) else None
    return token if isinstance(token, str) else ""


def steamid_from_token(token: str) -> str:
    """SteamID64 from the JWT's `sub` claim; '' on any parse issue.

    No signature verification -- we only read our own token back."""
    try:
        seg = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))
        sub = claims.get("sub")
    except (IndexError, ValueError, binascii.Error) as exc:
        logger.debug("steam: webapi_token not parseable (%s)", exc)
        return ""
    return sub if isinstance(sub, str) else ""


def _games_via_session(page) -> list[ScrapedGame]:
    """Tier 2: mint a webapi_token from the logged-in store session, then call the
    official GetOwnedGames with access_token= (no Web API key involved)."""
    resp = page.request.get(TOKEN_CONFIG_URL)
    token = ""
    if resp.ok:
        try:
            token = parse_webapi_token(resp.json())
        except ValueError as exc:
            logger.warning("steam: token config not JSON (%s)", exc)
    steam_id = steamid_from_token(token)
    if not (token and steam_id):
        raise RuntimeError(LOGIN_REQUIRED_MSG)
    resp = page.request.get(OWNED_GAMES_URL, params={
        "access_token": token, "steamid": steam_id, "include_appinfo": "true",
        "include_played_free_games": "true", "format": "json"})
    if not resp.ok:
        raise RuntimeError(f"{LOGIN_REQUIRED_MSG} (GetOwnedGames HTTP {resp.status})")
    return parse_owned_games(resp.json())


def _fetch_userdata(page) -> list[ScrapedGame]:
    """Owned-appid carriers, fetched AFTER the games step; one cache-busted retry
    when empty (userdata is flaky right after login). Best-effort, never fatal."""
    for attempt, url in enumerate((USERDATA_URL,
                                   f"{USERDATA_URL}?v={time.monotonic_ns()}")):
        if attempt:
            page.wait_for_timeout(_USERDATA_RETRY_WAIT_MS)
        resp = page.request.get(url)
        if not resp.ok:
            logger.warning("steam: userdata fetch failed (%s)", resp.status)
            continue
        owned = parse_userdata(resp.json())
        if owned:
            logger.info("steam: %d owned appids (games+DLC) via userdata", len(owned))
            return owned
    logger.warning("steam: userdata empty after retry; owned DLC will be empty")
    return []


def collect(page, captured: list | None = None,
            progress: Callable[[int], None] | None = None) -> list[ScrapedGame]:
    """Owned Steam games + owned-appid carriers, via a three-tier ladder:

    1. Config creds (Web API key + SteamID64) -> keyed GetOwnedGames (CLI back-compat).
    2. Logged-in session mints its own webapi_token -> GetOwnedGames (zero config).
    3. Neither -> honest RuntimeError telling the user to log into Steam first.
    """
    api_key, steam_id = config.get_steam_credentials()
    if api_key and steam_id:
        params = {"key": api_key, "steamid": steam_id, "include_appinfo": "true",
                  "include_played_free_games": "true", "format": "json"}
        resp = requests.get(OWNED_GAMES_URL, params=params, timeout=30)
        resp.raise_for_status()
        games = parse_owned_games(resp.json())
        logger.info("steam: %d owned games via GetOwnedGames (config creds)", len(games))
    else:
        games = _games_via_session(page)
        logger.info("steam: %d owned games via session token", len(games))

    owned = _fetch_userdata(page)
    if progress:
        progress(len(games))  # owned base games (accurate "N games"; carriers are DLC)
    return games + owned
