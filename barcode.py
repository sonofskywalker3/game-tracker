"""UPC/barcode resolution for the mobile companion.

Resolution chain (see resolve()): local barcode_cache -> UPCitemdb free API
-> IGDB title match. External lookups degrade gracefully (never raise) so the
scan flow never 500s.
"""
import logging

import requests

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
