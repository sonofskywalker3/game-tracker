# SP2 — Per-slot Anthropic Decider Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Impl subagents use pytest temp DBs + a MOCKED Anthropic client — they NEVER hit the live API, touch the live `games.db`, run the app, or `git push`. The controller does the live smoke test, the app run, and the push.

**Goal:** A per-slot chat that reasons over the whole library + the slot's context + the user's mood/energy/time and recommends what to pin, surfaced as clickable game cards.

**Architecture:** A new `decider.py` builds a prompt-cached full-library snapshot + generic decider instructions (system), passes the slot context + conversation as the dynamic message suffix, calls the Anthropic Messages API (blocking, Sonnet 4.6, manual `cache_control`), and parses a trailing `<suggestions>` line into validated game ids. Key + model live in `config.json` via the existing Settings UI. UI reuses the existing game-card tiles.

**Tech Stack:** Python 3 / sqlite3 / Flask; `anthropic` SDK (Messages API, prompt caching); `uv` for deps; `uv run python -m pytest`; `ruff check`.

**Spec:** `docs/superpowers/specs/2026-06-03-slate-decider-chat-design.md`

**Key SDK facts (from the claude-api skill):**
- Client: `anthropic.Anthropic(api_key=...)`; call `client.messages.create(...)`.
- Model: **`claude-sonnet-4-6`** (owner's explicit choice; overrides the skill's Opus default). Configurable in `config.json`.
- Prompt caching: `system` is a list of text blocks; put `cache_control={"type": "ephemeral"}` on the **last stable block (the snapshot)**. Sonnet 4.6 min cacheable prefix = 2048 tokens (our ~27K snapshot caches). Verify with `usage.cache_read_input_tokens`.
- Blocking (non-stream): keep `max_tokens` modest (replies are short) — use `1024`.
- Errors: typed exceptions — `anthropic.AuthenticationError`, `anthropic.APIError` (base), etc.
- Stable→dynamic order: `system` (instructions + snapshot, cached) → `messages` (slot context as a leading user turn + the conversation). This keeps the cached system prefix byte-identical across ALL slots and conversations.

---

## Task 1: Dependency + config (key + model)

**Files:**
- Modify: `pyproject.toml` / `uv.lock` (via `uv add`)
- Modify: `config.py:10-15` (DEFAULT_CONFIG) + add `get_anthropic_config`
- Test: `tests/test_config_anthropic.py` (create)

- [ ] **Step 1: Add the dependency.**

Run: `uv add anthropic`
Expected: `anthropic` appears in `pyproject.toml` `[project].dependencies` and `uv.lock` updates.

- [ ] **Step 2: Write the failing test.**

```python
# tests/test_config_anthropic.py
import config


def test_default_config_has_anthropic_keys():
    assert "anthropic_api_key" in config.DEFAULT_CONFIG
    assert config.DEFAULT_CONFIG["decider_model"] == "claude-sonnet-4-6"


def test_get_anthropic_config_defaults(monkeypatch):
    monkeypatch.setattr(config, "load_config",
                        lambda: {"anthropic_api_key": "", "decider_model": "claude-sonnet-4-6"})
    key, model = config.get_anthropic_config()
    assert key is None and model == "claude-sonnet-4-6"


def test_get_anthropic_config_returns_key(monkeypatch):
    monkeypatch.setattr(config, "load_config",
                        lambda: {"anthropic_api_key": " sk-test ", "decider_model": "m"})
    key, model = config.get_anthropic_config()
    assert key == "sk-test" and model == "m"
```

- [ ] **Step 3: Run it — verify it fails.**

Run: `uv run python -m pytest tests/test_config_anthropic.py -q`
Expected: FAIL (`KeyError`/`AttributeError` — keys + function missing).

- [ ] **Step 4: Implement.**

