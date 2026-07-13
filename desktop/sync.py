"""Push scraped payloads to a BacklogQuest server, one vendor at a time."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import requests

from scrape_libraries import push_scrape

_TOKEN_REJECTED = "token rejected — check your token"
_SERVER_BUSY = "server busy — your CSV is safe, try sync again"

Pusher = Callable[[dict, str, str], dict]


@dataclass
class SyncResult:
    source: str
    ok: bool
    summary: str
    retryable: bool


def _failure(source: str, exc: Exception) -> SyncResult:
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        if exc.response.status_code == 401:
            return SyncResult(source, False, _TOKEN_REJECTED, retryable=False)
        return SyncResult(source, False, f"{_SERVER_BUSY} (HTTP {exc.response.status_code})",
                          retryable=True)
    return SyncResult(source, False, f"{_SERVER_BUSY} ({type(exc).__name__})", retryable=True)


def sync_payloads(payloads: list[dict], server_url: str, token: str,
                  push: Pusher = push_scrape) -> list[SyncResult]:
    """Sync every payload; a vendor failure never stops the others."""
    results: list[SyncResult] = []
    for payload in payloads:
        source = str(payload.get("source", "?"))
        try:
            response = push(payload, server_url, token)
            results.append(SyncResult(source, True, str(response.get("summary", "done")),
                                      retryable=False))
        except (requests.RequestException, OSError, ValueError) as exc:
            results.append(_failure(source, exc))
    return results
