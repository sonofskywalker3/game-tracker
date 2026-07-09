"""CLI to scrape vendor libraries into normalized JSON (scraped/<vendor>_<date>.json).

  python scrape_libraries.py --vendor playstation
  python scrape_libraries.py --vendor all
  python scrape_libraries.py --recon --vendor playstation   # capture raw HTML to .recon/

The scrape/recon flow opens a real (headed) browser using a persistent profile, so
you log in to each vendor once and the session is reused on later runs. Recon saves
the rendered library HTML to .recon/<vendor>.html (gitignored) for selector discovery.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import threading

import requests

from scrapers import nintendo, playstation, steam, xbox
from scrapers.base import (
    RECON_DIR,
    capturing_browser,
    scroll_until_idle,
    write_scrape,
)

logger = logging.getLogger(__name__)

SCRAPERS = {
    "playstation": playstation,
    "xbox": xbox,
    "nintendo": nintendo,
    "steam": steam,
}


def _wait_for_user(page, prompt: str) -> None:
    """Wait for the user to press Enter WITHOUT starving Playwright's event loop.

    A blocking input() freezes the sync Playwright connection, so interactive
    logins (especially OAuth popups / new tabs) stall and render blank until the
    script resumes. Instead we block on input() in a background thread and keep
    pumping the browser here so the page stays live while the user logs in.
    """
    print(prompt)
    done = threading.Event()

    def _waiter() -> None:
        try:
            input()
        except EOFError:
            pass
        done.set()

    threading.Thread(target=_waiter, daemon=True).start()
    while not done.is_set():
        page.wait_for_timeout(300)


def run_recon(vendor: str) -> None:
    mod = SCRAPERS[vendor]
    RECON_DIR.mkdir(parents=True, exist_ok=True)
    with capturing_browser(headless=False) as (page, captured):
        page.goto(mod.VENDOR_URL)
        _wait_for_user(page, f"Log in if needed, navigate to your {mod.SOURCE} library / "
                             f"purchase history, wait for it to load, then press Enter here... ")
        scroll_until_idle(page, captured)
        (RECON_DIR / f"{vendor}.html").write_text(page.content(), encoding="utf-8")
        out = RECON_DIR / f"{vendor}.responses.jsonl"
        with out.open("w", encoding="utf-8") as fh:
            for entry in captured:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        logger.info("saved %s.html + %d captured JSON responses -> %s",
                    vendor, len(captured), out.name)


# Timeout for the cloud import POST (the server runs enrichment; give it room).
_PUSH_TIMEOUT_S = 600


def push_scrape(payload: dict, base_url: str, token: str) -> dict:
    """POST a scrape payload to the cloud app's import endpoint. Returns the
    parsed JSON response; raises requests.HTTPError on a non-2xx status."""
    url = base_url.rstrip("/") + "/api/import/scrape"
    resp = requests.post(url, json=payload,
                         headers={"Authorization": f"Bearer {token}"},
                         timeout=_PUSH_TIMEOUT_S)
    resp.raise_for_status()
    return resp.json()


def run_scrape(vendor: str, *, push_url: str | None = None,
               push_token: str | None = None) -> None:
    mod = SCRAPERS[vendor]
    if not hasattr(mod, "collect"):
        raise SystemExit(f"{vendor} scraper not implemented yet (no collect()).")
    with capturing_browser(headless=False) as (page, captured):
        page.goto(mod.VENDOR_URL)
        _wait_for_user(page, f"Log in if needed, open your {mod.SOURCE} library / full "
                             f"purchase history, then press Enter here... ")
        games = mod.collect(page, captured)
    out_path = write_scrape(vendor, games)
    logger.info("scraped %d %s games", len(games), vendor)
    if push_url:
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        result = push_scrape(payload, push_url, push_token or "")
        logger.info("pushed %s -> %s", vendor, result.get("summary"))


def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Scrape vendor libraries to normalized JSON")
    parser.add_argument("--vendor", required=True, choices=[*SCRAPERS, "all"])
    parser.add_argument("--recon", action="store_true", help="save raw library HTML to .recon/")
    parser.add_argument("--push", metavar="URL", default=None,
                        help="POST the scrape to a cloud app's /api/import/scrape "
                             "(token from GAMETRACKER_IMPORT_TOKEN)")
    args = parser.parse_args(argv)
    push_token = os.environ.get("GAMETRACKER_IMPORT_TOKEN", "")
    if args.push and not push_token:
        parser.error("--push requires GAMETRACKER_IMPORT_TOKEN in the environment")
    vendors = list(SCRAPERS) if args.vendor == "all" else [args.vendor]
    for vendor in vendors:
        if args.recon:
            run_recon(vendor)
        else:
            run_scrape(vendor, push_url=args.push, push_token=push_token)


if __name__ == "__main__":
    main()