In `config.py`, extend `DEFAULT_CONFIG`:
```python
DEFAULT_CONFIG = {
    "twitch_client_id": "",
    "twitch_client_secret": "",
    "steam_api_key": "",
    "steam_id": "",
    "anthropic_api_key": "",
    "decider_model": "claude-sonnet-4-6",
}
```
Add at the end of `config.py`:
```python
def get_anthropic_config() -> tuple[str | None, str]:
    """Return (api_key_or_None, model) for the decider chat."""
    config = load_config()
    key = config.get("anthropic_api_key", "").strip()
    model = config.get("decider_model", "").strip() or DEFAULT_CONFIG["decider_model"]
    return (key or None), model
```

- [ ] **Step 5: Run — verify pass.**

Run: `uv run python -m pytest tests/test_config_anthropic.py -q`
Expected: `3 passed`.

- [ ] **Step 6: Update `config.example.json`** (keep parity, empty values):

Add `"anthropic_api_key": ""` and `"decider_model": "claude-sonnet-4-6"` to the example file (read it first; preserve existing keys/formatting).

- [ ] **Step 7: Commit.**

```bash
git add pyproject.toml uv.lock config.py config.example.json tests/test_config_anthropic.py
git commit -m "feat(decider): anthropic dep + config (api_key + decider_model)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `decider.build_library_snapshot`

**Files:**
- Create: `decider.py`
- Test: `tests/test_decider_snapshot.py` (create)

- [ ] **Step 1: Write the failing test.**

```python
# tests/test_decider_snapshot.py
import models
import decider


def _add(conn, title, **cols):
    conn.execute("INSERT INTO games (title, normalized_title, session_length) VALUES (?, ?, ?)",
                 (title, models.normalize_title(title), cols.get("session_length")))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO user_ratings (game_id, status, priority) VALUES (?, ?, ?)",
                 (gid, cols.get("status", "backlog"), cols.get("priority", 5)))
    conn.commit()
    return gid


def test_snapshot_includes_every_game_with_id_and_fields(temp_db):
    conn = models.get_db()
    gid = _add(conn, "Hades", session_length="short", status="playing", priority=8)
    snap = decider.build_library_snapshot(conn)
    line = next(ln for ln in snap.splitlines() if "Hades" in ln)
    assert f"#{gid}" in line          # id present for citation
    assert "short" in line            # session_length
    assert "playing" in line          # status
    conn.close()


def test_snapshot_is_deterministic(temp_db):
    conn = models.get_db()
    _add(conn, "Hades"); _add(conn, "Celeste")
    assert decider.build_library_snapshot(conn) == decider.build_library_snapshot(conn)
    conn.close()
```

- [ ] **Step 2: Run — verify it fails.**

Run: `uv run python -m pytest tests/test_decider_snapshot.py -q`
Expected: FAIL (`ModuleNotFoundError: decider`).

- [ ] **Step 3: Implement `decider.py` (snapshot only for now).**

```python
"""Per-slot Anthropic decider chat: library snapshot, prompt assembly, and the
blocking Messages call. The Anthropic client is injectable for testing."""
import logging
import re
import sqlite3

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
```

- [ ] **Step 4: Run — verify pass.**

Run: `uv run python -m pytest tests/test_decider_snapshot.py -q`
Expected: `2 passed`.

- [ ] **Step 5: Commit.**

```bash
git add decider.py tests/test_decider_snapshot.py
git commit -m "feat(decider): build_library_snapshot

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: System prompt (instructions + cached snapshot) + slot context

**Files:**
- Modify: `decider.py`
- Test: `tests/test_decider_prompt.py` (create)

- [ ] **Step 1: Write the failing test.**

