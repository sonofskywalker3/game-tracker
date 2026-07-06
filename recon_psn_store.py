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

import logging

from recon_store import StoreRecon, run

# Open straight onto a game page so the add-ons section is visible immediately;
# search/navigate from here to your owned and not-owned add-ons.
START_URL = ("https://store.playstation.com/en-us/product/"
             "UP0082-PPSA10664_00-FF16SIEA00000002")

SETTLE_MS = 2000        # let a page's entitlement fetches land before snapshotting

CONFIG = StoreRecon(
    file_prefix="psn_store",
    start_url=START_URL,
    host_marker="store.playstation.com",
    product_markers=("/product/", "/concept/"),
    settle_ms=SETTLE_MS,
    instructions=(
        "Browse to a game page, an OWNED add-on, and a NOT-owned add-on.",
        "Every store product page you open is captured automatically.",
    ),
)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run(CONFIG)


if __name__ == "__main__":
    main()
