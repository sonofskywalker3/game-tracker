"""The Slate engine: per-slot eligibility/ranking + pin/outcome lifecycle.

Ranking reuses recommendation.calculate_tag_affinity for taste signal and layers
slot-specific hard filters (platform, latency) + a session/length fit nudge +
a genre-fatigue penalty from recent slot_history.
"""
from __future__ import annotations

import json
import sqlite3

from recommendation import calculate_tag_affinity
from slot_signals import session_tolerant, latency_tolerant, effective_time_to_beat_minutes

# Statuses that mean "not a candidate to start" (already done or actively elsewhere).
FINISHED_STATUSES = frozenset({"completed", "100", "dropped"})
# Recent-history window for genre-fatigue penalty.
FATIGUE_RECENT_COUNT = 5
FATIGUE_PENALTY = 20.0
SESSION_MISMATCH_PENALTY = 25.0
STARTED_BOOST = 1000.0
TTB_REFERENCE_MINUTES = 1200   # 20h: pivot between "short" and "long"
TTB_WEIGHT = 0.02              # score points per minute of deviation
TTB_TERM_CAP = 20.0
SERIES_BOOST = 30.0


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


def _slot_recent_series_id(conn: sqlite3.Connection, slot_id: int) -> int | None:
    """series_id of the most recent game that passed through this slot, or None."""
    row = conn.execute("""
        SELECT ur.series_id
        FROM slot_history h JOIN user_ratings ur ON ur.game_id = h.game_id
        WHERE h.slot_id = ? AND ur.series_id IS NOT NULL
        ORDER BY h.removed_at DESC LIMIT 1
    """, (slot_id,)).fetchone()
    return row["series_id"] if row else None


def _pinned_game_ids(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute(
        "SELECT current_game_id FROM slots WHERE current_game_id IS NOT NULL").fetchall()
    return {r["current_game_id"] for r in rows}


def dismiss_suggestion(conn: sqlite3.Connection, slot_id: int, game_id: int) -> None:
    """Hide a game from a slot's suggestion list until the slot's game changes."""
    conn.execute(
        "INSERT OR IGNORE INTO slot_dismissals (slot_id, game_id) VALUES (?, ?)",
        (slot_id, game_id))
    conn.commit()


def _clear_dismissals(conn: sqlite3.Connection, slot_id: int) -> None:
    conn.execute("DELETE FROM slot_dismissals WHERE slot_id = ?", (slot_id,))


def _dismissed_game_ids(conn: sqlite3.Connection, slot_id: int) -> set[int]:
    return {r["game_id"] for r in conn.execute(
        "SELECT game_id FROM slot_dismissals WHERE slot_id = ?", (slot_id,)).fetchall()}


def rank_candidates(conn: sqlite3.Connection, slot: dict, limit: int = 10) -> list[dict]:
    """Return ranked eligible games for a slot: [{"game", "score", "reasons"}]."""
    platforms = set(json.loads(slot["platforms"])) if slot.get("platforms") else set()
    streamable_only = bool(slot["streamable_only"])
    affinity = calculate_tag_affinity(conn)
    fatigue_tags = _recent_fatigue_tags(conn)
    pinned = _pinned_game_ids(conn)
    dismissed = _dismissed_game_ids(conn, slot["id"]) if slot.get("id") else set()

    placeholders = ",".join("?" * len(FINISHED_STATUSES))
    rows = conn.execute(f"""
        SELECT g.*, ur.status, ur.priority, ur.hours_played, ur.series_id
        FROM games g
        JOIN user_ratings ur ON ur.game_id = g.id
        WHERE ur.status NOT IN ({placeholders})
    """, tuple(FINISHED_STATUSES)).fetchall()

    recent_series_id = _slot_recent_series_id(conn, slot["id"]) if slot.get("id") else None

    out = []
    for game in rows:
        if game["id"] in pinned:
            continue
        if game["id"] in dismissed:
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
        if streamable_only and not tolerant:
            continue          # streamed slot: drop lag-sensitive games

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
        # Session-tolerance penalty for short-session slots
        max_session = slot.get("max_session_minutes")
        min_session = slot.get("min_session_minutes")
        if max_session is not None and not session_tolerant(tag_names):
            score -= SESSION_MISMATCH_PENALTY
            reasons.append("May not suit a short session")
        # Directional time-to-beat term
        ttb = effective_time_to_beat_minutes(game)
        if ttb is not None:
            if max_session is not None:
                term = max(-TTB_TERM_CAP, min(TTB_TERM_CAP, (TTB_REFERENCE_MINUTES - ttb) * TTB_WEIGHT))
                if term:
                    score += term
                    reasons.append("Short play" if term > 0 else "Long for a quick session")
            elif min_session is not None:
                term = max(-TTB_TERM_CAP, min(TTB_TERM_CAP, (ttb - TTB_REFERENCE_MINUTES) * TTB_WEIGHT))
                if term:
                    score += term
                    reasons.append("Meaty play" if term > 0 else "Short for a long session")
        # Boost in-progress games to the top when prioritize_started is enabled
        if slot.get("prioritize_started") and game["status"] == "playing":
            score += STARTED_BOOST
            reasons.append("Continue playing")
        # Series momentum: boost games that share a series with the slot's last play
        if recent_series_id is not None and game["series_id"] == recent_series_id:
            score += SERIES_BOOST
            reasons.append("Next in this series")

        out.append({"game": dict(game), "score": round(score, 1), "reasons": reasons})

    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:limit]