```python
# tests/test_decider_prompt.py
import models
import decider

PERSONA_LEAKS = ("garage", "shield", "square enix", "3 kids", "9pm", "9-10pm")


def test_system_blocks_order_and_cache_control():
    blocks = decider.build_system_prompt("SNAPSHOT-TEXT")
    assert blocks[0]["type"] == "text" and "session" in blocks[0]["text"].lower()
    assert blocks[1]["text"] == "SNAPSHOT-TEXT"
    assert blocks[1]["cache_control"] == {"type": "ephemeral"}  # breakpoint on snapshot
    # the cached prefix must be slot-independent → no slot text in system blocks
    assert "SLOT" not in blocks[0]["text"]


def test_instructions_have_no_hardcoded_persona():
    text = decider.build_system_prompt("X")[0]["text"].lower()
    for leak in PERSONA_LEAKS:
        assert leak not in text, f"persona leak: {leak}"


def test_slot_context_uses_db_fields(temp_db):
    conn = models.get_db()
    conn.execute(
        "INSERT INTO slots (label, sort_order, platforms, max_session_minutes, "
        "streamable_only, context_notes) VALUES (?, 0, ?, 60, 1, ?)",
        ("Couch Quick", '["Switch"]', "Living room via Shield. Short sittings."))
    conn.commit()
    slot = dict(conn.execute("SELECT * FROM slots LIMIT 1").fetchone())
    ctx = decider.build_slot_context(conn, slot)
    assert "Couch Quick" in ctx
    assert "Switch" in ctx
    assert "Living room via Shield" in ctx   # the user's own notes drive behavior
    assert "60" in ctx                        # max session minutes
    conn.close()
```

- [ ] **Step 2: Run — verify it fails.**

Run: `uv run python -m pytest tests/test_decider_prompt.py -q`
Expected: FAIL (`AttributeError` — functions missing).

- [ ] **Step 3: Implement (append to `decider.py`).**

```python
import json

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
    "Each library line begins with #<id>. End EVERY reply with a single line listing the ids "
    "you recommend, exactly: <suggestions>12,88</suggestions> (use an empty list "
    "<suggestions></suggestions> if you are only asking a question). Recommend at most 3."
)


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
    if slot.get("focus_series_id"):
        row = conn.execute("SELECT name FROM series WHERE id = ?",
                           (slot["focus_series_id"],)).fetchone()
        if row:
            parts.append(f"Focus series: {row['name']}.")
    if slot.get("context_notes"):
        parts.append(f"Notes: {slot['context_notes']}")
    return "\n".join(parts)
```

- [ ] **Step 4: Run — verify pass.**

Run: `uv run python -m pytest tests/test_decider_prompt.py -q`
Expected: `3 passed`.

- [ ] **Step 5: Commit.**

```bash
git add decider.py tests/test_decider_prompt.py
git commit -m "feat(decider): system prompt (cached snapshot) + slot context

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Parse + validate the `<suggestions>` line

**Files:**
- Modify: `decider.py`
- Test: `tests/test_decider_parse.py` (create)

- [ ] **Step 1: Write the failing test.**

```python
# tests/test_decider_parse.py
import decider


def test_parse_strips_block_and_returns_ids():
    text = "I'd go with Hades.\n<suggestions>12, 88</suggestions>"
    reply, ids = decider.parse_suggestions(text, valid_ids={12, 88, 99})
    assert reply == "I'd go with Hades."
    assert ids == [12, 88]


def test_parse_drops_invalid_ids():
    reply, ids = decider.parse_suggestions("ok <suggestions>5,7</suggestions>", valid_ids={5})
    assert ids == [5]  # 7 dropped (not a real game)


def test_parse_no_block_returns_text_and_empty():
    reply, ids = decider.parse_suggestions("Just a question?", valid_ids={1})
    assert reply == "Just a question?" and ids == []


def test_parse_empty_block():
    reply, ids = decider.parse_suggestions("What's your energy? <suggestions></suggestions>",
                                           valid_ids={1})
    assert ids == [] and reply == "What's your energy?"
```

- [ ] **Step 2: Run — verify it fails.**

Run: `uv run python -m pytest tests/test_decider_parse.py -q`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Implement (append to `decider.py`).**

```python
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
```

- [ ] **Step 4: Run — verify pass.**

Run: `uv run python -m pytest tests/test_decider_parse.py -q`
Expected: `4 passed`.

- [ ] **Step 5: Commit.**

```bash
git add decider.py tests/test_decider_parse.py
git commit -m "feat(decider): parse + validate <suggestions> ids

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `decider.decide` (blocking Messages call, mocked in tests)

