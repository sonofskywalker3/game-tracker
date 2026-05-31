"""The Slate engine: per-slot eligibility/ranking + pin/outcome lifecycle.

Ranking reuses recommendation.calculate_tag_affinity for taste signal and layers
slot-specific hard filters (platform, latency) + a session/length fit nudge +
a genre-fatigue penalty from recent slot_history.
"""
from __future__ import annotations

import json
import sqlite3

from recommendation import calculate_tag_affinity
from slot_signals import latency_tolerant

# Statuses that mean "not a candidate to start" (already done or actively elsewhere).
FINISHED_STATUSES = frozenset({"completed", "100", "dropped"})
# Recent-history window for genre-fatigue penalty.
FATIGUE_RECENT_COUNT = 5
FATIGUE_PENALTY = 20.0


def _game_tag_names(conn: sqlite3.Connection, game_id: int) -> set[str]:
    rows = conn.execute(
        "SELECT t.name FROM game_tags gt JOIN tags t ON t.id = gt.tag_id WHERE gt.game_id = ?",
        (game_id,)).fetchall()
    return {r["name"] for r in rows}


def _recent_fatigue_tags(conn: sqlite3.Connection) -> set[str]:
    """Tags of the few most-recently-removed slot_history games (genre fatigue)."""
    rows = conn.execute(
        "SELECT game_id FROM slot_history ORDER BY removed_at DESC LIMIT ?",
        (FATIGUE_RECENT_COUNT,)).fetchall()
    tags: set[str] = set()
    for r in rows:
        tags |= _game_tag_names(conn, r["game_id"])
    return tags


def _pinned_game_ids(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute(
        "SELECT current_game_id FROM slots WHERE current_game_id IS NOT NULL").fetchall()
    return {r["current_game_id"] for r in rows}


def rank_candidates(conn: sqlite3.Connection, slot: dict, limit: int = 10) -> list[dict]:
    """Return ranked eligible games for a slot: [{"game", "score", "reasons"}]."""
    platforms = set(json.loads(slot["platforms"])) if slot.get("platforms") else set()
    requires_low_latency = bool(slot["requires_low_latency"])
    affinity = calculate_tag_affinity(conn)
    fatigue_tags = _recent_fatigue_tags(conn)
    pinned = _pinned_game_ids(conn)

    placeholders = ",".join("?" * len(FINISHED_STATUSES))
    rows = conn.execute(f"""
        SELECT g.*, ur.status, ur.priority, ur.hours_played
        FROM games g
        JOIN user_ratings ur ON ur.game_id = g.id
        WHERE ur.status NOT IN ({placeholders})
    """, tuple(FINISHED_STATUSES)).fetchall()

    out = []
    for game in rows:
        if game["id"] in pinned:
            continue
        # Platform hard filter
        game_platforms = {
            r["short_name"] for r in conn.execute(
                "SELECT p.short_name FROM game_platforms gp "
                "JOIN platforms p ON p.id = gp.platform_id WHERE gp.game_id = ?",
                (game["id"],)).fetchall()}
        if platforms and not (game_platforms & platforms):
            continue
        # Latency hard filter
        tag_names = _game_tag_names(conn, game["id"])
        tolerant = latency_tolerant(tag_names, game["input_lag_override"])
        if requires_low_latency and tolerant:
            # Garage slot: wants games that NEED low latency (lag-sensitive only)
            continue
        if not requires_low_latency and not tolerant:
            # Stream-safe/couch slots: exclude lag-sensitive games
            continue

        score = 50.0
        reasons = []
        priority = game["priority"] or 5
        score += (priority - 5) * 5
        if priority >= 7:
            reasons.append(f"High priority ({priority}/10)")
        # Taste signal from tag affinity
        tag_boost = 0.0
        for t in tag_names:
            for data in affinity.values():
                if data["name"] == t and data["avg_rating"] >= 7:
                    tag_boost += data["score"] * 0.5
        score += min(tag_boost, 15)
        if tag_boost:
            reasons.append("Matches your taste")
        # Genre fatigue penalty
        if tag_names & fatigue_tags:
            score -= FATIGUE_PENALTY
            reasons.append("Similar to what you just finished")

        out.append({"game": dict(game), "score": round(score, 1), "reasons": reasons})

    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:limit]
