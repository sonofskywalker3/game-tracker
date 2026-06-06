"""Resolve a Nintendo (Switch) add-on's parent GAME via the eShop, PlayStation-style.

Order history gives a flat list of owned items (NSUID + name) with no parent link,
and an NSUID carries no parent information. So, like the PS per-game add-on pass,
we read each owned game's DLC list and link the owned add-ons we find:

  NSUID --(deterministic)--> SKU --(Algolia getObject)--> slug
        --(Next.js dlc.json)--> the game's full DLC list (each DLC's NSUID)

`sku_from_nsuid` and `parse_dlc_list` are pure (unit-tested against a captured
fixture); the network fetch is injected so the suite runs offline. Bootstrap
values (`buildId`, Algolia search key) rotate per Nintendo deploy and are grabbed
live from the scrape browser session (see scrape wiring). See
docs/superpowers/specs/2026-06-06-nintendo-dlc-deep-fetch-design.md.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from addon_parent import ParentRef

logger = logging.getLogger(__name__)

# NSUID prefixes (first 4 digits): 7001/7007 = game/bundle, 7005 = add-on (DLC).
DLC_NSUID_PREFIX = "7005"

# eShop endpoints. The Algolia search key + Next.js buildId rotate per deploy and
# are harvested live (see bootstrap); the app id and index are stable.
ALGOLIA_APP_ID = "U3B6GR4UA3"
ALGOLIA_HOST = "https://u3b6gr4ua3-dsn.algolia.net"
GAME_INDEX = "store_game_en_us"
STORE_HOME_URL = "https://www.nintendo.com/us/store/"
DLC_JSON_URL = ("https://www.nintendo.com/_next/data/{build_id}"
                "/us/store/products/{slug}/dlc.json?slug={slug}")


@dataclass
class DlcEntry:
    """One DLC pulled from a game's eShop DLC list."""
    nsuid: str
    sku: str | None = None
    name: str | None = None


def sku_from_nsuid(nsuid: str) -> str:
    """The eShop SKU (== Algolia objectID) for an NSUID, deterministically.

    A 14-digit NSUID maps to a 10-digit SKU as `nsuid[0] + nsuid[3] + nsuid[6:]`
    (base 70010000059002 -> 7100059002; DLC 70050000042414 -> 7500042414).
    """
    return nsuid[0] + nsuid[3] + nsuid[6:]


def parse_dlc_list(body: str | dict) -> list[DlcEntry]:
    """Pull the add-on (7005) DLC out of a game's `dlc.json` Next.js page-data.

    The DLC live as `Product:{sku}` entries under
    pageProps.initialApolloState; the base game and any non-Product keys are
    ignored. Returns [] for empty/garbage input.
    """
    obj = json.loads(body) if isinstance(body, str) else (body or {})
    state = ((obj.get("pageProps") or {}).get("initialApolloState")) or {}
    out: list[DlcEntry] = []
    for key, val in state.items():
        if not key.startswith("Product:") or not isinstance(val, dict):
            continue
        nsuid = str(val.get("nsuid") or "")
        if not nsuid.startswith(DLC_NSUID_PREFIX):
            continue
        out.append(DlcEntry(nsuid=nsuid, sku=val.get("sku"), name=val.get("name")))
    return out


class CatalogFetch(Protocol):
    """Injected eShop access: an Algolia game record by SKU, and a game's
    `dlc.json` page-data by slug. Both return None on miss/failure."""
    def game(self, sku: str) -> dict | None: ...
    def dlc_json(self, slug: str) -> dict | None: ...