**Files:**
- Modify: `decider.py`
- Test: `tests/test_decider_decide.py` (create)

- [ ] **Step 1: Write the failing test.** The Anthropic client is injected (`client=`) so no network/no key is needed in tests.

```python
# tests/test_decider_decide.py
import types
import models
import decider


class FakeMessages:
    def __init__(self, text):
        self.text = text
        self.captured = {}

    def create(self, **kwargs):
        self.captured = kwargs
        block = types.SimpleNamespace(type="text", text=self.text)
        return types.SimpleNamespace(
            content=[block],
            usage=types.SimpleNamespace(cache_read_input_tokens=0, cache_creation_input_tokens=10))


class FakeClient:
    def __init__(self, text):
        self.messages = FakeMessages(text)


def _slot(conn):
    conn.execute("INSERT INTO slots (label, sort_order, platforms, max_session_minutes) "
                 "VALUES ('Quick', 0, '[\"Switch\"]', 60)")
    conn.commit()
    return dict(conn.execute("SELECT * FROM slots LIMIT 1").fetchone())


def _add(conn, title):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO user_ratings (game_id, status) VALUES (?, 'backlog')", (gid,))
    conn.commit()
    return gid


def test_decide_request_shape_and_parse(temp_db):
    conn = models.get_db()
    gid = _add(conn, "Hades")
    slot = _slot(conn)
    fake = FakeClient(f"Try Hades.\n<suggestions>{gid}</suggestions>")
    out = decider.decide(conn, slot, [{"role": "user", "content": "quick fun thing?"}],
                         client=fake, model="claude-sonnet-4-6")
    cap = fake.messages.captured
    assert cap["model"] == "claude-sonnet-4-6"
    assert cap["system"][1]["cache_control"] == {"type": "ephemeral"}   # snapshot cached
    assert cap["messages"][0]["role"] == "user" and "SLOT" in cap["messages"][0]["content"]
    assert cap["messages"][-1]["content"] == "quick fun thing?"          # convo threaded
    assert out["reply"] == "Try Hades." and out["suggestions"] == [gid]
    conn.close()


def test_decide_no_key_returns_error(temp_db, monkeypatch):
    conn = models.get_db()
    slot = _slot(conn)
    monkeypatch.setattr(decider.config, "get_anthropic_config", lambda: (None, "claude-sonnet-4-6"))
    out = decider.decide(conn, slot, [{"role": "user", "content": "hi"}])
    assert out["error"] == "no_api_key"
    conn.close()
```

- [ ] **Step 2: Run — verify it fails.**

Run: `uv run python -m pytest tests/test_decider_decide.py -q`
Expected: FAIL (`AttributeError: module 'decider' has no attribute 'decide'`).

- [ ] **Step 3: Implement (append to `decider.py`).**

```python
import anthropic


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
    except anthropic.APIError as e:           # base for all SDK API errors
        logger.warning("decider API error: %s", e)
        return {"error": "api_error"}

    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
    reply, ids = parse_suggestions(text, valid_ids)
    return {"reply": reply, "suggestions": ids}
```

- [ ] **Step 4: Run — verify pass.**

Run: `uv run python -m pytest tests/test_decider_decide.py -q`
Expected: `2 passed`.

- [ ] **Step 5: Commit.**

```bash
git add decider.py tests/test_decider_decide.py
git commit -m "feat(decider): blocking decide() with prompt caching + injectable client

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: `POST /api/slots/<id>/chat`

**Files:**
- Modify: `app.py` (add route after `api_slot_dismiss`, ~line 1695; confirm `decider` is imported near the other module imports at the top)
- Test: `tests/test_api_slot_chat.py` (create)

- [ ] **Step 1: Write the failing test.** Mock `decider.decide` so the route test never calls the API.

```python
# tests/test_api_slot_chat.py
import json
import models
import app as app_module


