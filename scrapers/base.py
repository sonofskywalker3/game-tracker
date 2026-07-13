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
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from collections.abc import Iterator

    from playwright.sync_api import Page

logger = logging.getLogger(__name__)

# Module-level path constants (mirror models.DB_PATH).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = PROJECT_ROOT / ".pw-profile"   # persistent Playwright login session
RECON_DIR = PROJECT_ROOT / ".recon"          # raw captured HTML (personal; gitignored)
SCRAPE_DIR = PROJECT_ROOT / "scraped"        # normalized JSON output (gitignored)

VALID_SOURCES = frozenset({"playstation", "xbox", "nintendo", "steam"})

# Prefer a real installed browser (vendor logins like Nintendo block bundled
# Chromium's automation fingerprint); fall back to bundled Chromium.
BROWSER_CHANNELS = ("chrome", "msedge")
# Hides the navigator.webdriver / AutomationControlled signal many logins check.
LAUNCH_ARGS = ("--disable-blink-features=AutomationControlled", "--start-maximized")
# Default switches Playwright adds that we suppress. "--enable-automation" drives
# the "browser is controlled by automated software" banner and is a common login
# bot-check trigger (Nintendo's authorize step returns 400 "invalid params" with
# it present); removing it is safe for the working PS/Xbox flows.
IGNORE_DEFAULT_ARGS = ("--enable-automation",)


def _launch_context(p, headless: bool, profile_dir: Path | None = None):
    """Launch a persistent context, trying real browser channels first."""
    user_data_dir = str(profile_dir or PROFILE_DIR)
    for channel in BROWSER_CHANNELS:
        try:
            return p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir, headless=headless,
                channel=channel, args=list(LAUNCH_ARGS),
                ignore_default_args=list(IGNORE_DEFAULT_ARGS),
            )
        except Exception as exc:  # channel not installed on this machine
            logger.debug("browser channel %s unavailable: %s", channel, exc)
    logger.info("falling back to bundled Chromium (no installed Chrome/Edge found)")
    return p.chromium.launch_persistent_context(
        user_data_dir=user_data_dir, headless=headless, args=list(LAUNCH_ARGS),
        ignore_default_args=list(IGNORE_DEFAULT_ARGS),
    )


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
    kind: str = "game"                  # "game" | "addon" (add-ons attach to a game's DLC)
    url_key: Optional[str] = None       # vendor store slug (Nintendo eShop urlKey), if known

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
    # Date-only filename: re-scraping the same vendor on the same day overwrites
    # (latest-wins), which keeps directory-based imports from processing stale dupes.
    out_path = out_dir / f"{source}_{now:%Y%m%d}.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("wrote %d games to %s", len(games), out_path)
    return out_path


