"""Resolve a Nintendo (Switch) add-on's parent GAME via the eShop, Xbox-style.

Order history gives a flat list of owned items (NSUID + name + eShop urlKey/slug)
with no parent link, and an NSUID carries no parent information. But each add-on's
own eShop product page declares its required base game ("requires this game to
play"): the Next.js page-data carries a `baseSoftware` ref pointing at the base
game's Product. So, like Microsoft's displaycatalog `addOnParent`, we read each
owned add-on's page and link it to that base GAME:

  urlKey --(Next.js products/{slug}.json)--> baseSoftware ref --> base SKU
         --(deterministic)--> base GAME NSUID (matches game_external_ids)

`parse_base_software`, `sku_from_nsuid`, `nsuid_from_sku`, and
`build_addon_parent_map` are pure (unit-tested against a captured fixture); the
network fetch is injected so the suite runs offline. The Next.js `buildId` rotates
per Nintendo deploy and is grabbed live from the scrape browser session (see scrape
wiring). See docs/superpowers/specs/2026-06-06-nintendo-dlc-deep-fetch-design.md.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from addon_parent import ParentRef

logger = logging.getLogger(__name__)

# NSUID prefixes (first 4 digits): 7001/7007 = game/bundle, 7005 = add-on (DLC).
DLC_NSUID_PREFIX = "7005"

# The store games grid is a Next.js page; navigating it exposes window.__NEXT_DATA__
# whose buildId addresses the per-product page-data endpoint below.
STORE_GAMES_URL = "https://www.nintendo.com/us/store/games/"
PRODUCT_JSON_URL = ("https://www.nintendo.com/_next/data/{build_id}"
                    "/us/store/products/{slug}.json?slug={slug}")

# An Apollo cache key for a Product looks like: Product:{"sku":"7100059002"}.
_PRODUCT_SKU_RE = re.compile(r'Product:\{"sku":"(\d+)"\}')


def sku_from_nsuid(nsuid: str) -> str:
    """The eShop SKU (== Algolia objectID) for an NSUID, deterministically.

    A 14-digit NSUID maps to a 10-digit SKU as `nsuid[0] + nsuid[3] + nsuid[6:]`
    (base 70010000059002 -> 7100059002; DLC 70050000042414 -> 7500042414).
    """
    return nsuid[0] + nsuid[3] + nsuid[6:]


def nsuid_from_sku(sku: str) -> str:
    """The 14-digit NSUID for a 10-digit eShop SKU (inverse of `sku_from_nsuid`).

    Real Switch software NSUIDs are `700` + type + `00` + an 8-digit serial; the
    SKU drops the two zero-pairs (`700X00SSSSSSSS` -> `7XSSSSSSSS`). Reconstructed
    as `'700' + sku[1] + '00' + sku[2:]`; verified to round-trip on every NSUID in
    the live library.
    """
    return "700" + sku[1] + "00" + sku[2:]


def _sku_from_ref(ref: str | None) -> str | None:
    """The SKU out of an Apollo `Product:{"sku":"..."}` cache-key ref, or None."""
    if not ref:
        return None
    m = _PRODUCT_SKU_RE.search(ref)
    return m.group(1) if m else None


def parse_base_software(body: str | dict) -> list[tuple[str, str | None]]:
    """The base GAME(s) an add-on requires, from its eShop product page-data.

    Reads every `Product` in `pageProps.initialApolloState` that carries a
    `baseSoftware` ref list, resolves each ref to its base Product (by SKU), and
    returns [(base_game_nsuid, base_game_name)] -- the NSUID matching
    `game_external_ids`. Returns [] for empty/garbage input or pages with no
    baseSoftware. Order-preserving and de-duplicated.
    """
    obj = json.loads(body) if isinstance(body, str) else (body or {})
    state = ((obj.get("pageProps") or {}).get("initialApolloState")) or {}
    out: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for val in state.values():
        if not isinstance(val, dict):
            continue
        for ref in val.get("baseSoftware") or []:
            sku = _sku_from_ref(ref.get("__ref") if isinstance(ref, dict) else None)
            if not sku:
                continue
            nsuid = nsuid_from_sku(sku)
            if nsuid in seen:
                continue
            seen.add(nsuid)
            base = state.get(f'Product:{{"sku":"{sku}"}}') or {}
            out.append((nsuid, base.get("name")))
    return out


class CatalogFetch(Protocol):
    """Injected eShop access: an add-on's `products/{slug}.json` page-data by slug.
    Returns None on miss/failure."""
    def product_json(self, slug: str) -> dict | None: ...


def _choose_parent(parents: list[tuple[str, str | None]],
                   owned_game_nsuids: set[str]) -> tuple[str, str | None] | None:
    """Pick the single parent to link an add-on to, or None to leave for review.

    Prefer a base game the user already owns (handles cross-series packs that list
    several base games); otherwise accept a sole candidate. Multiple candidates
    with none owned is ambiguous -> None (the add-on stays in the review queue).
    """
    if not parents:
        return None
    owned = [p for p in parents if p[0] in owned_game_nsuids]
    if owned:
        return owned[0]
    if len(parents) == 1:
        return parents[0]
    return None


def build_addon_parent_map(
    addon_items: list[tuple[str, str | None]], *, fetch: CatalogFetch,
    owned_game_nsuids: set[str] | None = None,
    progress: Callable[[int, int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, ParentRef]:
    """Map each owned add-on NSUID to its parent GAME ParentRef.

    `addon_items` is [(addon_nsuid, slug)] from the order-history scrape. For each
    add-on: fetch its `products/{slug}.json`, read `baseSoftware` -> base GAME
    NSUID + name, choose one (preferring an owned base), and point the add-on at it.
    An add-on with no slug, no resolvable page, or an ambiguous parent is skipped
    (left for the review queue). `progress(done, total, linked)` ticks per add-on;
    `should_cancel` stops early. One failed add-on never sinks the batch.
    """
    from addon_parent import ParentRef  # local import avoids an import cycle

    owned = owned_game_nsuids or set()
    out: dict[str, ParentRef] = {}
    for done, (nsuid, slug) in enumerate(addon_items, 1):
        if should_cancel and should_cancel():
            logger.info("nintendo dlc: cancelled after %d/%d add-ons", done - 1, len(addon_items))
            break
        try:
            if slug:
                body = fetch.product_json(slug)
                if body:
                    chosen = _choose_parent(parse_base_software(body), owned)
                    if chosen:
                        out[nsuid] = ParentRef(product_id=chosen[0], name=chosen[1])
        except Exception as exc:  # one bad add-on must not sink the batch
            logger.warning("nintendo dlc: add-on %s failed: %s", nsuid, exc)
        if progress:
            progress(done, len(addon_items), len(out))
    return out


def bootstrap(page, captured: list | None = None) -> str:
    """The Next.js buildId needed to address the per-product page-data endpoint.

    Loads the store games grid and reads buildId from window.__NEXT_DATA__. Raises
    if it is missing (the caller then falls back to name matching). `captured` is
    accepted for call-site symmetry with the other vendor passes; it is unused.
    """
    page.goto(STORE_GAMES_URL)
    build_id = page.evaluate("() => (window.__NEXT_DATA__ || {}).buildId || null")
    if not build_id:
        raise RuntimeError("nintendo bootstrap failed: buildId MISSING")
    logger.info("nintendo bootstrap: buildId=%s", build_id)
    return build_id


class LiveFetch:
    """CatalogFetch over the authenticated scrape browser (page.request)."""

    def __init__(self, page, build_id: str):
        self._page = page
        self._build_id = build_id

    def product_json(self, slug: str) -> dict | None:
        resp = self._page.request.get(
            PRODUCT_JSON_URL.format(build_id=self._build_id, slug=slug))
        return resp.json() if resp.ok else None


def collect_addon_parents(
    page, captured: list | None, addon_items: list[tuple[str, str | None]], *,
    owned_game_nsuids: set[str] | None = None,
    progress: Callable[[int, int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, ParentRef]:
    """Live per-add-on parent pass: bootstrap the buildId, then build the map.

    Runs inside the scrape browser session (like playstation.collect_addons).
    Returns {addon_nsuid: ParentRef}; the caller links the owned subset through
    addon_parent.resolve_and_link.
    """
    build_id = bootstrap(page, captured)
    return build_addon_parent_map(
        addon_items, fetch=LiveFetch(page, build_id),
        owned_game_nsuids=owned_game_nsuids, progress=progress, should_cancel=should_cancel)