def _client(temp_db):
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def _make_slot(conn):
    conn.execute("INSERT INTO slots (label, sort_order, platforms) VALUES ('Quick', 0, '[]')")
    conn.commit()
    return conn.execute("SELECT id FROM slots LIMIT 1").fetchone()[0]


def test_chat_returns_reply_and_resolved_suggestions(temp_db, monkeypatch):
    conn = models.get_db()
    sid = _make_slot(conn)
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (7, 'Hades', 'hades')")
    conn.commit(); conn.close()
    monkeypatch.setattr(app_module.decider, "decide",
                        lambda *a, **k: {"reply": "Try Hades.", "suggestions": [7]})
    client = _client(temp_db)
    resp = client.post(f"/api/slots/{sid}/chat",
                       json={"messages": [{"role": "user", "content": "fun?"}]})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["reply"] == "Try Hades."
    assert data["suggestions"][0]["id"] == 7 and data["suggestions"][0]["title"] == "Hades"


def test_chat_no_key_returns_400(temp_db, monkeypatch):
    conn = models.get_db()
    sid = _make_slot(conn); conn.close()
    monkeypatch.setattr(app_module.decider, "decide", lambda *a, **k: {"error": "no_api_key"})
    resp = _client(temp_db).post(f"/api/slots/{sid}/chat", json={"messages": []})
    assert resp.status_code == 400 and resp.get_json()["error"] == "no_api_key"
```

- [ ] **Step 2: Run — verify it fails.**

Run: `uv run python -m pytest tests/test_api_slot_chat.py -q`
Expected: FAIL (404 — route missing — or import error).

- [ ] **Step 3: Implement.** Confirm the top of `app.py` imports `decider` (add `import decider` alongside `import slots` if absent). Add the route:

```python
@app.route('/api/slots/<int:slot_id>/chat', methods=['POST'])
def api_slot_chat(slot_id: int):
    """One blocking decider turn for a slot. Body: {messages:[...]}. Returns
    {reply, suggestions:[game,...]} or a 400 {error} when no API key is configured."""
    data = request.get_json() or {}
    messages = data.get('messages') or []
    conn = get_db()
    row = conn.execute("SELECT * FROM slots WHERE id = ?", (slot_id,)).fetchone()
    if row is None:
        conn.close()
        return jsonify({'error': 'slot not found'}), 404
    result = decider.decide(conn, dict(row), messages)
    if 'error' in result:
        conn.close()
        return jsonify({'error': result['error']}), 400
    # Resolve validated ids to full game rows for the suggestion cards
    games = []
    for gid in result['suggestions']:
        g = conn.execute("SELECT * FROM games WHERE id = ?", (gid,)).fetchone()
        if g:
            games.append(dict(g))
    conn.close()
    return jsonify({'reply': result['reply'], 'suggestions': games})
```

- [ ] **Step 4: Run — verify pass.**

Run: `uv run python -m pytest tests/test_api_slot_chat.py -q`
Expected: `2 passed`.

- [ ] **Step 5: Commit.**

```bash
git add app.py tests/test_api_slot_chat.py
git commit -m "feat(decider): POST /api/slots/<id>/chat endpoint

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Extend `/api/settings` for the key + model

**Files:**
- Modify: `app.py:1787-1815` (`api_get_settings`, `api_update_settings`)
- Test: `tests/test_api_settings_anthropic.py` (create)

- [ ] **Step 1: Write the failing test.**

