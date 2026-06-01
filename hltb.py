"""HowLongToBeat enrichment. Unofficial JSON endpoint; degrades gracefully.

Never raises on network/parse failure — returns None so eligibility can fall back
to genre rules. Matches by normalized title; reuses models.normalize_title.
"""
from __future__ import annotations

import logging
import sqlite3
import time

import requests

from models import normalize_title

logger = logging.getLogger(__name__)

BASE_URL = "https://howlongtobeat.com"
INIT_URL = BASE_URL + "/api/bleed/init"
SEARCH_URL = BASE_URL + "/api/bleed"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": BASE_URL + "/",
    "Origin": BASE_URL,
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


def _search_body(title: str, hp_key: str, hp_val: str) -> dict:
    """Build the HLTB /api/bleed POST body with honeypot field."""
    body: dict = {
        "searchType": "games",
        "searchTerms": title.split(),
        "searchPage": 1,
        "size": 20,
        "searchOptions": {
            "games": {
                "userId": 0,
                "platform": "",
                "sortCategory": "popular",
                "rangeCategory": "main",
                "rangeTime": {"min": None, "max": None},
                "gameplay": {"perspective": "", "flow": "", "genre": "", "difficulty": ""},
                "rangeYear": {"min": "", "max": ""},
                "modifier": "",
            },
            "users": {"sortCategory": "postcount"},
            "lists": {"sortCategory": "follows"},
            "filter": "",
            "sort": 0,
            "randomizer": 0,
        },
        "useCache": True,
    }
    body[hp_key] = hp_val
    return body


def fetch_durations(title: str) -> dict | None:
    """Search HLTB for `title`; return duration dict or None.

    Performs the /api/bleed/init token handshake then POSTs to /api/bleed.
    Re-fetches the token once on a 403 response and retries.

    Returns: {"hltb_id", "hltb_main_minutes", "hltb_main_extra_minutes",
              "hltb_completionist_minutes"} or None if no match / error.
    """
    try:
        session = requests.Session()
        session.headers.update(HEADERS)

        sec = session.get(f"{INIT_URL}?t={int(time.time() * 1000)}", timeout=TIMEOUT_SECONDS)
        sec.raise_for_status()
        init = sec.json()
        token, hp_key, hp_val = init["token"], init["hpKey"], init["hpVal"]

        def _do_search(tok: str, k: str, v: str) -> requests.Response:
            return session.post(
                SEARCH_URL,
                json=_search_body(title, k, v),
                headers={"x-auth-token": tok, "x-hp-key": k, "x-hp-val": v},
                timeout=TIMEOUT_SECONDS,
            )

        resp = _do_search(token, hp_key, hp_val)
        if resp.status_code == 403:
            sec2 = session.get(f"{INIT_URL}?t={int(time.time() * 1000)}", timeout=TIMEOUT_SECONDS)
            init = sec2.json()
            token, hp_key, hp_val = init["token"], init["hpKey"], init["hpVal"]
            resp = _do_search(token, hp_key, hp_val)

        resp.raise_for_status()
        data = resp.json().get("data") or []
    except (requests.RequestException, ValueError, KeyError) as exc:
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
