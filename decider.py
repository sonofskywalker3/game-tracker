"""Per-slot Anthropic decider chat: library snapshot, prompt assembly, and the
blocking Messages call. The Anthropic client is injectable for testing."""
import logging
import re  # noqa: F401
import sqlite3

import config  # noqa: F401

logger = logging.getLogger(__name__)

MODEL_MAX_TOKENS = 1024


def _platforms(conn: sqlite3.Connection, game_id: int) -> str:
    rows = conn.execute(
        "SELECT p.short_name FROM game_platforms gp JOIN platforms p ON p.id = gp.platform_id "
        "WHERE gp.game_id = ? ORDER BY p.short_name", (game_id,)).fetchall()
    return "/".join(r["short_name"] for r in rows if r["short_name"])


def build_library_snapshot(conn: sqlite3.Connection) -> str:
    """One compact line per game, deterministic (ordered by id). Status-tagged so the
    model knows what is finished. Each line starts with #<id> for citation."""
    rows = conn.execute("""
        SELECT g.id, g.title, g.session_length, g.series_role,
               ur.status, ur.priority, ur.hours_played, ur.series_id,
               s.name AS series_name
        FROM games g
        LEFT JOIN user_ratings ur ON ur.game_id = g.id
        LEFT JOIN series s ON s.id = ur.series_id
        ORDER BY g.id
    """).fetchall()
    lines = []
    for r in rows:
        series = r["series_name"] or "-"
        role = r["series_role"] or "-"
        lines.append(
            f"#{r['id']} | {r['title']} | plat:{_platforms(conn, r['id']) or '-'} | "
            f"session:{r['session_length'] or '?'} | series:{series}({role}) | "
            f"status:{r['status'] or '-'} | hrs:{r['hours_played'] or 0} | pri:{r['priority'] or 5}")
    return "\n".join(lines)
