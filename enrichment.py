"""Collection UPC backfill worker.

Selects owned (game, platform) pairs lacking a known UPC, name-searches
UPCitemdb, classifies each result, and writes confident matches to
barcode_registry or rows to upc_review. External calls go through barcode.py
(which degrades to []/None); this module never raises out to its caller.
"""
import logging

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