OUTCOME_STATUS: dict[str, str] = {"beat": "completed", "complete": "100", "dropped": "dropped"}


def pin_game(conn: sqlite3.Connection, slot_id: int, game_id: int,
             goal: str | None = None) -> None:
    """Assign a game (+ goal) to a slot. Replaces any current game in that slot."""
    conn.execute(
        "UPDATE slots SET current_game_id = ?, goal = ? WHERE id = ?",
        (game_id, goal, slot_id))
    _clear_dismissals(conn, slot_id)
    conn.commit()


def _set_status(conn: sqlite3.Connection, game_id: int, status: str) -> None:
    conn.execute("""
        INSERT INTO user_ratings (game_id, status) VALUES (?, ?)
        ON CONFLICT(game_id) DO UPDATE SET status = excluded.status,
            updated_at = CURRENT_TIMESTAMP
    """, (game_id, status))


def _log_history(conn: sqlite3.Connection, slot_id: int, game_id: int,
                 goal: str | None, outcome: str) -> None:
    conn.execute(
        "INSERT INTO slot_history (slot_id, game_id, goal, outcome) VALUES (?, ?, ?, ?)",
        (slot_id, game_id, goal, outcome))


def _clear_slot(conn: sqlite3.Connection, slot_id: int) -> None:
    conn.execute("UPDATE slots SET current_game_id = NULL, goal = NULL WHERE id = ?", (slot_id,))


def apply_outcome(conn: sqlite3.Connection, slot_id: int, outcome: str, *,
                  chase: bool = False, new_goal: str | None = None) -> None:
    """Apply a slot outcome.

    outcome:
      'beat'     -> status 'completed'. chase=True keeps the game slotted with
                    new_goal; chase=False frees the slot + logs history 'shelved'.
      'complete' -> status '100', free slot, history 'completed'.
      'dropped'  -> status 'dropped', free slot, history 'dropped'.
      'swap'     -> free slot, NO history, NO status change.
    """
    slot = conn.execute("SELECT current_game_id, goal FROM slots WHERE id = ?",
                        (slot_id,)).fetchone()
    if slot is None or slot["current_game_id"] is None:
        return
    game_id: int = slot["current_game_id"]
    goal: str | None = slot["goal"]

    if outcome == "swap":
        _clear_dismissals(conn, slot_id)
        _clear_slot(conn, slot_id)
        conn.commit()
        return

    if outcome == "beat":
        _set_status(conn, game_id, OUTCOME_STATUS["beat"])
        if chase:
            conn.execute("UPDATE slots SET goal = ? WHERE id = ?", (new_goal, slot_id))
        else:
            _log_history(conn, slot_id, game_id, goal, "shelved")
            _clear_dismissals(conn, slot_id)
            _clear_slot(conn, slot_id)
        conn.commit()
        return

    if outcome in ("complete", "dropped"):
        _set_status(conn, game_id, OUTCOME_STATUS[outcome])
        history_outcome = "completed" if outcome == "complete" else "dropped"
        _log_history(conn, slot_id, game_id, goal, history_outcome)
        _clear_dismissals(conn, slot_id)
        _clear_slot(conn, slot_id)
        conn.commit()
        return

    raise ValueError(f"unknown outcome: {outcome!r}")


def recently_finished(conn: sqlite3.Connection, limit: int = 6) -> list[dict]:
    """Most-recently removed slot_history rows joined to game title/cover."""
    rows = conn.execute("""
        SELECT h.outcome, h.removed_at, g.id AS game_id, g.title, g.cover_url
        FROM slot_history h JOIN games g ON g.id = h.game_id
        ORDER BY h.removed_at DESC LIMIT ?
    """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_slots_state(conn: sqlite3.Connection, candidate_limit: int = 8) -> list[dict]:
    """Full slate state: each slot dict + its current_game dict + ranked candidates."""
    slot_rows = conn.execute("SELECT * FROM slots ORDER BY sort_order, id").fetchall()
    state = []
    for row in slot_rows:
        slot = dict(row)
        current_game = None
        if slot["current_game_id"]:
            g = conn.execute("SELECT * FROM games WHERE id = ?",
                             (slot["current_game_id"],)).fetchone()
            current_game = dict(g) if g else None
        slot["current_game"] = current_game
        slot["candidates"] = rank_candidates(conn, slot, limit=candidate_limit)
        state.append(slot)
    return state