def read_scrape(path: Path) -> list[dict]:
    """Read the games list out of a normalized scrape file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["games"]


@contextmanager
def persistent_browser(headless: bool = False) -> Iterator[Page]:
    """Yield a Playwright page backed by a persistent profile (login persists).

    Live shell — verified manually, not unit-tested. First run per vendor opens a
    real window for interactive login + 2FA; the session is reused thereafter.
    """
    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context = _launch_context(p, headless)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            yield page
        finally:
            context.close()


def autoscroll(page: Page, max_rounds: int = 60, pause_ms: int = 500) -> None:
    """Scroll to the bottom repeatedly to trigger lazy-loaded library items."""
    prev_height = 0
    for _ in range(max_rounds):
        page.mouse.wheel(0, 20000)
        page.wait_for_timeout(pause_ms)
        height = page.evaluate("document.body.scrollHeight")
        if height == prev_height:
            break
        prev_height = height
    else:
        logger.warning("autoscroll hit max_rounds=%d without a stable height", max_rounds)


@contextmanager
def capturing_browser(headless: bool = False, profile_dir: Path | None = None):
    """Persistent browser that records JSON XHR/fetch responses seen in the session.

    Yields ``(page, captured)`` where ``captured`` is a growing list of
    ``{"url", "status", "body"}`` dicts. Used for API-backed sites (virtualized
    SPAs) whose game list is fetched as JSON rather than rendered into the HTML.
    Registered at the context level so responses from popups (e.g. OAuth login)
    are captured too.
    """
    from playwright.sync_api import sync_playwright

    (profile_dir or PROFILE_DIR).mkdir(parents=True, exist_ok=True)
    captured: list[dict] = []

    def _on_response(response) -> None:
        try:
            if response.request.resource_type not in ("xhr", "fetch"):
                return
            ctype = (response.headers or {}).get("content-type", "")
            if "json" not in ctype and "graphql" not in response.url:
                return
            try:
                req_headers = dict(response.request.all_headers())
            except Exception:
                req_headers = dict(response.request.headers)
            captured.append({
                "url": response.url,
                "status": response.status,
                "body": response.text(),
                "request_headers": req_headers,
            })
        except Exception as exc:  # body may be unavailable (redirects, aborted, etc.)
            logger.debug("skip response %s: %s", getattr(response, "url", "?"), exc)

    with sync_playwright() as p:
        context = _launch_context(p, headless, profile_dir)
        context.on("response", _on_response)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            yield page, captured
        finally:
            context.close()


def scroll_until_idle(page: Page, captured: list, *, max_rounds: int = 80,
                      pause_ms: int = 700, idle_rounds: int = 4) -> None:
    """Scroll until no new captured responses arrive for several rounds.

    Lazy/paginated lists fetch more as you scroll; waiting for the capture count
    to stop growing is a more reliable "fully loaded" signal than scroll height.
    """
    stable, last = 0, len(captured)
    for _ in range(max_rounds):
        page.mouse.wheel(0, 25000)
        page.wait_for_timeout(pause_ms)
        if len(captured) == last:
            stable += 1
            if stable >= idle_rounds:
                return
        else:
            stable, last = 0, len(captured)


def capture_request_headers(page, url_substring: str, trigger,
                            *, timeout_ms: int = 15000) -> dict:
    """Run `trigger` and capture the first matching XHR/fetch request's headers.

    Lets us reuse the page's own auth (e.g. PSN's Authorization bearer) when
    replaying an API for pagination, since cookies alone don't authenticate the
    cross-origin np.playstation.com endpoints.
    """
    holder: dict = {}

    def _on_request(req) -> None:
        if not holder and url_substring in req.url:
            holder.update(req.headers)

    page.on("request", _on_request)
    try:
        trigger()
        waited = 0
        while not holder and waited < timeout_ms:
            page.wait_for_timeout(250)
            waited += 250
    finally:
        page.remove_listener("request", _on_request)
    return holder


# Request headers we must NOT replay (the API client manages these itself).
SKIP_REPLAY_HEADERS = frozenset({
    "host", "content-length", "connection", "accept-encoding", "cookie",
})


def replay_headers(headers: dict) -> dict:
    """Captured request headers safe to replay (drops auto-managed + pseudo headers).

    Replaying the page's full header set (content-type, x-*, apollo-*, origin,
    etc.) is what gets past API CSRF checks; cookies are dropped because
    page.request sends the browser context's cookie jar itself.
    """
    return {
        key: value
        for key, value in (headers or {}).items()
        if key.lower() not in SKIP_REPLAY_HEADERS and not key.startswith(":")
    }


def auth_from_captured(captured: list, url_substring: str) -> dict:
    """Find a captured request matching url_substring; return its replayable headers.

    Uses the headers the page already sent (captured passively during the
    session), which is more reliable than trying to re-trigger the request.
    """
    for entry in captured or []:
        if url_substring in entry.get("url", ""):
            hdrs = replay_headers(entry.get("request_headers") or {})
            if hdrs:
                return hdrs
    return {}
