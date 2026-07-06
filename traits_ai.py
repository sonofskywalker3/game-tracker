"""SP-C: opt-in AI session-length classification for the catalog's long tail.

Games the shipped traits catalog doesn't cover are left with
session_length = NULL at import. This module classifies them with the USER'S
own Anthropic key (the decider's key + configured model), in numbered batches
with a strict-JSON reply contract, and caches every result into the per-user
game_traits.json — so each title costs one classification ever, and re-imports
hit the local catalog path (models.apply_traits_catalog) with no AI at all.

The classification rule is SP-B's refinement, verbatim: session_length keys on
the length of the natural UNIT OF PLAY, not total game length. The Anthropic
client is injectable for testing (same pattern as decider.py).
"""
import json
import logging
import re
import sqlite3
from collections.abc import Callable

import anthropic

import config
import models

logger = logging.getLogger(__name__)

# Titles per API call. ~30 output tokens per title keeps a batch well inside
# MODEL_MAX_TOKENS; small enough that one lost call wastes little.
TRAITS_AI_BATCH = 60
MODEL_MAX_TOKENS = 4000

VALID_LENGTHS = frozenset({"short", "long"})

INSTRUCTIONS = (
    "You classify video games by SESSION LENGTH — the length of the natural "
    "UNIT OF PLAY, not the total game length and not merely whether stopping "
    "points exist.\n\n"
    "- 'short': the natural unit of play is a brief sitting — one run, one "
    "level, one match, one puzzle, one visual-novel scene. Roguelikes, "
    "level-based platformers, arcade/score games, party games, most puzzle "
    "games.\n"
    "- 'long': the natural unit of play wants a dedicated block — RPGs, "
    "open-world games, tactics/strategy, sims, and episodic narratives whose "
    "episodes are themselves multi-hour (e.g. Telltale-style adventures are "
    "LONG).\n"
    "- 'unknown': only when you genuinely cannot tell what game this is.\n\n"
    "Reply with ONLY a JSON object mapping each game's NUMBER to its class, "
    'e.g. {"1": "short", "2": "long", "3": "unknown"}. No other text.'
)

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def find_unclassified(conn: sqlite3.Connection) -> list[dict]:
    """Games with no session_length yet (the catalog/AI/manual sources all set
    it, so NULL means nothing has classified this title). Ordered by id."""
    rows = conn.execute(
        "SELECT id, title, normalized_title FROM games "
        "WHERE session_length IS NULL ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def build_batch_prompt(titles: list[str]) -> str:
    """One user turn: the numbered title list (the model replies keyed by our
    numbers — we never match on model-echoed titles)."""
    lines = [f"{i}. {t}" for i, t in enumerate(titles, start=1)]
    return "Classify these games:\n" + "\n".join(lines)


def parse_classifications(text: str, n: int) -> dict[int, str]:
    """{index: 'short'|'long'|'unknown'} for indices 1..n from the model reply.

    Values are case-normalized; anything unparseable, missing, out-of-range,
    or not a valid class comes back 'unknown' — an unknown is left NULL in the
    DB and never cached, so a flaky reply is always retryable.
    """
    parsed: dict = {}
    match = _JSON_OBJECT_RE.search(text or "")
    if match:
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            parsed = {}
    out: dict[int, str] = {}
    for i in range(1, n + 1):
        value = str(parsed.get(str(i), "")).strip().lower()
        out[i] = value if value in VALID_LENGTHS else "unknown"
    return out


def _make_client(api_key: str):
    return anthropic.Anthropic(api_key=api_key)


def classify_unclassified(conn: sqlite3.Connection, *, client=None,
                          model: str | None = None,
                          progress: Callable[[int, int, int], None] | None = None) -> dict:
    """Classify every unclassified game in batches; write results + cache.

    Returns {"total", "classified", "unknown"}. Rows are re-checked at write
    time (only still-NULL rows get the AI value, source='ai'); classified
    titles are merged into the per-user game_traits.json so they never cost
    another API call. client/model are injectable for tests; otherwise built
    from config (raises ValueError without a configured key). A failed batch
    propagates — the caller (background runner) surfaces it as a task error;
    everything already written stays.
    """
    todo = find_unclassified(conn)
    report = {"total": len(todo), "classified": 0, "unknown": 0}
    if not todo:
        return report
    if client is None:
        key, cfg_model = config.get_anthropic_config()
        if not key:
            raise ValueError("no Anthropic API key configured")
        client = _make_client(key)
        model = model or cfg_model

    done = 0
    for start in range(0, len(todo), TRAITS_AI_BATCH):
        batch = todo[start:start + TRAITS_AI_BATCH]
        prompt = build_batch_prompt([g["title"] for g in batch])
        resp = client.messages.create(
            model=model, max_tokens=MODEL_MAX_TOKENS,
            system=INSTRUCTIONS,
            messages=[{"role": "user", "content": prompt}])
        text = next((b.text for b in resp.content
                     if getattr(b, "type", None) == "text"), "")
        verdicts = parse_classifications(text, len(batch))

        cache_entries: dict[str, dict] = {}
        for i, game in enumerate(batch, start=1):
            value = verdicts[i]
            if value not in VALID_LENGTHS:
                report["unknown"] += 1
                continue
            cur = conn.execute(
                "UPDATE games SET session_length = ?, session_length_source = 'ai' "
                "WHERE id = ? AND session_length IS NULL", (value, game["id"]))
            if cur.rowcount:
                report["classified"] += 1
            cache_entries[game["normalized_title"]] = {"session_length": value}
        conn.commit()
        models.add_game_traits_entries(cache_entries)
        done += len(batch)
        if progress:
            progress(done, len(todo), report["classified"])
    logger.info("traits AI: %d/%d classified (%d unknown)",
                report["classified"], report["total"], report["unknown"])
    return report