def build_parent_map(
    game_nsuids: list[str], *, fetch: CatalogFetch,
    progress: Callable[[int, int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, ParentRef]:
    """Map each owned game's DLC NSUID to that game's parent ref.

    For every owned game NSUID: resolve its SKU (deterministic) -> Algolia record
    (slug + title) -> the game's `dlc.json` DLC list, then point each DLC NSUID at
    the parent GAME (its NSUID, which matches `game_external_ids` for linking). The
    caller intersects the result with the owned-DLC set. A game that doesn't
    resolve or has no DLC is skipped; one failed game never sinks the batch.
    `progress(done, total, linked)` ticks per game; `should_cancel` stops early.
    """
    from addon_parent import ParentRef  # local import avoids an import cycle

    out: dict[str, ParentRef] = {}
    unique = list(dict.fromkeys(game_nsuids))
    for done, nsuid in enumerate(unique, 1):
        if should_cancel and should_cancel():
            logger.info("nintendo dlc: cancelled after %d/%d games", done - 1, len(unique))
            break
        try:
            record = fetch.game(sku_from_nsuid(nsuid))
            slug = (record or {}).get("urlKey")
            if slug:
                body = fetch.dlc_json(slug)
                if body:
                    parent = ParentRef(product_id=nsuid,
                                       name=record.get("title") or record.get("name"))
                    for dlc in parse_dlc_list(body):
                        out[dlc.nsuid] = parent
        except Exception as exc:  # one bad game must not sink the batch
            logger.warning("nintendo dlc: game %s failed: %s", nsuid, exc)
        if progress:
            progress(done, len(unique), len(out))
    return out


def _algolia_key(captured: list | None) -> str | None:
    """The Algolia search key from the most recent captured eShop request."""
    for entry in reversed(captured or []):
        if "algolia.net" in (entry.get("url") or ""):
            headers = {k.lower(): v for k, v in (entry.get("request_headers") or {}).items()}
            if headers.get("x-algolia-api-key"):
                return headers["x-algolia-api-key"]
    return None


def bootstrap(page, captured: list | None) -> tuple[str, str]:
    """Harvest the rotating (algolia_key, build_id) from a live eShop page.

    Loads the store home (which fires an Algolia search) so the key appears in the
    captured request headers, and reads buildId from window.__NEXT_DATA__. Raises
    if either can't be found (the scrape leaves DLC to the name fallback).
    """
    page.goto(STORE_HOME_URL)
    build_id = page.evaluate("() => (window.__NEXT_DATA__ || {}).buildId || null")
    key = _algolia_key(captured)
    if not key:                       # the home page should have queried Algolia; retry once
        page.wait_for_timeout(1500)
        key = _algolia_key(captured)
    if not key or not build_id:
        raise RuntimeError(f"nintendo bootstrap failed (key={'set' if key else 'MISSING'}, "
                           f"buildId={'set' if build_id else 'MISSING'})")
    logger.info("nintendo bootstrap: buildId=%s, algolia key captured", build_id)
    return key, build_id


class LiveFetch:
    """CatalogFetch over the authenticated scrape browser (page.request)."""

    def __init__(self, page, algolia_key: str, build_id: str):
        self._page = page
        self._key = algolia_key
        self._build_id = build_id

    def game(self, sku: str) -> dict | None:
        resp = self._page.request.get(
            f"{ALGOLIA_HOST}/1/indexes/{GAME_INDEX}/{sku}",
            headers={"X-Algolia-Application-Id": ALGOLIA_APP_ID, "X-Algolia-API-Key": self._key})
        return resp.json() if resp.ok else None

    def dlc_json(self, slug: str) -> dict | None:
        resp = self._page.request.get(DLC_JSON_URL.format(build_id=self._build_id, slug=slug))
        return resp.json() if resp.ok else None


def collect_parent_map(
    page, captured: list | None, game_nsuids: list[str], *,
    progress: Callable[[int, int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, ParentRef]:
    """Live per-game DLC pass: bootstrap creds, then build the child->parent map.

    Runs inside the scrape browser session (like playstation.collect_addons).
    Returns {dlc_nsuid: ParentRef}; the caller links the owned subset through
    addon_parent.resolve_and_link.
    """
    key, build_id = bootstrap(page, captured)
    return build_parent_map(game_nsuids, fetch=LiveFetch(page, key, build_id),
                            progress=progress, should_cancel=should_cancel)
