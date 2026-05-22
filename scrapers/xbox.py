"""Xbox library scraper.

Owned games come from the Microsoft account order history API
(account.microsoft.com/billing/orders/list), which the page fetches as JSON and
pages in as you scroll. We observe those responses (captured by the browser),
filter each order's line items to itemTypeName == "Game", and read the title +
stable Store product id. The default view shows only ~30 days, so widen the date
filter before scraping to capture the full history.
"""
from __future__ import annotations

import json
import logging

from scrapers.base import ScrapedGame, scroll_until_idle

logger = logging.getLogger(__name__)

VENDOR_URL = "https://account.microsoft.com/billing/orders"
SOURCE = "xbox"
GAME_ITEM_TYPE = "Game"
PLATFORM = "Xbox"  # modern Xbox; reuses the existing coarse platform row


def parse_orders(responses: list[dict]) -> list[ScrapedGame]:
    """Extract owned games from captured orders/list response payloads."""
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


def _orders_responses(captured: list) -> list[dict]:
    bodies = []
    for entry in captured or []:
        if "orders/list" not in entry.get("url", ""):
            continue
        try:
            bodies.append(json.loads(entry["body"]))
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return bodies


def collect(page, captured: list) -> list[ScrapedGame]:
    """Scroll the order history so all pages load, then parse captured orders.

    Relies on the browser having captured the orders/list JSON responses; widen
    the date filter to the maximum range before pressing Enter so the full
    history loads.
    """
    scroll_until_idle(page, captured)
    games = parse_orders(_orders_responses(captured))
    logger.info("xbox: extracted %d games from order history", len(games))
    return games
