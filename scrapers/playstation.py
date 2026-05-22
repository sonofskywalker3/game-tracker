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

from scrapers.base import ScrapedGame, auth_headers, capture_request_headers

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
    resp = page.request.get(GRAPHQL_URL, params=params, headers=headers)
    if not resp.ok:
        raise RuntimeError(
            f"PSN getPurchasedGameList failed: {resp.status} {resp.status_text} "
            f"(persisted-query hash may be stale — re-run recon to refresh it)"
        )
    return resp.json()


def collect(page, captured: list | None = None) -> list[ScrapedGame]:
    """Page through the authenticated library API and return all owned games.

    Assumes the page is on an authenticated PSN session (cookies carry the auth,
    shared by page.request). Stops when a page yields no new product IDs. The
    `captured` page traffic is unused here — PSN's list comes from the API.
    """
    headers = auth_headers(
        capture_request_headers(page, OP_NAME, trigger=lambda: page.goto(VENDOR_URL))
    )
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
    return games
