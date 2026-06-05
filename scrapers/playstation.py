"""PlayStation library scraper.

The library page (library.playstation.com) is a virtualized SPA backed by a
GraphQL API, so we don't parse HTML — we call the same `getPurchasedGameList`
operation the site uses, paginating through the authenticated session to get the
complete PS4/PS5 purchased library. `parse_games` (pure) is unit-tested against a
sanitized JSON fixture; `collect` drives the live paginated requests.
"""
from __future__ import annotations

import json
import logging
import re

from scrapers.base import (
    ScrapedGame,
    auth_from_captured,
    capture_request_headers,
    replay_headers,
)

logger = logging.getLogger(__name__)

VENDOR_URL = "https://library.playstation.com/"
SOURCE = "playstation"

GRAPHQL_URL = "https://web.np.playstation.com/api/graphql/v1/op"
OP_NAME = "getPurchasedGameList"
# Apollo persisted-query hash for getPurchasedGameList. If PSN updates their web
# app this can change; a 400/persisted-query-not-found means it needs refreshing
# from a fresh recon capture (.recon/playstation.responses.jsonl).
SHA256 = "827a423f6a8ddca4107ac01395af2ec0eafd8396fc7fa204aaf9b7ed2eefa168"
PAGE_SIZE = 50
MAX_GAMES = 5000  # safety cap against a server that ignores the start cursor
REQUEST_DELAY_MS = 400  # gentle pacing between API page requests

# The API returns platform as "PS4"/"PS5" directly.
PLATFORM_LABELS = {"PS5": "PS5", "PS4": "PS4"}
DEFAULT_PLATFORM = "PS4"

# Full PS Store product id, e.g. UP0082-CUSA09377_00-PT00000000000000 (region UP/EP/JP).
ADDON_PID_RE = re.compile(r"[A-Z]{2}\d{4}-[A-Z]{4}\d{5}_00-[A-Z0-9]{16}")
STORE_PRODUCT_URL = "https://store.playstation.com/en-us/product/{pid}"
# storeDisplayClassification values that are NOT add-ons.
NON_ADDON_CLASS = frozenset({"FULL_GAME", "GAME_BUNDLE", "DEMO"})
OWNED_PRICE = "Purchased"


def parse_games(items: list[dict]) -> list[ScrapedGame]:
    """Map raw getPurchasedGameList items to ScrapedGame records."""
    games = []
    for it in items:
        name = it.get("name")
        if not name:
            continue
        platform = (it.get("platform") or "").upper().replace(" ", "")
        image = it.get("image")
        games.append(ScrapedGame(
            title=name,
            platform=PLATFORM_LABELS.get(platform, DEFAULT_PLATFORM),
            source=SOURCE,
            external_id=it.get("productId") or it.get("titleId"),
            cover_url=image.get("url") if isinstance(image, dict) else None,
            source_title=name,
        ))
    return games


def _extract(payload: dict) -> list[ScrapedGame]:
    """Pull the games list out of a getPurchasedGameList response payload."""
    data = (payload or {}).get("data") or {}
    retrieve = data.get("purchasedTitlesRetrieve") or {}
    return parse_games(retrieve.get("games") or [])


def _request_page(page, start: int, headers: dict) -> dict:
    variables = {
        "isActive": True, "platform": ["ps4", "ps5"], "size": PAGE_SIZE,
        "start": start, "sortBy": "ACTIVE_DATE", "sortDirection": "desc",
    }
    params = {
        "operationName": OP_NAME,
        "variables": json.dumps(variables, separators=(",", ":")),
        "extensions": json.dumps(
            {"persistedQuery": {"version": 1, "sha256Hash": SHA256}},
            separators=(",", ":"),
        ),
    }
    # Apollo CSRF guard: must send a non-simple content-type or the preflight header.
    req_headers = {**headers, "content-type": "application/json",
                   "apollo-require-preflight": "true"}
    resp = page.request.get(GRAPHQL_URL, params=params, headers=req_headers)
    if not resp.ok:
        raise RuntimeError(
            f"PSN getPurchasedGameList {resp.status} {resp.status_text}: {resp.text()[:300]}"
        )
    return resp.json()


