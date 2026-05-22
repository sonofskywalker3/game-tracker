"""Shared scraper infrastructure: the ScrapedGame record, the persistent-browser
lifecycle, and the normalized-JSON writer/reader.

Scraping never imports models or touches the database — it only produces JSON.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Module-level path constants (mirror models.DB_PATH).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = PROJECT_ROOT / ".pw-profile"   # persistent Playwright login session
RECON_DIR = PROJECT_ROOT / ".recon"          # raw captured HTML (personal; gitignored)
SCRAPE_DIR = PROJECT_ROOT / "scraped"        # normalized JSON output (gitignored)

VALID_SOURCES = frozenset({"playstation", "xbox", "nintendo"})


@dataclass
class ScrapedGame:
    """One owned title as seen on a vendor library page."""
    title: str
    platform: str                       # canonical short_name, e.g. "PS5", "X360"
    source: str                         # one of VALID_SOURCES
    external_id: Optional[str] = None
    cover_url: Optional[str] = None
    source_title: Optional[str] = None  # exact vendor title; defaults to title
    status_hint: Optional[str] = None

    def __post_init__(self) -> None:
        if self.source_title is None:
            self.source_title = self.title


def write_scrape(source: str, games: list[ScrapedGame], out_dir: Path = SCRAPE_DIR) -> Path:
    """Write a normalized scrape file and return its path."""
    if source not in VALID_SOURCES:
        raise ValueError(f"unknown source: {source!r}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    payload = {
        "source": source,
        "scraped_at": now.isoformat(),
        "count": len(games),
        "games": [asdict(g) for g in games],
    }
    out_path = out_dir / f"{source}_{now:%Y%m%d}.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("wrote %d games to %s", len(games), out_path)
    return out_path


def read_scrape(path: Path) -> list[dict]:
    """Read the games list out of a normalized scrape file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["games"]


@contextmanager
def persistent_browser(headless: bool = False):
    """Yield a Playwright page backed by a persistent profile (login persists).

    Live shell — verified manually, not unit-tested. First run per vendor opens a
    real window for interactive login + 2FA; the session is reused thereafter.
    """
    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=headless,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            yield page
        finally:
            context.close()


def autoscroll(page, max_rounds: int = 60, pause_ms: int = 500) -> None:
    """Scroll to the bottom repeatedly to trigger lazy-loaded library items."""
    prev_height = 0
    for _ in range(max_rounds):
        page.mouse.wheel(0, 20000)
        page.wait_for_timeout(pause_ms)
        height = page.evaluate("document.body.scrollHeight")
        if height == prev_height:
            break
        prev_height = height
