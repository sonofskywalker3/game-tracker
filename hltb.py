"""HowLongToBeat enrichment. Unofficial JSON endpoint; degrades gracefully.

Never raises on network/parse failure — returns None so eligibility can fall back
to genre rules. Matches by normalized title; reuses models.normalize_title.
"""
from __future__ import annotations

import logging
import sqlite3

import requests

from models import normalize_title

logger = logging.getLogger(__name__)

SEARCH_URL = "https://howlongtobeat.com/api/search"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Game-Tracker)",
    "Referer": "https://howlongtobeat.com/",
    "Content-Type": "application/json",
}
TIMEOUT_SECONDS = 15


def _minutes(seconds: int | None) -> int | None:
    """Convert HLTB seconds to whole minutes; 0/None/falsey -> None (unknown)."""
    if not seconds:
        return None
    return int(seconds) // 60


def _best_match(candidates: list[dict], query: str) -> dict | None:
    """Pick the candidate whose normalized name equals the query, else the first."""
    norm_query = normalize_title(query)
    for c in candidates:
        if normalize_title(c.get("game_name", "")) == norm_query:
            return c
    return candidates[0] if candidates else None


def fetch_durations(title: str) -> dict | None:
    """Search HLTB for `title`; return duration dict or None.

    Returns: {"hltb_id", "hltb_main_minutes", "hltb_main_extra_minutes",
              "hltb_completionist_minutes"} or None if no match / error.
    """
    payload = {
        "searchType": "games",
        "searchTerms": title.split(),
        "searchPage": 1,
        "size": 20,
    }
    try:
        resp = requests.post(SEARCH_URL, json=payload, headers=HEADERS, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json().get("data") or []
    except (requests.RequestException, ValueError) as exc:
        logger.warning("HLTB search failed for %r: %s", title, exc)
        return None

    match = _best_match(data, title)
    if not match:
        return None
    game_id = match.get("game_id")
    return {
        "hltb_id": str(game_id) if game_id is not None else None,
        "hltb_main_minutes": _minutes(match.get("comp_main")),
        "hltb_main_extra_minutes": _minutes(match.get("comp_plus")),
        "hltb_completionist_minutes": _minutes(match.get("comp_100")),
    }


def enrich_game(conn: sqlite3.Connection, game_id: int) -> bool:
    """Fetch + persist HLTB durations for one game. Returns True if matched.

    Commits on a successful write; leaves columns untouched on a miss.
    """
    title = conn.execute("SELECT title FROM games WHERE id = ?", (game_id,)).fetchone()
    if title is None:
        return False
    result = fetch_durations(title["title"])
    if result is None:
        return False
    conn.execute(
        "UPDATE games SET hltb_id = ?, hltb_main_minutes = ?, "
        "hltb_main_extra_minutes = ?, hltb_completionist_minutes = ?, "
        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (result["hltb_id"], result["hltb_main_minutes"],
         result["hltb_main_extra_minutes"], result["hltb_completionist_minutes"], game_id))
    conn.commit()
    return True


def enrich_missing(conn: sqlite3.Connection) -> dict:
    """Enrich every game lacking an hltb_id. Returns {"matched": n, "missed": n}."""
    rows = conn.execute("SELECT id FROM games WHERE hltb_id IS NULL ORDER BY id").fetchall()
    matched = missed = 0
    for row in rows:
        if enrich_game(conn, row["id"]):
            matched += 1
        else:
            missed += 1
    logger.info("HLTB enrich: %d matched, %d missed", matched, missed)
    return {"matched": matched, "missed": missed}
