"""Nintendo (Switch) library scraper.

Owned games come from the Nintendo Store order history GraphQL API
(graph.nintendo.com, operationName=CustomerOrderHistory), the same call the
account site fires on Funds and Payment Methods -> Purchase History ->
Transaction History. It paginates via an integer `page` variable, so we replay
the persisted query through all pages, reusing the page's own auth headers
(bearer + x-access-token + x-customer-token, like PSN). Online history only goes
back ~2 years. Login needs the real-Chrome channel with --enable-automation
suppressed (see scrapers.base) to pass Nintendo's bot detection.

`parse_orders` (pure) is unit-tested against a sanitized JSON fixture; `collect`
drives the live paginated requests.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable

from scrapers.base import (
    ScrapedGame,
    auth_from_captured,
    capture_request_headers,
    replay_headers,
)

logger = logging.getLogger(__name__)

VENDOR_URL = "https://www.nintendo.com/us/orders/"  # order history; fires CustomerOrderHistory
SOURCE = "nintendo"

GRAPHQL_URL = "https://graph.nintendo.com/"
OP_NAME = "CustomerOrderHistory"
# Apollo persisted-query hash for CustomerOrderHistory. If Nintendo updates their
# web app this can change; a 400/persisted-query-not-found means it needs
# refreshing from a fresh recon capture (.recon/nintendo.responses.jsonl).
SHA256 = "b77d54b84f1820a9401dd46915771243abafc2f69c1539a9fc34ff46f096d0b7"

MAX_PAGES = 200  # safety cap (~24 pages for a 350-order library at 15/page)
REQUEST_DELAY_MS = 400  # gentle pacing between API page requests

# All Switch-family purchases map to the single existing "Switch" platform
# (NINTENDO_SWITCH and NINTENDO_SWITCH_2 are folded together).
PLATFORM = "Switch"

# NSUID prefix -> content type. 7005 is add-on content (DLC / upgrade packs /
# soundtracks): kept as kind="addon" so it can mark DLC ownership. 7001 (base
# games) and 7007 (bundles/collections) are kind="game". classify_nsuid is the
# prefix gate; is_non_game is the downstream name-based backstop for games.
ADDON_NSUID_PREFIXES = frozenset({"7005"})
NSUID_PREFIX_LEN = 4
# Real software NSUIDs are 14-digit ids starting "700". Physical hardware/merch
# (GameCube controller, dock, Virtual Boy headset, etc.) use short non-NSUID
# product ids (e.g. 6-digit), so requiring a real NSUID skips them.
NSUID_LEN = 14
NSUID_GAME_PREFIX = "700"


def classify_nsuid(nsuid: str | None) -> str | None:
    """Classify a Nintendo product id: "game" (base/bundle), "addon" (7005 DLC),
    or None for non-game hardware/merch (short non-NSUID product ids)."""
    if not nsuid or len(nsuid) != NSUID_LEN or not nsuid.isdigit():
        return None
    if not nsuid.startswith(NSUID_GAME_PREFIX):
        return None
    return "addon" if nsuid[:NSUID_PREFIX_LEN] in ADDON_NSUID_PREFIXES else "game"


def _orders(body: dict) -> list[dict]:
    """Pull the orders list out of a CustomerOrderHistory response payload."""
    customer = ((body or {}).get("data") or {}).get("customer") or {}
    return (customer.get("orderHistory") or {}).get("orders") or []


def parse_orders(responses: list[dict]) -> list[ScrapedGame]:
    """Map CustomerOrderHistory response payloads to ScrapedGame records.

    Emits 7005 add-on NSUIDs as kind='addon'; skips hardware/merch (non-NSUID
    ids), items missing a name or NSUID, and duplicate NSUIDs.
    """
    games: list[ScrapedGame] = []
    seen: set[str] = set()
    for body in responses:
        for order in _orders(body):
            for item in order.get("items") or []:
                nsuid = item.get("id")
                product = item.get("product") or {}
                name = product.get("name")
                kind = classify_nsuid(nsuid)
                if not name or kind is None:
                    continue
                if nsuid in seen:
                    continue
                seen.add(nsuid)
                # cover_url is left None: Nintendo's productImage is wide hero art
                # (~1920x1080), the wrong aspect for box art. The IGDB pipeline
                # (fetch_covers.py) supplies covers; see docs cover-art-igdb spec.
                games.append(ScrapedGame(
                    title=name,
                    platform=PLATFORM,
                    source=SOURCE,
                    external_id=nsuid,
                    cover_url=None,
                    source_title=name,
                    kind=kind,
                ))
    return games


def _request_page(page, page_num: int, headers: dict) -> dict:
    variables = {"includeTotals": True, "personalized": False, "page": page_num}
    params = {
        "operationName": OP_NAME,
        "variables": json.dumps(variables, separators=(",", ":")),
        "extensions": json.dumps(
            {"persistedQuery": {"version": 1, "sha256Hash": SHA256}},
            separators=(",", ":"),
        ),
    }
    req_headers = {**headers, "content-type": "application/json"}
    resp = page.request.get(GRAPHQL_URL, params=params, headers=req_headers)
    if not resp.ok:
        raise RuntimeError(
            f"Nintendo {OP_NAME} {resp.status} {resp.status_text}: {resp.text()[:300]}"
        )
    return resp.json()


def collect(page, captured: list | None = None,
            progress: Callable[[int], None] | None = None) -> list[ScrapedGame]:
    """Page through the authenticated order-history API and return owned games.

    Reuses the page's captured auth headers (the cross-origin graph.nintendo.com
    endpoint needs the bearer + x-*-token headers, not just cookies). Stops when a
    page returns no orders. `captured` holds the traffic seen while the user
    navigated to Transaction History.
    """
    headers = auth_from_captured(captured or [], OP_NAME)
    if not headers:
        logger.info("nintendo: no captured headers; reloading to capture them...")
        headers = replay_headers(capture_request_headers(page, OP_NAME, trigger=page.reload))
    logger.info("nintendo: replay headers: %s", sorted(headers) or "NONE (will fail)")
    responses: list[dict] = []
    for page_num in range(1, MAX_PAGES + 1):
        body = _request_page(page, page_num, headers)
        if not _orders(body):
            break
        responses.append(body)
        if progress:
            progress(sum(len(_orders(b)) for b in responses))
        logger.info("nintendo: fetched page %d (%d orders so far)",
                    page_num, sum(len(_orders(b)) for b in responses))
        page.wait_for_timeout(REQUEST_DELAY_MS)
    games = parse_orders(responses)
    if not games:
        logger.warning("nintendo: 0 games — auth likely failed (see any error above)")
    logger.info("nintendo: extracted %d games from %d order pages", len(games), len(responses))
    return games
