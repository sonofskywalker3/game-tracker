"""UPC/barcode resolution for the mobile companion.

Resolution chain (see resolve()): local barcode_cache -> UPCitemdb free API
-> IGDB title match. External lookups degrade gracefully (never raise) so the
scan flow never 500s.
"""
import logging
import sqlite3

import requests

import igdb_match
import models

log = logging.getLogger(__name__)

# Free UPCitemdb trial endpoint: ~100 lookups/day, no API key required.
UPCITEMDB_TRIAL_URL = "https://api.upcitemdb.com/prod/trial/lookup"
UPC_LOOKUP_TIMEOUT = 8  # seconds
MAX_CANDIDATES = 5


def lookup_product_title(upc: str, *, url: str = UPCITEMDB_TRIAL_URL,
                         timeout: int = UPC_LOOKUP_TIMEOUT) -> str | None:
    """Return the first product title for a UPC via UPCitemdb, or None.

    Network/parse failures are logged and degrade to None (never raise)."""
    try:
        resp = requests.get(url, params={"upc": upc}, timeout=timeout)
        resp.raise_for_status()
        items = resp.json().get("items") or []
    except (requests.RequestException, ValueError) as exc:
        log.warning("UPC lookup failed for %s: %s", upc, exc)
        return None
    if not items:
        return None
    return (items[0].get("title") or "").strip() or None


def cache_get(conn: sqlite3.Connection, upc: str) -> dict | None:
    """Return the cached mapping for a UPC, or None."""
    row = conn.execute(
        "SELECT upc, igdb_id, title, platform, game_id FROM barcode_cache WHERE upc = ?",
        (upc,),
    ).fetchone()
    return dict(row) if row else None


def cache_put(conn: sqlite3.Connection, upc: str, *, igdb_id: int | None = None, title: str | None = None,
              platform: str | None = None, game_id: int | None = None) -> None:
    """Upsert a confirmed UPC -> game mapping (stamps confirmed_at)."""
    conn.execute(
        "INSERT INTO barcode_cache (upc, igdb_id, title, platform, game_id, confirmed_at) "
        "VALUES (?, ?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(upc) DO UPDATE SET igdb_id=excluded.igdb_id, title=excluded.title, "
        "platform=excluded.platform, game_id=excluded.game_id, confirmed_at=datetime('now')",
        (upc, igdb_id, title, platform, game_id),
    )


def _owned_game_id(conn: sqlite3.Connection, title: str) -> int | None:
    """id of an existing game whose normalized title matches, else None."""
    if not title:
        return None
    row = conn.execute(
        "SELECT id FROM games WHERE normalized_title = ?",
        (models.normalize_title(title),),
    ).fetchone()
    return row["id"] if row else None


def resolve(conn: sqlite3.Connection, upc: str, *, client_id: str | None = None,
            token: str | None = None) -> dict:
    """Resolve a UPC to candidate games: cache -> UPC API -> IGDB match.

    Returns {upc, source, candidates[, product_title]}. Each candidate:
    {igdb_id, title, platform, cover_url, owned_game_id}."""
    cached = cache_get(conn, upc)
    if cached:
        return {"upc": upc, "source": "cache", "candidates": [{
            "igdb_id": cached["igdb_id"],
            "title": cached["title"],
            "platform": cached["platform"],
            "cover_url": None,
            "owned_game_id": cached["game_id"] or _owned_game_id(conn, cached["title"] or ""),
        }]}

    product = lookup_product_title(upc)
    if not product:
        return {"upc": upc, "source": "none", "candidates": []}

    candidates: list[dict] = []
    if client_id and token:
        for c in igdb_match.candidates_for(product, set(), None, client_id, token)[:MAX_CANDIDATES]:
            shorts = igdb_match.short_names_for(c.get("platforms") or [])
            candidates.append({
                "igdb_id": c.get("igdb_id"),
                "title": c.get("name"),
                "platform": shorts[0] if shorts else None,
                "cover_url": c.get("cover_url"),
                "owned_game_id": _owned_game_id(conn, c.get("name") or ""),
            })

    if not candidates:
        # Found a product name but no IGDB match: hand the raw title back so the
        # app can prefill manual search.
        return {"upc": upc, "source": "upc_api", "candidates": [], "product_title": product}
    return {"upc": upc, "source": "upc_api", "candidates": candidates}
