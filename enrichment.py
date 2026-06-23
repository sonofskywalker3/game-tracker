"""Collection UPC backfill worker.

Selects owned (game, platform) pairs lacking a known UPC, name-searches
UPCitemdb, classifies each result, and writes confident matches to
barcode_registry or rows to upc_review. External calls go through barcode.py
(which degrades to []/None); this module never raises out to its caller.
"""
import logging
import sqlite3
import time

import barcode
import models

log = logging.getLogger(__name__)

CONFIDENT = "confident"
UNCERTAIN = "uncertain"
NO_MATCH = "no_match"

# Minimum normalized-title length for the containment heuristic (avoids "go"/"a").
_MIN_CONTAIN_LEN = 4

UPC_ENRICH_DAILY_BUDGET = 90          # < 100/day trial cap (shared per-IP bucket)
UPC_ENRICH_QUOTA_SAFETY_MARGIN = 5    # stop if live remaining drops to/below this
UPC_ENRICH_CALL_DELAY_SECONDS = 2     # inter-call throttle to avoid burst rate-limit
UPC_ENRICH_MAX_RETRIES = 3            # transient-429 burst retries before giving up
UPC_ENRICH_BACKOFF_BASE_SECONDS = 5   # exponential: 5, 10, 20s


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
_NON_RETAIL_IN_LIST = ", ".join(f"'{c}'" for c in _NON_RETAIL_CATEGORIES)

_ELIGIBLE_SQL = f"""
    SELECT g.id, g.title, g.normalized_title, g.igdb_id, g.cover_url, p.short_name
    FROM games g
    JOIN game_platforms gp ON gp.game_id = g.id
    JOIN platforms p ON p.id = gp.platform_id
    WHERE gp.owned = 1
      AND p.category NOT IN ({_NON_RETAIL_IN_LIST})
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


def run_batch(conn: sqlite3.Connection, *, budget: int = UPC_ENRICH_DAILY_BUDGET,
              search_fn=barcode.search_products_by_name,
              remaining_fn=barcode.last_rate_remaining, progress=None,
              sleep_fn=time.sleep) -> dict:
    """Run one throttled enrichment batch. Idempotent + resumable.

    Selects up to `budget` eligible pairs, name-searches each, and writes a
    confident registry link / pending review / no_match row. Stops early if the
    live trial quota (remaining_fn) drops to the safety margin. On a transient
    failure (search_fn returns None with quota unknown), retries up to
    UPC_ENRICH_MAX_RETRIES times with exponential backoff before giving up.
    If the daily quota is confirmed exhausted, skips retries and stops immediately.
    A failed call is never recorded as no_match (no poisoning).
    Throttles between calls by sleeping UPC_ENRICH_CALL_DELAY_SECONDS (injectable
    via sleep_fn for tests). All sleeps go through sleep_fn. Commits per write.
    """
    pairs = select_eligible_pairs(conn, limit=budget)
    total = len(pairs)
    found = queued = no_match = calls_used = 0
    for row in pairs:
        if calls_used >= budget:
            break
        live = remaining_fn()
        if live is not None and live <= UPC_ENRICH_QUOTA_SAFETY_MARGIN:
            log.warning("UPC enrichment stopping: trial quota remaining=%s", live)
            break
        if calls_used > 0:
            sleep_fn(UPC_ENRICH_CALL_DELAY_SECONDS)
        products = search_fn(row["title"])
        calls_used += 1
        if products is None:
            live = remaining_fn()
            if live is not None and live <= UPC_ENRICH_QUOTA_SAFETY_MARGIN:
                log.warning(
                    "UPC enrichment stopping: daily quota exhausted, remaining=%s", live)
                break
            # Transient burst — retry with exponential backoff.
            resolved = False
            for attempt in range(UPC_ENRICH_MAX_RETRIES):
                if calls_used >= budget:
                    break
                sleep_fn(UPC_ENRICH_BACKOFF_BASE_SECONDS * (2 ** attempt))
                products = search_fn(row["title"])
                calls_used += 1
                if products is not None:
                    resolved = True
                    break
            if not resolved:
                log.warning(
                    "UPC enrichment giving up after retries for %r", row["title"])
                break
        verdict = classify_match(row["normalized_title"], row["short_name"], products)
        status = verdict["status"]
        if status == CONFIDENT:
            barcode.registry_put(conn, verdict["upc"], igdb_id=row["igdb_id"],
                                 title=row["title"], platform=row["short_name"],
                                 game_id=row["id"], cover_url=row["cover_url"])
            found += 1
        elif status == UNCERTAIN:
            conn.execute(
                "INSERT INTO upc_review (game_id, platform, upc, product_title, "
                "cover_url, status, reason) VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                (row["id"], row["short_name"], verdict["upc"], verdict["product_title"],
                 row["cover_url"], verdict["reason"]))
            queued += 1
        else:
            conn.execute(
                "INSERT INTO upc_review (game_id, platform, upc, product_title, "
                "cover_url, status, reason) VALUES (?, ?, NULL, NULL, ?, 'no_match', ?)",
                (row["id"], row["short_name"], row["cover_url"], verdict["reason"]))
            no_match += 1
        conn.commit()
        if progress is not None:
            progress(calls_used, total, found, queued, no_match)
    return {"found": found, "queued": queued, "no_match": no_match,
            "calls_used": calls_used, "total": total}
