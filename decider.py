"""Per-slot Anthropic decider chat: library snapshot, prompt assembly, and the
blocking Messages call. The Anthropic client is injectable for testing."""
import json
import logging
import re
import sqlite3

import anthropic
import config

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


INSTRUCTIONS = (
    "You are a backlog gaming decider. You help the user choose ONE game to pin into a "
    "specific slot, given their whole library, the slot's constraints/notes, and their "
    "mood, energy, and time tonight.\n\n"
    "session_length is SESSION TOLERANCE (clean short stopping points = 'short'; needs a "
    "dedicated block = 'long'), NOT total play-time. Weigh: the slot's platform and session "
    "constraints, what the user just finished (avoid genre fatigue), time-to-beat for length "
    "moods, priority, and the user's stated mood/energy/time. Ask a brief clarifying question "
    "about mood/energy/time when it would change your pick. Recommend from anywhere in the "
    "library, but respect the slot's hard constraints (platform, streamable, session) in your "
    "reasoning.\n\n"
    "Only recommend games the user can start now. NEVER recommend a finished game (status "
    "'completed' or '100') or a 'dropped' game unless the user explicitly asks to replay or "
    "100% something — those are listed only for context (what they've played).\n\n"
    "Write in plain conversational prose. Do NOT use Markdown: no **bold**, no ## headings, "
    "no bullet or numbered lists, no backticks.\n\n"
    "Each library line begins with #<id>. End EVERY reply with a single line listing the ids "
    "you recommend, exactly: <suggestions>12,88</suggestions> (use an empty list "
    "<suggestions></suggestions> if you are only asking a question). Recommend at most 3."
)

# Statuses that exclude a game from being auto-recommended.
FINISHED_STATUSES: frozenset[str] = frozenset({"completed", "100"})
ABANDONED_STATUSES: frozenset[str] = frozenset({"dropped"})
# Phrases that signal the user WANTS a finished game (replay / completion run), which
# lifts the finished-game suppression.
REPLAY_INTENT: tuple[str, ...] = (
    "100%", "100 percent", "replay", "play again", "platinum", "finish it", "complete it",
)


def _suppressed_suggestion_ids(conn: sqlite3.Connection, messages: list[dict],
                               completionist: bool = False) -> set[int]:
    """Game ids that must not be auto-suggested: dropped always; finished
    (completed/100%) unless a user message signals replay/completion intent. A
    completionist slot additionally allows beaten ('completed') games."""
    user_text = " ".join(
        m.get("content", "") for m in messages if m.get("role") == "user").lower()
    statuses = set(ABANDONED_STATUSES)
    finished = set(FINISHED_STATUSES)
    if completionist:
        finished.discard("completed")
    if not any(kw in user_text for kw in REPLAY_INTENT):
        statuses |= finished
    placeholders = ",".join("?" for _ in statuses)
    rows = conn.execute(
        f"SELECT game_id FROM user_ratings WHERE status IN ({placeholders})",
        tuple(statuses)).fetchall()
    return {r["game_id"] for r in rows}


def build_system_prompt(snapshot: str) -> list[dict]:
    """Stable, slot-independent system blocks. Cache breakpoint on the snapshot so the
    prefix is byte-identical across all slots and conversations."""
    return [
        {"type": "text", "text": INSTRUCTIONS},
        {"type": "text", "text": snapshot, "cache_control": {"type": "ephemeral"}},
    ]


def build_slot_context(conn: sqlite3.Connection, slot: dict) -> str:
    """A leading user-turn preamble describing the slot the user is filling. Dynamic
    (per-slot), so it lives in messages, NOT in the cached system prefix."""
    platforms = ", ".join(json.loads(slot["platforms"])) if slot.get("platforms") else "any"
    parts = [f"SLOT: {slot.get('label')}", f"Platforms: {platforms}"]
    if slot.get("max_session_minutes"):
        parts.append(f"Short session (about {slot['max_session_minutes']} min) — exclude long games.")
    if slot.get("min_session_minutes"):
        parts.append(f"Long session (>= {slot['min_session_minutes']} min) — long games welcome.")
    if slot.get("streamable_only"):
        parts.append("Streamed/lag-tolerant games only.")
    if slot.get("completionist"):
        parts.append(
            "Completionist slot — the user has BEATEN these games and wants to 100% "
            "them (achievements, collectibles, postgame). Beaten games (status "
            "'complete') ARE welcome here; still avoid already-100% and dropped games.")
    if slot.get("focus_series_id"):
        row = conn.execute("SELECT name FROM series WHERE id = ?",
                           (slot["focus_series_id"],)).fetchone()
        if row:
            parts.append(f"Focus series: {row['name']}.")
    if slot.get("context_notes"):
        parts.append(f"Notes: {slot['context_notes']}")
    return "\n".join(parts)


