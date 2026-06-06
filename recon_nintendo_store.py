"""Recon: capture Nintendo eShop product pages for DLC-list discovery.

A headed browser opens reusing your saved Nintendo login (.pw-profile). Just
browse — every Nintendo eShop product page you visit is auto-captured (no key
presses). Close the browser window (or wait for the time budget) to finish.

The goal is to find Nintendo's "this game's DLC list" data source, so the
PlayStation-style per-game add-on pass can be built for Switch. Order history
gives a flat owned list with no parent link; the game page is where a game's DLC
is enumerated.

Capture at least two pages so we can see the game -> DLC relationship:
  1. an owned GAME's eShop page that HAS DLC (e.g. Vampire Survivors, DAVE THE
     DIVER, Sea of Stars). SCROLL DOWN to the "DLC" / "Add-ons" section so its
     fetch lands before you move on.
  2. one of that game's DLC/add-on pages (click into a DLC entry).

Each capture writes to .recon/ (gitignored):
  nintendo_store_NN_<slug>.html   rendered DOM of the page
  nintendo_store_NN_<slug>.json   the JSON / GraphQL responses that page fetched
  nintendo_store_index.json       url + files for every capture

The captured JSON is the prize: the page fetches the game's DLC list (likely a
graph.nintendo.com call), so the parent->DLC NSUID mapping is probably a clean
field there. Note the operationName + persistedQuery sha256Hash for each call.

Close any in-progress scrape browser first — the persistent profile (.pw-profile)
can only be opened by one Chromium context at a time.
"""
from __future__ import annotations

import json
import logging
import re

from scrapers.base import RECON_DIR, capturing_browser

logger = logging.getLogger(__name__)

# Open on the US eShop games landing page; search/navigate to one of your owned
# games that has DLC, then into one of its DLC entries.
START_URL = "https://www.nintendo.com/us/store/games/"

MAX_SECONDS = 900       # overall time budget; close the window to stop sooner
SETTLE_MS = 2500        # let a page's DLC-list fetches land before snapshotting
POLL_MS = 700           # how often to check the address bar
PRODUCT_MARKERS = ("/store/products/",)


def _slug(url: str) -> str:
    tail = url.rstrip("/").split("/")[-1]
    return re.sub(r"[^A-Za-z0-9._-]", "_", tail)[:60] or "page"


def _is_product(url: str) -> bool:
    return "nintendo.com" in url and any(m in url for m in PRODUCT_MARKERS)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    RECON_DIR.mkdir(parents=True, exist_ok=True)
    index: list[dict] = []
    captured_urls: set[str] = set()
    seen = 0
    elapsed = 0

    with capturing_browser(headless=False) as (page, captured):
        page.goto(START_URL)
        logger.info("\nBrowser open. Confirm you're logged in (avatar, top-right).")
        logger.info("Open an OWNED game that has DLC, SCROLL to its DLC/Add-ons")
        logger.info("section, then click into one of its DLC entries.")
        logger.info("Every eShop product page you open is captured automatically.")
        logger.info("Close the window when you're done.\n")

        while elapsed < MAX_SECONDS:
            try:
                url = page.url
            except Exception:  # window/context closed by the user -> finish up
                logger.info("browser closed; wrapping up.")
                break

            if _is_product(url) and url not in captured_urls:
                page.wait_for_timeout(SETTLE_MS)
                try:
                    if page.url != url:          # navigated again mid-settle; retry next loop
                        continue
                    html = page.content()
                except Exception:
                    break
                n = len(index) + 1
                slug = f"{n:02d}_{_slug(url)}"
                html_path = RECON_DIR / f"nintendo_store_{slug}.html"
                json_path = RECON_DIR / f"nintendo_store_{slug}.json"
                new_entries = captured[seen:]
                seen = len(captured)
                html_path.write_text(html, encoding="utf-8")
                json_path.write_text(
                    json.dumps(new_entries, ensure_ascii=False, indent=2), encoding="utf-8")
                index.append({"n": n, "url": url, "html": html_path.name,
                              "json": json_path.name, "responses": len(new_entries)})
                captured_urls.add(url)
                logger.info("captured #%d: %s", n, url)
                logger.info("  -> %s + %s (%d JSON responses)",
                            html_path.name, json_path.name, len(new_entries))

            page.wait_for_timeout(POLL_MS)
            elapsed += (SETTLE_MS + POLL_MS) / 1000 if url in captured_urls else POLL_MS / 1000

    (RECON_DIR / "nintendo_store_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("\nDone. %d page(s) captured to %s", len(index), RECON_DIR)


if __name__ == "__main__":
    main()
