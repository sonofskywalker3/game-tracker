"""Xbox library scraper.

Owned games come from the Microsoft account order history API
(account.microsoft.com/billing/orders/list). It paginates via a continuationToken
(each response carries the token for the next page), so we replay the API through
all pages with period=SevenYears, reusing the page's own auth headers. Each
order's line items are filtered to itemTypeName == "Game" for the title + stable
Store product id.
"""
from __future__ import annotations

import logging

from scrapers.base import (
    ScrapedGame,
    auth_from_captured,
    capture_request_headers,
    replay_headers,
)

logger = logging.getLogger(__name__)

VENDOR_URL = "https://account.microsoft.com/billing/orders"
SOURCE = "xbox"
GAME_ITEM_TYPE = "Game"
PLATFORM = "Xbox"  # modern Xbox; reuses the existing coarse platform row

ORDERS_API = "https://account.microsoft.com/billing/orders/list"
ORDERS_PARAMS = {
    "period": "SevenYears",
    "orderTypeFilter": "All",
    "filterChangeCount": "1",
    "isInD365Orders": "true",
    "isPiDetailsRequired": "true",
    "timeZoneOffsetMinutes": "240",
}
MAX_PAGES = 200
REQUEST_DELAY_MS = 400


def parse_orders(responses: list[dict]) -> list[ScrapedGame]:
    """Extract owned games from orders/list response payloads."""
    games: list[ScrapedGame] = []
    seen: set[str] = set()
    for body in responses:
        for order in body.get("orders", []):
            for item in order.get("items", []):
                if item.get("itemTypeName") != GAME_ITEM_TYPE:
                    continue
                title = item.get("localTitle")
                product_id = item.get("productId")
                if not title or not product_id or product_id in seen:
                    continue
                seen.add(product_id)
                games.append(ScrapedGame(
                    title=title,
                    platform=PLATFORM,
                    source=SOURCE,
                    external_id=product_id,
                    cover_url=item.get("logoLink"),
                    source_title=title,
                ))
    return games


def collect(page, captured: list | None = None) -> list[ScrapedGame]:
    """Replay the order-history API across all pages and return owned games.

    Follows the continuationToken until exhausted; reuses the page's auth headers
    so the replay is authenticated. `captured` is unused (we drive the API).
    """
    headers = auth_from_captured(captured or [], "orders/list")
    if not headers:
        logger.info("xbox: no captured headers; reloading to capture them...")
        headers = replay_headers(capture_request_headers(page, "orders/list", trigger=page.reload))
    logger.info("xbox: replay headers: %s", sorted(headers) or "NONE (will fail)")
    responses: list[dict] = []
    token = None
    seen_tokens: set[str] = set()
    for _ in range(MAX_PAGES):
        params = dict(ORDERS_PARAMS)
        if token:
            params["continuationToken"] = token
        resp = page.request.get(ORDERS_API, params=params, headers=headers)
        if not resp.ok:
            raise RuntimeError(
                f"Xbox orders/list {resp.status} {resp.status_text}: {resp.text()[:300]}"
            )
        body = resp.json()
        responses.append(body)
        token = body.get("continuationToken")
        if not token or token in seen_tokens:
            break
        seen_tokens.add(token)
        page.wait_for_timeout(REQUEST_DELAY_MS)
    games = parse_orders(responses)
    logger.info("xbox: extracted %d games from %d order pages", len(games), len(responses))
    return games
