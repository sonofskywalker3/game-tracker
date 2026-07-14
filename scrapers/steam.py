"""Steam library scraper (hybrid: Web API key for owned games + session for owned DLC).

Owned games come from IPlayerService/GetOwnedGames (key + SteamID64 from config).
Owned-DLC ownership comes from the logged-in store session's dynamicstore/userdata
(`rgOwnedApps` -- every owned appid incl. DLC), carried as id-only kind="addon" rows.
The DLC catalogue itself is fetched later by steam_dlc (keyless appdetails). The pure
parsers are unit-tested; `collect` drives the live calls and is verified manually.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
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


def collect(page, captured: list | None = None,
            progress: Callable[[int], None] | None = None) -> list[ScrapedGame]:
    """Owned Steam games (via Web API key) + owned-appid carriers (via session).

    GetOwnedGames needs the key + SteamID64 from config; if absent, no games are
    returned (logged, not fatal). rgOwnedApps is read from the logged-in store
    session via page.request (cookies carry auth).
    """
    api_key, steam_id = config.get_steam_credentials()
    if not (api_key and steam_id):
        # Without the Web API key there are no named games at all — fail loudly
        # (the desktop app shows this note) instead of writing a confusing 0-row.
        raise RuntimeError(
            "Steam needs your Steam Web API key + SteamID64 (Settings page on the "
            "web app); Steam sync from this app isn't supported yet")
    params = {"key": api_key, "steamid": steam_id, "include_appinfo": "true",
              "include_played_free_games": "true", "format": "json"}
    resp = requests.get(OWNED_GAMES_URL, params=params, timeout=30)
    resp.raise_for_status()
    games = parse_owned_games(resp.json())
    logger.info("steam: %d owned games via GetOwnedGames", len(games))

    owned: list[ScrapedGame] = []
    resp = page.request.get(USERDATA_URL)
    if resp.ok:
        owned = parse_userdata(resp.json())
        logger.info("steam: %d owned appids (games+DLC) via userdata", len(owned))
    else:
        logger.warning("steam: userdata fetch failed (%s); owned DLC will be empty", resp.status)
    if progress:
        progress(len(games))  # owned base games (accurate "N games"; carriers are DLC)
    return games + owned
