"""Launch-time soft update check (vendor pages rot; stale scrapers need a nudge)."""
from __future__ import annotations

import json
import logging
from collections.abc import Callable

import requests

logger = logging.getLogger(__name__)

APP_VERSION = "0.1.2"
_TIMEOUT_S = 5

Fetcher = Callable[[str, int], str]


def _http_fetch(url: str, timeout: int) -> str:
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def parse_version(s: str) -> tuple[int, ...]:
    return tuple(int(part) for part in s.strip().split("."))


def check_for_update(server_url: str, fetch: Fetcher = _http_fetch,
                     current: str = APP_VERSION) -> str | None:
    """Return the server's version string when it is newer; None otherwise/on error."""
    url = server_url.rstrip("/") + "/api/scraper/version"
    try:
        latest = str(json.loads(fetch(url, _TIMEOUT_S))["version"])
        return latest if parse_version(latest) > parse_version(current) else None
    except Exception as exc:
        logger.info("update check skipped: %s", exc)
        return None
