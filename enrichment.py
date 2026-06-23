"""Collection UPC backfill worker.

Selects owned (game, platform) pairs lacking a known UPC, name-searches
UPCitemdb, classifies each result, and writes confident matches to
barcode_registry or rows to upc_review. External calls go through barcode.py
(which degrades to []/None); this module never raises out to its caller.
"""
import logging
import sqlite3

import barcode
import models

log = logging.getLogger(__name__)

CONFIDENT = "confident"
UNCERTAIN = "uncertain"
NO_MATCH = "no_match"

# Minimum normalized-title length for the containment heuristic (avoids "go"/"a").
_MIN_CONTAIN_LEN = 4


def classify_match(normalized_title: str, short_name: str,
                   products: list[dict]) -> dict:
    """Classify name-search products against an owned game+platform.

    confident: exact normalized-title match AND platform matches (or product
        names no platform) -> auto-linkable.
    uncertain: exact title with a mismatched platform, or a normalized-title
        containment near-match -> needs human review.
    no_match: nothing plausible.
    Returns {status, upc, product_title, reason}.
    """
    uncertain: dict | None = None
    for p in products:
        raw = p.get("title") or ""
        upc = (p.get("upc") or "").strip()
        if not upc:
            continue
        clean = barcode.clean_product_title(raw)
        prod_nt = models.normalize_title(clean)
        prod_plat = barcode.parse_retail_platform(raw)
        if prod_nt == normalized_title and (prod_plat is None or prod_plat == short_name):
            return {"status": CONFIDENT, "upc": upc, "product_title": clean, "reason": None}
        if uncertain is None:
            if prod_nt == normalized_title and prod_plat and prod_plat != short_name:
                uncertain = {"status": UNCERTAIN, "upc": upc, "product_title": clean,
                             "reason": f"platform mismatch: product names {prod_plat}"}
            elif (len(prod_nt) >= _MIN_CONTAIN_LEN
                  and len(normalized_title) >= _MIN_CONTAIN_LEN
                  and (prod_nt in normalized_title or normalized_title in prod_nt)):
                uncertain = {"status": UNCERTAIN, "upc": upc, "product_title": clean,
                             "reason": "near title match"}
    if uncertain is not None:
        return uncertain
    return {"status": NO_MATCH, "upc": None, "product_title": None, "reason": "no plausible product"}


# Platform categories that can never have a physical retail UPC.
_NON_RETAIL_CATEGORIES = ("mobile", "subscription")

_ELIGIBLE_SQL = """
    SELECT g.id, g.title, g.normalized_title, g.igdb_id, g.cover_url, p.short_name
    FROM games g
    JOIN game_platforms gp ON gp.game_id = g.id
    JOIN platforms p ON p.id = gp.platform_id
    WHERE gp.owned = 1
      AND p.category NOT IN ('mobile', 'subscription')
      AND NOT EXISTS (SELECT 1 FROM barcode_registry br
                      WHERE br.game_id = g.id AND br.platform = p.short_name)
      AND NOT EXISTS (SELECT 1 FROM upc_review ur
                      WHERE ur.game_id = g.id AND ur.platform = p.short_name)
    ORDER BY g.id, p.short_name
"""


def select_eligible_pairs(conn: sqlite3.Connection, *,
                          limit: int | None = None) -> list[sqlite3.Row]:
    """Owned (game, platform) pairs with no known UPC and no review row.

    Excludes mobile/subscription platforms (no physical retail UPC exists).
    Idempotent: covered/queued/attempted/dismissed pairs are all skipped.
    """
    sql = _ELIGIBLE_SQL
    params: tuple = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    return conn.execute(sql, params).fetchall()


def count_eligible_pairs(conn: sqlite3.Connection) -> int:
    """How many eligible pairs remain (for the status display)."""
    return conn.execute(
        f"SELECT COUNT(*) FROM ({_ELIGIBLE_SQL})").fetchone()[0]


def get_enrichment_state(conn: sqlite3.Connection) -> dict:
    """The single drip-state row as a dict (seeded by the migration)."""
    row = conn.execute(
        "SELECT last_run_date, last_run_count FROM upc_enrichment_state WHERE id = 1"
    ).fetchone()
    if row is None:
        return {"last_run_date": None, "last_run_count": 0}
    return {"last_run_date": row["last_run_date"], "last_run_count": row["last_run_count"]}


def set_enrichment_state(conn: sqlite3.Connection, *, last_run_date: str,
                         last_run_count: int) -> None:
    """Persist the last drip run date + per-day call count."""
    conn.execute(
        "UPDATE upc_enrichment_state SET last_run_date = ?, last_run_count = ? WHERE id = 1",
        (last_run_date, last_run_count))
    conn.commit()