_SUGGESTIONS_RE = re.compile(r"<suggestions>(.*?)</suggestions>", re.IGNORECASE | re.DOTALL)


def parse_suggestions(text: str, valid_ids: set[int]) -> tuple[str, list[int]]:
    """Strip the trailing <suggestions> line from the prose and return (reply, ids).
    Ids not present in valid_ids (the snapshot's games) are dropped — never pin a phantom."""
    ids: list[int] = []
    match = None
    for match in _SUGGESTIONS_RE.finditer(text):
        pass  # keep the last match
    if match:
        for tok in match.group(1).split(","):
            tok = tok.strip()
            if tok.isdigit() and int(tok) in valid_ids and int(tok) not in ids:
                ids.append(int(tok))
        text = _SUGGESTIONS_RE.sub("", text)
    return text.strip(), ids


def save_chat(conn: sqlite3.Connection, game_id: int, slot_id: int | None,
              slot_label: str | None, messages: list[dict]) -> int | None:
    """Persist a decider conversation tied to a game (picks-tab history). Keeps only
    real user/assistant dialogue; returns the new row id, or None if nothing to save."""
    clean = [{"role": m.get("role"), "content": m.get("content")}
             for m in (messages or [])
             if m.get("role") in ("user", "assistant") and m.get("content")]
    if not clean:
        return None
    cur = conn.execute(
        "INSERT INTO decider_chats (game_id, slot_id, slot_label, messages) VALUES (?, ?, ?, ?)",
        (game_id, slot_id, slot_label, json.dumps(clean)))
    return cur.lastrowid


def list_chats(conn: sqlite3.Connection, game_id: int) -> list[dict]:
    """Saved decider conversations for a game, newest first."""
    rows = conn.execute(
        "SELECT id, slot_label, messages, created_at FROM decider_chats "
        "WHERE game_id = ? ORDER BY created_at DESC, id DESC", (game_id,)).fetchall()
    out = []
    for r in rows:
        try:
            msgs = json.loads(r["messages"])
        except (json.JSONDecodeError, TypeError):
            msgs = []
        out.append({"id": r["id"], "slot_label": r["slot_label"],
                    "messages": msgs, "created_at": r["created_at"]})
    return out


def _make_client(api_key: str):
    return anthropic.Anthropic(api_key=api_key)


def decide(conn: sqlite3.Connection, slot: dict, messages: list[dict],
           *, client=None, model: str | None = None) -> dict:
    """Run one blocking decider turn. Returns {"reply", "suggestions": [game_id]} or
    {"error": ...}. `client`/`model` are injectable for tests; otherwise built from config."""
    if client is None:
        key, cfg_model = config.get_anthropic_config()
        if not key:
            return {"error": "no_api_key"}
        client = _make_client(key)
        model = model or cfg_model
    model = model or "claude-sonnet-4-6"

    snapshot = build_library_snapshot(conn)
    system = build_system_prompt(snapshot)
    slot_context = build_slot_context(conn, slot)
    payload = [{"role": "user", "content": slot_context}] + list(messages)
    valid_ids = {r["id"] for r in conn.execute("SELECT id FROM games").fetchall()}

    try:
        resp = client.messages.create(
            model=model, max_tokens=MODEL_MAX_TOKENS, system=system, messages=payload)
    except anthropic.AuthenticationError:
        return {"error": "auth_error"}
    except anthropic.APIError as e:
        logger.warning("decider API error: %s", e)
        return {"error": "api_error"}

    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
    reply, ids = parse_suggestions(text, valid_ids)
    suppressed = _suppressed_suggestion_ids(
        conn, messages, completionist=bool(slot.get("completionist")))
    ids = [i for i in ids if i not in suppressed]   # backstop: never pin finished/dropped
    return {"reply": reply, "suggestions": ids}
