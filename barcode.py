"""UPC/barcode resolution for the mobile companion.

Resolution chain (see resolve()): local barcode_registry -> UPCitemdb free API
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
# Retail platform phrase (as it appears in UPC titles) -> app short_name. Longest
# phrases first so "nintendo switch" wins over "switch". Extensible.
RETAIL_PLATFORM_TO_SHORT: tuple[tuple[str, str], ...] = (
    ("nintendo switch 2", "Switch"), ("nintendo switch", "Switch"), ("switch", "Switch"),
    ("playstation 5", "PS5"), ("ps5", "PS5"),
    ("playstation 4", "PS4"), ("ps4", "PS4"),
    ("playstation 3", "PS3"), ("ps3", "PS3"),
    ("xbox series x|s", "Xbox"), ("xbox series x", "Xbox"), ("xbox series s", "Xbox"),
    ("xbox one", "Xbox"), ("xbox 360", "X360"), ("xbox", "Xbox"),
    ("wii u", "WiiU"), ("wii", "Wii"),
    ("nintendo 3ds", "3DS"), ("3ds", "3DS"), ("nintendo ds", "NDS"),
    ("gamecube", "GC"), ("nintendo 64", "N64"),
    ("pc", "PC"), ("windows", "PC"),
)


def parse_retail_platform(raw: str | None) -> str | None:
    """First platform named in a retail product title, mapped to an app short_name."""
    if not raw:
        return None
    low = raw.lower()
    for phrase, short in RETAIL_PLATFORM_TO_SHORT:
        if re.search(r"\b" + re.escape(phrase) + r"\b", low):
            return short
    return None


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


def registry_get(conn: sqlite3.Connection, upc: str) -> dict | None:
    """Return the cached mapping for a UPC, or None."""
    row = conn.execute(
        "SELECT upc, igdb_id, title, platform, game_id FROM barcode_registry WHERE upc = ?",
        (upc,),
    ).fetchone()
    return dict(row) if row else None


def registry_put(conn: sqlite3.Connection, upc: str, *, igdb_id: int | None = None,
                 title: str | None = None, platform: str | None = None,
                 game_id: int | None = None) -> None:
    """Upsert a UPC -> game mapping (stamps confirmed_at)."""
    conn.execute(
        "INSERT INTO barcode_registry (upc, igdb_id, title, platform, game_id, confirmed_at) "
        "VALUES (?, ?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(upc) DO UPDATE SET igdb_id=excluded.igdb_id, title=excluded.title, "
        "platform=excluded.platform, game_id=excluded.game_id, confirmed_at=datetime('now')",
        (upc, igdb_id, title, platform, game_id),
    )


def registry_upcs_for_game(conn: sqlite3.Connection, game_id: int) -> list[dict]:
    """All known UPC -> platform rows for a game (the per-platform UPC set)."""
    rows = conn.execute(
        "SELECT upc, platform FROM barcode_registry WHERE game_id = ? ORDER BY platform",
        (game_id,),
    ).fetchall()
    return [{"upc": r["upc"], "platform": r["platform"]} for r in rows]


def _owned_game_id(conn: sqlite3.Connection, title: str) -> int | None:
    """id of an existing game whose normalized title matches, else None."""
    if not title:
        return None
    row = conn.execute(
        "SELECT id FROM games WHERE normalized_title = ?",
        (models.normalize_title(title),),
    ).fetchone()
    return row["id"] if row else None


def owned_platforms_for(conn: sqlite3.Connection, game_id: int) -> list[dict]:
    """Platforms the user owns this game on, with per-platform format + whether the
    platform has a digital market (drives the (Physical/Digital) display qualifier)."""
    rows = conn.execute(
        "SELECT p.short_name, gp.format, p.has_digital_market "
        "FROM game_platforms gp JOIN platforms p ON p.id = gp.platform_id "
        "WHERE gp.game_id = ? ORDER BY p.short_name",
        (game_id,),
    ).fetchall()
    return [{"short_name": r["short_name"], "format": r["format"],
             "has_digital_market": r["has_digital_market"]} for r in rows]


def resolve(conn: sqlite3.Connection, upc: str, *, client_id: str | None = None,
            token: str | None = None) -> dict:
    """Resolve a UPC to candidate games: cache -> UPC API -> IGDB match.

    Returns {upc, source, scanned_platform, candidates[, product_title]}. Each candidate:
    {igdb_id, title, platform, cover_url, game_type, owned_game_id, owned_platforms}."""
    cached = registry_get(conn, upc)
    if cached:
        owned_id = cached["game_id"] or _owned_game_id(conn, cached["title"] or "")
        return {"upc": upc, "source": "cache", "scanned_platform": None, "candidates": [{
            "igdb_id": cached["igdb_id"],
            "title": cached["title"],
            "platform": cached["platform"],
            "cover_url": None,
            "game_type": None,
            "owned_game_id": owned_id,
            "owned_platforms": owned_platforms_for(conn, owned_id) if owned_id else [],
        }]}

    product = lookup_product_title(upc)
    if not product:
        return {"upc": upc, "source": "none", "candidates": [], "scanned_platform": None}

    scanned_platform = parse_retail_platform(product)
    # Retail titles carry platform/packaging noise that defeats IGDB's title search;
    # match on (and prefill) the cleaned name. Fall back to raw if cleaning empties it.
    search_title = clean_product_title(product) or product
    platform_ids = igdb_match.platform_ids_for([scanned_platform]) if scanned_platform else set()

    candidates: list[dict] = []
    if client_id and token:
        raw = igdb_match.candidates_for(
            search_title, platform_ids, None, client_id, token,
            drop_fan_types=True, restrict_to_platform=bool(platform_ids))
        # Some valid IGDB entries have empty/incomplete platform lists, so a
        # platform-restricted search can drop the real game entirely. If the
        # restricted search found nothing, retry once unrestricted (still dropping
        # fan/mod types) so a known scanned platform never zeroes out a valid scan.
        if not raw and platform_ids:
            raw = igdb_match.candidates_for(
                search_title, platform_ids, None, client_id, token,
                drop_fan_types=True, restrict_to_platform=False)
        for c in raw[:MAX_CANDIDATES]:
            owned_id = _owned_game_id(conn, c.get("name") or "")
            shorts = igdb_match.short_names_for(c.get("platforms") or [])
            candidates.append({
                "igdb_id": c.get("igdb_id"),
                "title": c.get("name"),
                "platform": scanned_platform or (shorts[0] if shorts else None),
                "cover_url": c.get("cover_url"),
                "game_type": c.get("game_type"),
                "owned_game_id": owned_id,
                "owned_platforms": owned_platforms_for(conn, owned_id) if owned_id else [],
            })

    if client_id and token:
        for cand in candidates:
            if cand.get("game_type") != igdb_match._BUNDLE_GAME_TYPE or not cand.get("igdb_id"):
                continue
            cons = []
            for k in igdb_match.bundle_constituents(cand["igdb_id"], client_id, token):
                owned_id = _owned_game_id(conn, k.get("name") or "")
                cons.append({
                    "title": k.get("name"),
                    "owned_game_id": owned_id,
                    "owned_platforms": owned_platforms_for(conn, owned_id) if owned_id else [],
                })
            cand["constituents"] = cons

    # Record EVERY scan (knowledge, not ownership): upc -> best-guess title/igdb,
    # game_id stays NULL until a confirmed add links it.
    top = candidates[0] if candidates else None
    registry_put(conn, upc,
                 igdb_id=top["igdb_id"] if top else None,
                 title=top["title"] if top else search_title,
                 platform=scanned_platform, game_id=None)
    conn.commit()

    if not candidates:
        # Found a product name but no IGDB match: hand the cleaned title back so the
        # app can prefill manual search without the retail boilerplate.
        return {"upc": upc, "source": "upc_api", "candidates": [],
                "product_title": search_title, "scanned_platform": scanned_platform}
    return {"upc": upc, "source": "upc_api", "candidates": candidates,
            "scanned_platform": scanned_platform}
