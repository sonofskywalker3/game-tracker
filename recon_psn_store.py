"""Recon: capture logged-in PS Store product pages for DLC ownership discovery.

A headed browser opens reusing your saved PSN login (.pw-profile). Just browse —
every PS Store product/concept page you visit is auto-captured (no key presses).
Close the browser window (or wait for the time budget) to finish.

Capture at least three pages so the owned/not-owned signal can be diffed:
  1. a GAME's store page that has add-ons (it opens on the Final Fantasy XVI page),
  2. an add-on you OWN,
  3. an add-on you do NOT own (ideally for the same game as #2).

Each capture writes to .recon/ (gitignored):
  psn_store_NN_<slug>.html   rendered DOM of the page
  psn_store_NN_<slug>.json   the JSON / GraphQL responses that page fetched
  psn_store_index.json       url + files for every capture

The captured JSON is the prize: the SPA fetches entitlement/ownership data, so
the owned-vs-not signal is likely a clean field there rather than DOM-only.

Close any in-progress scrape browser first — the persistent profile (.pw-profile)
can only be opened by one Chromium context at a time.
"""
from __future__ import annotations

import json
import logging
import re

from scrapers.base import RECON_DIR, capturing_browser

logger = logging.getLogger(__name__)

# Open straight onto a game page so the add-ons section is visible immediately;
# search/navigate from here to your owned and not-owned add-ons.
START_URL = ("https://store.playstation.com/en-us/product/"
             "UP0082-PPSA10664_00-FF16SIEA00000002")

MAX_SECONDS = 900       # overall time budget; close the window to stop sooner
SETTLE_MS = 2000        # let a page's entitlement fetches land before snapshotting
POLL_MS = 700           # how often to check the address bar
PRODUCT_MARKERS = ("/product/", "/concept/")


def _slug(url: str) -> str:
    tail = url.rstrip("/").split("/")[-1]
    return re.sub(r"[^A-Za-z0-9._-]", "_", tail)[:60] or "page"


def _is_product(url: str) -> bool:
    return "store.playstation.com" in url and any(m in url for m in PRODUCT_MARKERS)


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
        logger.info("Browse to a game page, an OWNED add-on, and a NOT-owned add-on.")
        logger.info("Every store product page you open is captured automatically.")
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
                html_path = RECON_DIR / f"psn_store_{slug}.html"
                json_path = RECON_DIR / f"psn_store_{slug}.json"
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

    (RECON_DIR / "psn_store_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("\nDone. %d page(s) captured to %s", len(index), RECON_DIR)


if __name__ == "__main__":
    main()
