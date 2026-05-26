"""Steam library scraper (hybrid: Web API key for owned games + session for owned DLC).

Owned games come from IPlayerService/GetOwnedGames (key + SteamID64 from config).
Owned-DLC ownership comes from the logged-in store session's dynamicstore/userdata
(`rgOwnedApps` -- every owned appid incl. DLC), carried as id-only kind="addon" rows.
The DLC catalogue itself is fetched later by steam_dlc (keyless appdetails). The pure
parsers are unit-tested; `collect` drives the live calls and is verified manually.
"""
from __future__ import annotations

import logging

import requests

import config
from scrapers.base import ScrapedGame

logger = logging.getLogger(__name__)

VENDOR_URL = "https://store.steampowered.com/account/licenses/"
SOURCE = "steam"
PLATFORM = "Steam"

OWNED_GAMES_URL = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
USERDATA_URL = "https://store.steampowered.com/dynamicstore/userdata/"
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


def collect(page, captured: list | None = None) -> list[ScrapedGame]:
    """Owned Steam games (via Web API key) + owned-appid carriers (via session).

    GetOwnedGames needs the key + SteamID64 from config; if absent, no games are
    returned (logged, not fatal). rgOwnedApps is read from the logged-in store
    session via page.request (cookies carry auth).
    """
    api_key, steam_id = config.get_steam_credentials()
    games: list[ScrapedGame] = []
    if api_key and steam_id:
        params = {"key": api_key, "steamid": steam_id, "include_appinfo": "true",
                  "include_played_free_games": "true", "format": "json"}
        resp = requests.get(OWNED_GAMES_URL, params=params, timeout=30)
        resp.raise_for_status()
        games = parse_owned_games(resp.json())
        logger.info("steam: %d owned games via GetOwnedGames", len(games))
    else:
        logger.warning("steam: no API key / SteamID in config.json; skipping owned-games fetch")

    owned: list[ScrapedGame] = []
    resp = page.request.get(USERDATA_URL)
    if resp.ok:
        owned = parse_userdata(resp.json())
        logger.info("steam: %d owned appids (games+DLC) via userdata", len(owned))
    else:
        logger.warning("steam: userdata fetch failed (%s); owned DLC will be empty", resp.status)
    return games + owned