def collect(page, captured: list | None = None) -> list[ScrapedGame]:
    """Page through the authenticated library API and return all owned games.

    Assumes the page is on an authenticated PSN session (cookies carry the auth,
    shared by page.request). Stops when a page yields no new product IDs. The
    `captured` page traffic is unused here — PSN's list comes from the API.
    """
    headers = auth_from_captured(captured or [], OP_NAME)
    if not headers:
        logger.info("playstation: no captured headers; reloading to capture them...")
        headers = replay_headers(capture_request_headers(page, OP_NAME, trigger=page.reload))
    logger.info("playstation: replay headers: %s", sorted(headers) or "NONE (will fail)")
    games: list[ScrapedGame] = []
    seen: set[str] = set()
    start = 0
    while start < MAX_GAMES:
        items = _extract(_request_page(page, start, headers))
        new = [g for g in items if g.external_id and g.external_id not in seen]
        if not new:
            break
        seen.update(g.external_id for g in new)
        games.extend(new)
        start += len(items)
        logger.info("playstation: %d games so far...", len(games))
        page.wait_for_timeout(REQUEST_DELAY_MS)
    if not games:
        logger.warning("playstation: 0 games — auth likely failed (see any error above)")
    return games


def _iter_product_objects(node):
    """Yield dicts anywhere in the structure that look like a store product."""
    if isinstance(node, dict):
        pid = node.get("id")
        if isinstance(pid, str) and ADDON_PID_RE.fullmatch(pid):
            yield node
        for v in node.values():
            yield from _iter_product_objects(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_product_objects(v)


def parse_addons(bodies: list[dict]) -> list[ScrapedGame]:
    """Map captured game-page GraphQL bodies to OWNED add-on ScrapedGame records.

    An add-on is a product object with a non-null price.basePrice and a
    classification that is not a base game/bundle/demo. Owned == basePrice ==
    "Purchased" (the en-us Store label). Dedupes by product id.
    """
    out: list[ScrapedGame] = []
    seen: set[str] = set()
    for body in bodies:
        if not isinstance(body, dict):
            continue
        for obj in _iter_product_objects(body):
            pid = obj["id"]
            if pid in seen:
                continue
            cls = obj.get("storeDisplayClassification")
            if cls in NON_ADDON_CLASS:
                continue
            price = obj.get("price") or {}
            base_price = price.get("basePrice") if isinstance(price, dict) else None
            if not base_price:                 # base/edition/demo objects have price=None
                continue
            if base_price != OWNED_PRICE:       # priced / "Unavailable" -> not owned
                continue
            name = obj.get("name")
            if not name:
                continue
            seen.add(pid)
            platforms = obj.get("platforms") or []
            platform = platforms[0] if platforms else DEFAULT_PLATFORM
            out.append(ScrapedGame(
                title=name, platform=PLATFORM_LABELS.get(platform, DEFAULT_PLATFORM),
                source=SOURCE, external_id=pid, source_title=name, kind="addon",
            ))
    return out


def collect_addons(page, captured: list | None = None) -> list[ScrapedGame]:
    """Owned PSN add-ons (kind="addon"), for DLC ownership matching.

    Disabled until the PSN add-on GraphQL operation + persisted-query hash are
    captured from a fresh recon (.recon/playstation.responses.jsonl); returns []
    so base-game scraping is unaffected. Enabling this is the recon-gated
    follow-up in docs/superpowers/specs/2026-05-25-dlc-scrape-ownership-design.md
    (PSN section).
    """
    logger.info("playstation: add-on capture not yet enabled (needs recon hash)")
    return []
