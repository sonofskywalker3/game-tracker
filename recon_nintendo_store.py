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

import logging

from recon_store import StoreRecon, run

# Open on the US eShop games landing page; search/navigate to one of your owned
# games that has DLC, then into one of its DLC entries.
START_URL = "https://www.nintendo.com/us/store/games/"

SETTLE_MS = 2500        # let a page's DLC-list fetches land before snapshotting

CONFIG = StoreRecon(
    file_prefix="nintendo_store",
    start_url=START_URL,
    host_marker="nintendo.com",
    product_markers=("/store/products/",),
    settle_ms=SETTLE_MS,
    instructions=(
        "Open an OWNED game that has DLC, SCROLL to its DLC/Add-ons",
        "section, then click into one of its DLC entries.",
        "Every eShop product page you open is captured automatically.",
    ),
)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run(CONFIG)


if __name__ == "__main__":
    main()