```python
# tests/test_api_settings_anthropic.py
import app as app_module
import config


def _client():
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def test_get_settings_masks_key(monkeypatch):
    monkeypatch.setattr(app_module, "load_config",
                        lambda: {"anthropic_api_key": "sk-secret", "decider_model": "claude-sonnet-4-6"})
    data = _client().get("/api/settings").get_json()
    assert data["anthropic_api_key"] == "••••••••"      # masked, never echoed
    assert data["decider_model"] == "claude-sonnet-4-6"
    assert data["has_anthropic_key"] is True


def test_put_settings_saves_key_and_model(monkeypatch):
    saved = {}
    monkeypatch.setattr(app_module, "save_config", lambda u: saved.update(u))
    monkeypatch.setattr(app_module, "load_config", lambda: config.DEFAULT_CONFIG.copy())
    _client().put("/api/settings", json={"anthropic_api_key": "sk-new",
                                         "decider_model": "claude-sonnet-4-6"})
    assert saved["anthropic_api_key"] == "sk-new"
    assert saved["decider_model"] == "claude-sonnet-4-6"


def test_put_settings_ignores_masked_key(monkeypatch):
    saved = {}
    monkeypatch.setattr(app_module, "save_config", lambda u: saved.update(u))
    monkeypatch.setattr(app_module, "load_config", lambda: config.DEFAULT_CONFIG.copy())
    _client().put("/api/settings", json={"anthropic_api_key": "••••••••"})
    assert "anthropic_api_key" not in saved   # don't overwrite with the mask
```

- [ ] **Step 2: Run — verify it fails.**

Run: `uv run python -m pytest tests/test_api_settings_anthropic.py -q`
Expected: FAIL (keys absent from response / not saved).

- [ ] **Step 3: Implement.** In `api_get_settings`, extend `masked`:
```python
    masked = {
        'twitch_client_id': config.get('twitch_client_id', ''),
        'twitch_client_secret': '••••••••' if config.get('twitch_client_secret') else '',
        'has_credentials': bool(config.get('twitch_client_id') and config.get('twitch_client_secret')),
        'anthropic_api_key': '••••••••' if config.get('anthropic_api_key') else '',
        'decider_model': config.get('decider_model', 'claude-sonnet-4-6'),
        'has_anthropic_key': bool(config.get('anthropic_api_key')),
    }
```
In `api_update_settings`, after the twitch block:
```python
    if 'anthropic_api_key' in data and data['anthropic_api_key'] != '••••••••':
        updates['anthropic_api_key'] = data['anthropic_api_key'].strip()

    if 'decider_model' in data:
        updates['decider_model'] = data['decider_model'].strip()
```

- [ ] **Step 4: Run — verify pass.**

Run: `uv run python -m pytest tests/test_api_settings_anthropic.py -q`
Expected: `3 passed`.

- [ ] **Step 5: Commit.**

```bash
git add app.py tests/test_api_settings_anthropic.py
git commit -m "feat(decider): Settings API for anthropic key + model (masked)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Settings UI field (manual — controller verifies live)

**Files:**
- Modify: `templates/settings.html`

> No unit test (Jinja template). Verified live in Task 10.

- [ ] **Step 1: Read `templates/settings.html`** to match the existing twitch/steam field markup + the JS that GETs/PUTs `/api/settings`.

- [ ] **Step 2: Add a masked password input for the Anthropic key + a text input for the model**, mirroring the twitch fields exactly (same wrapper classes, same label style). Wire them into the existing settings-load (populate from `anthropic_api_key`/`decider_model`) and settings-save (include both in the PUT body) JS. Pre-fill the model input with `decider_model`; leave the key blank-or-masked as the existing secret field does.

- [ ] **Step 3: Commit.**

```bash
git add templates/settings.html
git commit -m "feat(decider): Settings UI fields for anthropic key + model

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Chat panel UI on empty slots (manual — controller verifies live)

**Files:**
- Modify: `templates/recommendations.html` (+ reuse `gameCardHtml` from `base.html`)

> No unit test (Jinja/JS). Verified live in Task 10.

- [ ] **Step 1: Read `templates/recommendations.html`** to find where empty-slot cards render their ranked-candidate list + the `gameCardHtml` helper signature in `base.html`.

- [ ] **Step 2: Add a "Help me decide" button on each EMPTY slot card** that toggles a chat panel scoped to that slot. The panel holds: a message list `<div>`, a text `<input>`, and a Send button.

