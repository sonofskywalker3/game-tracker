"""Per-slot Anthropic decider chat: library snapshot, prompt assembly, and the
blocking Messages call. The Anthropic client is injectable for testing."""
import json
import logging
import re
import sqlite3

import anthropic
import config
from identity import OWNER_USER_ID

logger = logging.getLogger(__name__)

MODEL_MAX_TOKENS = 1024


def build_library_snapshot(conn: sqlite3.Connection,
                           user_id: int = OWNER_USER_ID) -> str:
    """One compact line per game, deterministic (ordered by id). Status-tagged so the
    model knows what is finished. Each line starts with #<id> for citation.
    Platforms are fetched in one query (not per game) — the snapshot rebuilds on
    every chat turn, and its bytes must stay identical for prompt caching. Scoped
    to ``user_id`` so the prompt only ever contains the acting user's library."""
    rows = conn.execute("""
        SELECT g.id, g.title, g.session_length,
               ur.status, ur.priority, ur.hours_played
        FROM games g
        LEFT JOIN user_ratings ur ON ur.game_id = g.id
        WHERE g.user_id = ?
        ORDER BY g.id
    """, (user_id,)).fetchall()
    plats: dict[int, list[str]] = {}
    for p in conn.execute(
            "SELECT gp.game_id, p.short_name FROM game_platforms gp "
            "JOIN platforms p ON p.id = gp.platform_id "
            "JOIN games g ON g.id = gp.game_id "
            "WHERE g.user_id = ? "
            "ORDER BY gp.game_id, p.short_name", (user_id,)).fetchall():
        if p["short_name"]:
            plats.setdefault(p["game_id"], []).append(p["short_name"])
    lines = []
    for r in rows:
        platforms = "/".join(plats.get(r["id"], []))
        lines.append(
            f"#{r['id']} | {r['title']} | plat:{platforms or '-'} | "
            f"session:{r['session_length'] or '?'} | "
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
    "100% something, or the slot context explicitly allows beaten games — those are listed "
    "only for context (what they've played).\n\n"
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
                               completionist: bool = False,
                               user_id: int = OWNER_USER_ID) -> set[int]:
    """Game ids that must not be auto-suggested: finished (completed/100%) and
    dropped games, unless a user message signals replay/completion intent (which
    lifts both — the INSTRUCTIONS promise dropped games are replayable on request).
    A completionist slot additionally allows beaten ('completed') games."""
    user_text = " ".join(
        m.get("content", "") for m in messages if m.get("role") == "user").lower()
    finished = set(FINISHED_STATUSES)
    if completionist:
        finished.discard("completed")
    statuses: set[str] = set()
    if not any(kw in user_text for kw in REPLAY_INTENT):
        statuses = set(ABANDONED_STATUSES) | finished
    if not statuses:
        return set()
    placeholders = ",".join("?" for _ in statuses)
    rows = conn.execute(
        f"SELECT ur.game_id FROM user_ratings ur "
        f"JOIN games g ON g.id = ur.game_id "
        f"WHERE ur.status IN ({placeholders}) AND g.user_id = ?",
        (*statuses, user_id)).fetchall()
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
            "them (achievements, collectibles, postgame). For THIS slot, games with "
            "status 'completed' ARE recommendable (that is the point of the slot); "
            "still avoid already-100% ('100') and 'dropped' games.")
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
              slot_label: str | None, messages: list[dict],
              user_id: int = OWNER_USER_ID) -> int | None:
    """Persist a decider conversation tied to a game (picks-tab history). Keeps only
    real user/assistant dialogue; returns the new row id, or None if nothing to save.
    decider_chats is a per-user root: the row is stamped with ``user_id``."""
    clean = [{"role": m.get("role"), "content": m.get("content")}
             for m in (messages or [])
             if m.get("role") in ("user", "assistant") and m.get("content")]
    if not clean:
        return None
    cur = conn.execute(
        "INSERT INTO decider_chats (game_id, slot_id, slot_label, messages, user_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (game_id, slot_id, slot_label, json.dumps(clean), user_id))
    return cur.lastrowid


def list_chats(conn: sqlite3.Connection, game_id: int,
               user_id: int = OWNER_USER_ID) -> list[dict]:
    """Saved decider conversations for a game (this user's), newest first."""
    rows = conn.execute(
        "SELECT id, slot_label, messages, created_at FROM decider_chats "
        "WHERE game_id = ? AND user_id = ? ORDER BY created_at DESC, id DESC",
        (game_id, user_id)).fetchall()
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
           *, client=None, model: str | None = None,
           user_id: int = OWNER_USER_ID) -> dict:
    """Run one blocking decider turn. Returns {"reply", "suggestions": [game_id]} or
    {"error": ...}. `client`/`model` are injectable for tests; otherwise built from
    config. Scoped to ``user_id`` so the library, valid ids, and finished-game
    suppression only ever consider the acting user's games."""
    if client is None:
        key, cfg_model = config.get_anthropic_config()
        if not key:
            return {"error": "no_api_key"}
        client = _make_client(key)
        model = model or cfg_model
    model = model or "claude-sonnet-4-6"

    snapshot = build_library_snapshot(conn, user_id)
    system = build_system_prompt(snapshot)
    slot_context = build_slot_context(conn, slot)
    payload = [{"role": "user", "content": slot_context}] + list(messages)
    valid_ids = {r["id"] for r in conn.execute(
        "SELECT id FROM games WHERE user_id = ?", (user_id,)).fetchall()}

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
        conn, messages, completionist=bool(slot.get("completionist")), user_id=user_id)
    ids = [i for i in ids if i not in suppressed]   # backstop: never pin finished/dropped
    return {"reply": reply, "suggestions": ids}
