"""Shared recon engine: capture logged-in vendor store pages for data discovery.

A headed browser opens reusing the saved vendor login (.pw-profile). The user
just browses — every store product page visited is auto-captured (no key
presses). Closing the browser window (or exhausting the time budget) finishes
the session.

Each capture writes to .recon/ (gitignored):
  <prefix>_NN_<slug>.html   rendered DOM of the page
  <prefix>_NN_<slug>.json   the JSON / GraphQL responses that page fetched
  <prefix>_index.json       url + files for every capture

Per-vendor behavior (start URL, product-page detection, settle time, file
prefix, browsing guidance) is declarative: build a StoreRecon and call run().
See recon_psn_store.py / recon_nintendo_store.py for the vendor entry points.

Close any in-progress scrape browser first — the persistent profile
(.pw-profile) can only be opened by one Chromium context at a time.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from scrapers.base import RECON_DIR, capturing_browser

logger = logging.getLogger(__name__)

MAX_SECONDS = 900       # overall time budget; close the window to stop sooner
POLL_MS = 700           # how often to check the address bar


@dataclass(frozen=True)
class StoreRecon:
    """Declarative per-vendor recon configuration."""
    file_prefix: str                  # capture/index file-name prefix, e.g. "psn_store"
    start_url: str                    # page the browser opens on
    host_marker: str                  # url substring identifying the vendor store
    product_markers: tuple[str, ...]  # url substrings identifying a product page
    settle_ms: int                    # let a page's data fetches land before snapshotting
    instructions: tuple[str, ...]     # logged browsing guidance, one line each
    max_seconds: int = MAX_SECONDS


def _slug(url: str) -> str:
    tail = url.rstrip("/").split("/")[-1]
    return re.sub(r"[^A-Za-z0-9._-]", "_", tail)[:60] or "page"


def _is_product(cfg: StoreRecon, url: str) -> bool:
    return cfg.host_marker in url and any(m in url for m in cfg.product_markers)


def run(cfg: StoreRecon, *, browser_factory=capturing_browser,
        now: Callable[[], float] = time.monotonic) -> None:
    """Drive one recon session: open the store, capture every product page the
    user visits, write the index on exit. browser_factory/now are test seams.

    The time budget is wall-clock: a monotonic start time is compared against
    `now()` each loop, so waiting (settle or poll) counts exactly once — never
    over-billed for sitting on an already-captured page, never under-billed on
    the mid-settle retry path.
    """
    RECON_DIR.mkdir(parents=True, exist_ok=True)
    index: list[dict] = []
    captured_urls: set[str] = set()
    seen = 0
    started = now()

    with browser_factory(headless=False) as (page, captured):
        page.goto(cfg.start_url)
        logger.info("\nBrowser open. Confirm you're logged in (avatar, top-right).")
        for line in cfg.instructions:
            logger.info(line)
        logger.info("Close the window when you're done.\n")

        while now() - started < cfg.max_seconds:
            try:
                url = page.url
            except Exception:  # window/context closed by the user -> finish up
                logger.info("browser closed; wrapping up.")
                break

            if _is_product(cfg, url) and url not in captured_urls:
                page.wait_for_timeout(cfg.settle_ms)
                try:
                    if page.url != url:          # navigated again mid-settle; retry next loop
                        continue
                    html = page.content()
                except Exception:
                    break
                n = len(index) + 1
                slug = f"{n:02d}_{_slug(url)}"
                html_path = RECON_DIR / f"{cfg.file_prefix}_{slug}.html"
                json_path = RECON_DIR / f"{cfg.file_prefix}_{slug}.json"
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

    (RECON_DIR / f"{cfg.file_prefix}_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("\nDone. %d page(s) captured to %s", len(index), RECON_DIR)