- [ ] **Step 3: Implement the ephemeral chat JS** (frontend holds the `messages` array per slot):
  - On send: push `{role:"user", content}`, render it, show a spinner, `POST /api/slots/<id>/chat` with `{messages}`.
  - On 400 `{error:"no_api_key"}`: render a one-line "Add your Anthropic key in Settings" notice (link to `/settings`).
  - On success: push `{role:"assistant", content: reply}`, render the prose, then render each `suggestions[]` game via `gameCardHtml` with a **Pin** button wired to the existing pin flow (`POST /api/slots/<id>/pin {game_id}`), then `refreshGameList()`/close as the existing suggestion-accept path does.
  - Reset the `messages` array when the panel closes or the slot is filled.

- [ ] **Step 4: Commit.**

```bash
git add templates/recommendations.html
git commit -m "feat(decider): per-slot chat panel with pinnable suggestion cards

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Full gate + live smoke test + push (controller only)

**Files:** none (verification).

- [ ] **Step 1: Full test + lint gate.**

Run: `uv run python -m pytest -q` (expect all green, ≥504 + the new decider/route/config tests) and `ruff check .` (clean — NEVER `ruff format`).

- [ ] **Step 2: Controller live smoke test (real API, real key).** STOP any running app first (PowerShell, filter to `*app.py*` — not the servosity processes). Set a real `anthropic_api_key` in `config.json` (or via the Settings UI), start the app, open the picks tab, open the chat on an empty slot, send "quick fun thing for tonight, low energy", and confirm: a reply renders, suggestion cards appear with Pin buttons, Pin pins the game, and the slot's constraints are respected. Optionally check the server logs / `usage.cache_read_input_tokens` rises on the 2nd turn (cache hit). Then STOP the app.

- [ ] **Step 3: Push** (hold lifted):

```bash
git push origin main
```

- [ ] **Step 4: Update memory** (`slate-picks-tab-feature.md` + `session-traits-next-steps.md`): mark SP2 (decider chat) MVP LANDED + PUSHED; note deferred items (streaming, cross-slate, tool-pin, history).

---

## Self-Review

**Spec coverage:**
- Cached full-library snapshot (Approach A) → Task 2 (snapshot) + Task 3 (cache_control breakpoint). ✓
- Per-title input title+platform+session+series+status (HLTB withheld from the axis; time-to-beat not in snapshot line — see note) → Task 2. ✓
- Generic instructions, no hardcoded persona; slot notes drive behavior → Task 3 (+ `test_instructions_have_no_hardcoded_persona`). ✓
- `<suggestions>` parse + validate against snapshot ids → Task 4. ✓
- Blocking `decide()`, Sonnet 4.6, prompt caching, typed errors, injectable client → Task 5. ✓
- `POST /api/slots/<id>/chat`, 400 on no key, resolve ids→cards → Task 6. ✓
- config.json key+model via Settings (masked GET, PUT ignores mask) → Tasks 1, 7, 8. ✓
- Chat UI with pinnable cards, ephemeral conversation, empty-slot-only → Task 9. ✓
- Subagents mock the API / never touch live DB/app/push; controller smoke-tests + pushes → header + Task 10. ✓
- Dependency `uv add anthropic` → Task 1. ✓

**Note on time-to-beat:** the spec lists time-to-beat as available context. The snapshot omits it from the line to keep the play-time/session distinction clean and tokens low; it remains available to a future tool-use upgrade. If the owner wants it surfaced for "length mood" queries, add `ttb:<effective_time_to_beat_minutes>` to the snapshot line in Task 2 (one field; `slots.effective_time_to_beat_minutes` exists) — flagged, not silently dropped.

**Placeholder scan:** No TBD/TODO; all backend steps show complete code + commands. UI tasks (8, 9) are explicitly manual with concrete step lists + live verification (Jinja/JS isn't unit-tested here, matching the codebase's pattern).

**Type consistency:** `decide()` returns `{"reply", "suggestions":[int]}` or `{"error"}` in Tasks 5/6; the route resolves ids→dicts. `build_system_prompt(snapshot)` and `build_slot_context(conn, slot)` signatures match across Tasks 3/5. `parse_suggestions(text, valid_ids) -> (str, list[int])` consistent across Tasks 4/5.
