"""UPC/barcode resolution for the mobile companion.

Resolution chain (see resolve()): local barcode_cache -> UPCitemdb free API
-> IGDB title match. External lookups degrade gracefully (never raise) so the
scan flow never 500s.
"""
import logging
import re
import sqlite3

import requests

import igdb_match
import models

log = logging.getLogger(__name__)

# Free UPCitemdb trial endpoint: ~100 lookups/day, no API key required.
UPCITEMDB_TRIAL_URL = "https://api.upcitemdb.com/prod/trial/lookup"
UPC_LOOKUP_TIMEOUT = 8  # seconds
MAX_CANDIDATES = 5

# ── Retail-title cleanup ──────────────────────────────────────────────────────
# UPCitemdb titles wrap the real game name in platform names, packaging, region,
# and genre boilerplate ("Mario Kart 8 Deluxe racing video game (Nintendo Switch)").
# IGDB's title search chokes on that noise and returns nothing, so we strip it
# before matching. Lists are extensible — add new boilerplate here, not in callers.
_RETAIL_NOISE_WORDS: tuple[str, ...] = (
    # Platforms (longest-first so "nintendo switch" wins over "switch")
    "nintendo switch 2", "nintendo switch", "switch",
    "playstation 5", "playstation 4", "playstation 3", "playstation",
    "ps5", "ps4", "ps3",
    "xbox series x|s", "xbox series x", "xbox series s", "xbox series",
    "xbox one", "xbox 360", "xbox",
    "nintendo wii u", "wii u", "nintendo wii", "wii",
    "nintendo 3ds", "3ds", "nintendo ds", "nintendo gamecube", "gamecube",
    "pc dvd", "windows pc", "pc",
    # Packaging / region / condition
    "standard edition", "u.s. version", "us version", "world edition",
    "north america", "region free", "brand new", "factory sealed", "sealed",
    "physical", "digital", "import", "ntsc", "pal",
)
# Drops bracketed/parenthesised chunks: "(Nintendo Switch)", "[Physical]".
_BRACKETS_RE = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
# Drops "video game" plus an optional preceding genre word ("racing video game").
_VIDEO_GAME_RE = re.compile(r"\b(?:\w+\s+)?video game\b", re.IGNORECASE)
_NOISE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in _RETAIL_NOISE_WORDS) + r")\b",
    re.IGNORECASE,
)
# Separator dashes only (space on both sides) — leaves intra-word hyphens like
# "Spider-Man" untouched.
_SEP_DASH_RE = re.compile(r"\s+[-–—]+\s+")


def clean_product_title(raw: str | None) -> str:
    """Strip retail/platform/packaging boilerplate from a UPC product title so it
    can be title-searched on IGDB. Preserves intra-word hyphens and inner colons.

    "Mario Kart 8 Deluxe racing video game (Nintendo Switch)" -> "Mario Kart 8 Deluxe"
    """
    if not raw:
        return ""
    t = _BRACKETS_RE.sub(" ", raw)
    t = _VIDEO_GAME_RE.sub(" ", t)
    t = _NOISE_RE.sub(" ", t)
    t = _SEP_DASH_RE.sub(" ", t)                 # collapse separator dashes
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t.strip(" -–—:").strip()    # trim stray leading/trailing seps


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

    # Retail titles carry platform/packaging noise that defeats IGDB's title search;
    # match on (and prefill) the cleaned name. Fall back to raw if cleaning empties it.
    search_title = clean_product_title(product) or product

    candidates: list[dict] = []
    if client_id and token:
        for c in igdb_match.candidates_for(search_title, set(), None, client_id, token)[:MAX_CANDIDATES]:
            shorts = igdb_match.short_names_for(c.get("platforms") or [])
            candidates.append({
                "igdb_id": c.get("igdb_id"),
                "title": c.get("name"),
                "platform": shorts[0] if shorts else None,
                "cover_url": c.get("cover_url"),
                "owned_game_id": _owned_game_id(conn, c.get("name") or ""),
            })

    if not candidates:
        # Found a product name but no IGDB match: hand the cleaned title back so the
        # app can prefill manual search without the retail boilerplate.
        return {"upc": upc, "source": "upc_api", "candidates": [], "product_title": search_title}
    return {"upc": upc, "source": "upc_api", "candidates": candidates}
