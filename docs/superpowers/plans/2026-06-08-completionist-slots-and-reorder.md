# Completionist slots + drag-to-reorder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-slot "Completionist" mode that surfaces beaten games (status `completed`) to both the list ranker and the AI decider, and let the owner drag slots to reorder them on the grid.

**Architecture:** A new `slots.completionist` boolean column widens a slot's candidate pool to include `completed` games (boosted so they surface in the mixed pool) and flips the AI decider's finished-game suppression for that slot. Reordering reuses the existing `sort_order` column via a new save endpoint and HTML5 drag-and-drop on the slot cards.

**Tech Stack:** Python (Flask, sqlite3), pytest, vanilla JS templates (Jinja/`recommendations.html`), Tailwind classes.

**Conventions (from CLAUDE.md + project memory):**
- TDD throughout. Run tests with `uv run python -m pytest` (plain `uv run pytest` fails).
- Lint with `uv run ruff check` ONLY — never `ruff format`.
- Type hints on signatures; named constants (no magic statuses/scores in conditions); `logging` not `print`.
- Commit directly to `main` (no branches). Co-author trailer per repo convention.

---

## File Structure

- `models.py` — add the `completionist` column migration (idempotent ALTER).
- `slots.py` — eligibility per slot + `COMPLETION_BOOST` + `reorder()` helper.
- `decider.py` — completionist-aware suppression + slot-context line + `decide` wiring.
- `app.py` — PATCH accepts `completionist`; new `POST /api/slots/reorder`.
- `templates/recommendations.html` — completionist checkbox in slot settings; drag-and-drop.
- Tests: `tests/test_slots_completionist.py` (new), `tests/test_slots_lifecycle.py`,
  `tests/test_api_slots.py`, `tests/test_decider*.py` (extend existing).

---

## Task 1: Schema — `slots.completionist` column

**Files:**
- Modify: `models.py` (the slots migration block, near `models.py:639-650`)
- Test: `tests/test_slots_migration.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_slots_migration.py`:

```python
def test_slots_have_completionist_column(temp_db):
    import models
    conn = models.get_db()
    cols = [c[1] for c in conn.execute("PRAGMA table_info(slots)").fetchall()]
    assert "completionist" in cols
    # defaults to 0 for seeded slots
    row = conn.execute("SELECT completionist FROM slots LIMIT 1").fetchone()
    assert row["completionist"] == 0
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_slots_migration.py::test_slots_have_completionist_column -v`
Expected: FAIL — `assert "completionist" in cols` (column missing).

- [ ] **Step 3: Add the migration**

In `models.py`, immediately after the `focus_series_id` migration block (after `models.py:649`) and before `conn.commit()`:

```python
    cols = [c[1] for c in conn.execute("PRAGMA table_info(slots)").fetchall()]
    if "completionist" not in cols:
        conn.execute("ALTER TABLE slots ADD COLUMN completionist INTEGER NOT NULL DEFAULT 0")
```

Also add the column to the `CREATE TABLE IF NOT EXISTS slots` body (after `prioritize_started`, `models.py:632`) for fresh databases:

```python
            completionist        INTEGER NOT NULL DEFAULT 0,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_slots_migration.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add models.py tests/test_slots_migration.py
git commit -m "feat(slots): add completionist column"
```

---

## Task 2: Engine — completionist eligibility + boost

**Files:**
- Modify: `slots.py` (constants near `slots.py:18-25`; `rank_candidates` `slots.py:81-180`)
- Test: `tests/test_slots_completionist.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_slots_completionist.py`:

```python
import models
import slots


def _add_game(conn, title, status):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO user_ratings (game_id, status) VALUES (?, ?)", (gid, status))
    conn.commit()
    return gid


def _titles(candidates):
    return {c["game"]["title"] for c in candidates}


def test_normal_slot_excludes_completed(temp_db):
    conn = models.get_db()
    _add_game(conn, "Beaten Game", "completed")
    _add_game(conn, "Backlog Game", "backlog")
    cands = slots.rank_candidates(conn, {"id": None, "completionist": 0})
    assert "Backlog Game" in _titles(cands)
    assert "Beaten Game" not in _titles(cands)
    conn.close()


def test_completionist_slot_includes_completed(temp_db):
    conn = models.get_db()
    _add_game(conn, "Beaten Game", "completed")
    _add_game(conn, "Backlog Game", "backlog")
    cands = slots.rank_candidates(conn, {"id": None, "completionist": 1})
    assert "Beaten Game" in _titles(cands)
    assert "Backlog Game" in _titles(cands)
    conn.close()


def test_completionist_slot_still_excludes_100_and_dropped(temp_db):
    conn = models.get_db()
    _add_game(conn, "Platinumed", "100")
    _add_game(conn, "Bailed", "dropped")
    cands = slots.rank_candidates(conn, {"id": None, "completionist": 1})
    assert "Platinumed" not in _titles(cands)
    assert "Bailed" not in _titles(cands)
    conn.close()


def test_completionist_boosts_completed_with_reason(temp_db):
    conn = models.get_db()
    _add_game(conn, "Beaten Game", "completed")
    cands = slots.rank_candidates(conn, {"id": None, "completionist": 1})
    beaten = next(c for c in cands if c["game"]["title"] == "Beaten Game")
    assert beaten["score"] >= 50 + slots.COMPLETION_BOOST
    assert "Beaten — chase 100%" in beaten["reasons"]
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_slots_completionist.py -v`
Expected: FAIL — `AttributeError: module 'slots' has no attribute 'COMPLETION_BOOST'` and/or beaten game absent / not boosted.

- [ ] **Step 3: Add constants**

In `slots.py`, near the existing boost/penalty constants (around `slots.py:25`):

```python
COMPLETION_BOOST = 30.0       # completed game surfaced in a completionist slot
# Statuses excluded from a completionist slot's pool (beaten games ARE allowed in;
# only fully-100%'d and dropped games are nothing left to grind).
COMPLETIONIST_EXCLUDED = frozenset({"100", "dropped"})
```

- [ ] **Step 4: Use per-slot exclusion in `rank_candidates`**

In `slots.py`, replace the eligibility query block (currently `slots.py:90-96`):

```python
    placeholders = ",".join("?" * len(FINISHED_STATUSES))
    rows = conn.execute(f"""
        SELECT g.*, ur.status, ur.priority, ur.hours_played, ur.series_id
        FROM games g
        JOIN user_ratings ur ON ur.game_id = g.id
        WHERE ur.status NOT IN ({placeholders})
    """, tuple(FINISHED_STATUSES)).fetchall()
```

with:

```python
    completionist = bool(slot.get("completionist"))
    excluded = COMPLETIONIST_EXCLUDED if completionist else FINISHED_STATUSES
    placeholders = ",".join("?" * len(excluded))
    rows = conn.execute(f"""
        SELECT g.*, ur.status, ur.priority, ur.hours_played, ur.series_id
        FROM games g
        JOIN user_ratings ur ON ur.game_id = g.id
        WHERE ur.status NOT IN ({placeholders})
    """, tuple(excluded)).fetchall()
```

- [ ] **Step 5: Add the boost in the scoring loop**

In `slots.py`, inside the `for game in rows:` loop, right after the priority block
(after `slots.py:125`, the `if priority >= 7:` lines):

```python
        if completionist and game["status"] == "completed":
            score += COMPLETION_BOOST
            reasons.append("Beaten — chase 100%")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_slots_completionist.py -v`
Expected: PASS (all 4).

- [ ] **Step 7: Run the full slot suite (regression)**

Run: `uv run python -m pytest tests/test_slots_engine.py tests/test_slots_lifecycle.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add slots.py tests/test_slots_completionist.py
git commit -m "feat(slots): completionist slots surface beaten games with a boost"
```

---

## Task 3: AI decider — completionist-aware suppression + prompt

**Files:**
- Modify: `decider.py` (`_suppressed_suggestion_ids` `decider.py:77-89`,
  `build_slot_context` `decider.py:101-119`, `decide` `decider.py:205`)
- Test: `tests/test_decider.py` (create if absent) or extend an existing decider test file

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_decider.py` (create the file if it does not exist; import `models`,
`decider`):

```python
import models
import decider


def _add_game(conn, title, status):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO user_ratings (game_id, status) VALUES (?, ?)", (gid, status))
    conn.commit()
    return gid


def test_normal_slot_suppresses_completed(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Beaten Game", "completed")
    suppressed = decider._suppressed_suggestion_ids(conn, [{"role": "user", "content": "what next"}])
    assert gid in suppressed
    conn.close()


def test_completionist_slot_allows_completed(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Beaten Game", "completed")
    suppressed = decider._suppressed_suggestion_ids(
        conn, [{"role": "user", "content": "what next"}], completionist=True)
    assert gid not in suppressed
    conn.close()


def test_completionist_slot_still_suppresses_100_and_dropped(temp_db):
    conn = models.get_db()
    plat = _add_game(conn, "Platinumed", "100")
    bail = _add_game(conn, "Bailed", "dropped")
    suppressed = decider._suppressed_suggestion_ids(
        conn, [{"role": "user", "content": "what next"}], completionist=True)
    assert plat in suppressed
    assert bail in suppressed
    conn.close()


def test_slot_context_mentions_completionist():
    ctx = decider.build_slot_context.__wrapped__ if hasattr(decider.build_slot_context, "__wrapped__") else None
    # build_slot_context needs a conn; assert via a light stub instead:
    import models
    # use a temp slot dict; conn only used for focus_series_id lookup which we skip
    class _C:
        def execute(self, *a, **k):
            class _R:
                def fetchone(self_inner):
                    return None
            return _R()
    text = decider.build_slot_context(_C(), {"label": "Grind", "completionist": 1})
    assert "Completionist" in text
    plain = decider.build_slot_context(_C(), {"label": "Quick", "completionist": 0})
    assert "Completionist" not in plain
```

> Note: drop the unused `__wrapped__` line if it complicates review — the stub-based
> assertions are the real test. Keep the stub `_C` minimal; `build_slot_context` only
> calls `conn.execute(...).fetchone()` for `focus_series_id`, which is absent here.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_decider.py -v`
Expected: FAIL — `_suppressed_suggestion_ids()` got an unexpected keyword `completionist`; and "Completionist" not in context.

- [ ] **Step 3: Add the `completionist` parameter to suppression**

In `decider.py`, replace `_suppressed_suggestion_ids` (`decider.py:77-89`) body so it reads:

```python
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
```

- [ ] **Step 4: Add the completionist line to `build_slot_context`**

In `decider.py`, inside `build_slot_context`, after the `streamable_only` block
(after `decider.py:111`) add:

```python
    if slot.get("completionist"):
        parts.append(
            "Completionist slot — the user has BEATEN these games and wants to 100% "
            "them (achievements, collectibles, postgame). Beaten games (status "
            "'complete') ARE welcome here; still avoid already-100% and dropped games.")
```

- [ ] **Step 5: Pass the flag through `decide`**

In `decider.py`, change the suppression call (`decider.py:205`) to:

```python
    suppressed = _suppressed_suggestion_ids(
        conn, messages, completionist=bool(slot.get("completionist")))
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_decider.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add decider.py tests/test_decider.py
git commit -m "feat(decider): recommend beaten games in completionist slots"
```

---

## Task 4: PATCH endpoint accepts `completionist`

**Files:**
- Modify: `app.py` (`api_update_slot` `app.py:1773-1799`)
- Test: `tests/test_api_slots.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_api_slots.py`:

```python
def test_patch_slot_completionist(client):
    sid = client.get("/api/slots").get_json()["slots"][0]["id"]
    assert client.patch(f"/api/slots/{sid}", json={"completionist": 1}).status_code == 200
    slot = next(s for s in client.get("/api/slots").get_json()["slots"] if s["id"] == sid)
    assert slot["completionist"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_api_slots.py::test_patch_slot_completionist -v`
Expected: FAIL — `completionist` not persisted (the PATCH handler ignores it; assert `== 1` fails on 0).

- [ ] **Step 3: Handle the field in `api_update_slot`**

In `app.py`, after the `prioritize_started` block (`app.py:1789-1791`) add:

```python
    if 'completionist' in data:
        fields.append("completionist = ?")
        params.append(1 if data['completionist'] else 0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_api_slots.py::test_patch_slot_completionist -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_api_slots.py
git commit -m "feat(slots): persist completionist toggle via PATCH"
```

---

## Task 5: Reorder — `slots.reorder` + `POST /api/slots/reorder`

**Files:**
- Modify: `slots.py` (new `reorder` helper, near `pin_game`/`_clear_slot`)
- Modify: `app.py` (new route, near the other `/api/slots` routes)
- Test: `tests/test_slots_lifecycle.py`, `tests/test_api_slots.py`

- [ ] **Step 1: Write the failing helper test**

Add to `tests/test_slots_lifecycle.py`:

```python
def test_reorder_sets_sort_order(temp_db):
    conn = models.get_db()
    ids = [r["id"] for r in conn.execute("SELECT id FROM slots ORDER BY sort_order, id").fetchall()]
    reversed_ids = list(reversed(ids))
    slots.reorder(conn, reversed_ids)
    conn.commit()
    new_order = [r["id"] for r in conn.execute("SELECT id FROM slots ORDER BY sort_order, id").fetchall()]
    assert new_order == reversed_ids
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_slots_lifecycle.py::test_reorder_sets_sort_order -v`
Expected: FAIL — `module 'slots' has no attribute 'reorder'`.

- [ ] **Step 3: Add the `reorder` helper**

In `slots.py`, near `_clear_slot`/`free_slots_for_game`:

```python
def reorder(conn: sqlite3.Connection, slot_ids: list[int]) -> None:
    """Set each slot's sort_order to its position in slot_ids. Caller owns the commit."""
    for index, slot_id in enumerate(slot_ids):
        conn.execute("UPDATE slots SET sort_order = ? WHERE id = ?", (index, slot_id))
```

- [ ] **Step 4: Run helper test to verify it passes**

Run: `uv run python -m pytest tests/test_slots_lifecycle.py::test_reorder_sets_sort_order -v`
Expected: PASS.

- [ ] **Step 5: Write the failing endpoint tests**

Add to `tests/test_api_slots.py`:

```python
def test_reorder_slots_endpoint(client):
    ids = [s["id"] for s in client.get("/api/slots").get_json()["slots"]]
    reversed_ids = list(reversed(ids))
    resp = client.post("/api/slots/reorder", json={"slot_ids": reversed_ids})
    assert resp.status_code == 200
    new_ids = [s["id"] for s in client.get("/api/slots").get_json()["slots"]]
    assert new_ids == reversed_ids


def test_reorder_slots_empty_is_400(client):
    assert client.post("/api/slots/reorder", json={"slot_ids": []}).status_code == 400
```

- [ ] **Step 6: Run endpoint tests to verify they fail**

Run: `uv run python -m pytest tests/test_api_slots.py::test_reorder_slots_endpoint tests/test_api_slots.py::test_reorder_slots_empty_is_400 -v`
Expected: FAIL — 404 (no such route) for the first; the second may also 404.

- [ ] **Step 7: Add the route**

In `app.py`, near the other slot routes (e.g. just after `api_update_slot`, before the
`<int:slot_id>` DELETE — placement is not critical since `reorder` is not an int):

```python
@app.route('/api/slots/reorder', methods=['POST'])
def api_reorder_slots():
    """Persist slot grid order from drag-and-drop. Body: {slot_ids: [...]}."""
    data = request.get_json() or {}
    slot_ids = data.get('slot_ids') or []
    if not slot_ids:
        return jsonify({'error': 'slot_ids required'}), 400
    conn = get_db()
    slots.reorder(conn, slot_ids)
    conn.commit()
    conn.close()
    return jsonify({'success': True})
```

- [ ] **Step 8: Run endpoint tests to verify they pass**

Run: `uv run python -m pytest tests/test_api_slots.py -k reorder -v`
Expected: PASS (both).

- [ ] **Step 9: Commit**

```bash
git add slots.py app.py tests/test_slots_lifecycle.py tests/test_api_slots.py
git commit -m "feat(slots): reorder endpoint + helper for grid drag-and-drop"
```

---

## Task 6: UI — Completionist checkbox in slot settings

**Files:**
- Modify: `templates/recommendations.html` (`toggleSlotSettings` `~:266-269`,
  `_slotSettingsPayload` `~:292-301`)

> No pytest coverage — this is a JS template. Verify in the browser (Task 8).

- [ ] **Step 1: Add the checkbox to the settings panel**

In `toggleSlotSettings`, inside the `flex flex-col gap-1` block, after the
`prioritize_started` label (`recommendations.html:268`), add:

```html
                    <label><input type="checkbox" data-f="completionist" ${slot.completionist ? 'checked' : ''} class="accent-accent"> Completionist (beaten games, chase 100%)</label>
```

- [ ] **Step 2: Include it in the save payload**

In `_slotSettingsPayload`, add to the returned object (after `prioritize_started`,
`recommendations.html:298`):

```javascript
            completionist: val('completionist').checked ? 1 : 0,
```

- [ ] **Step 3: Add a slot-chip hint (optional, low-risk)**

In `slotChip` (`recommendations.html:48-57`), before `return parts.join(...)`:

```javascript
        if (slot.completionist) parts.push('completionist');
```

- [ ] **Step 4: Commit**

```bash
git add templates/recommendations.html
git commit -m "feat(picks): completionist toggle in slot settings"
```

---

## Task 7: UI — Drag-and-drop slot reordering

**Files:**
- Modify: `templates/recommendations.html` (`slotCardHtml` outer div `~:150`; new drag JS)

> No pytest coverage — verify in the browser (Task 8).

- [ ] **Step 1: Make the card draggable with a grip affordance**

In `slotCardHtml`, change the outer return div (`recommendations.html:150`) from:

```javascript
        return `<div class="bg-surface-light rounded-lg p-4 flex flex-col">
```

to:

```javascript
        return `<div draggable="true" data-slot-id="${slot.id}"
            ondragstart="slotDragStart(event, ${slot.id})" ondragover="slotDragOver(event)"
            ondrop="slotDrop(event, ${slot.id})" ondragend="slotDragEnd()"
            class="bg-surface-light rounded-lg p-4 flex flex-col">
```

In the card header row (`recommendations.html:151-154`), add a grip handle before the
`<h3>`:

```html
                <span class="cursor-move text-gray-600 hover:text-gray-300 mr-1 select-none" title="Drag to reorder">⠿</span>
```

- [ ] **Step 2: Add the drag handlers**

In the `<script>` section of `recommendations.html` (near the other slot functions, e.g.
after `dismissSuggestion`), add:

```javascript
        let _dragSlotId = null;
        function slotDragStart(e, slotId) {
            // Don't hijack drags that start inside an input/button so fields stay usable.
            if (e.target.closest('input, textarea, select, button, a')) { e.preventDefault(); return; }
            _dragSlotId = slotId;
            e.dataTransfer.effectAllowed = 'move';
        }
        function slotDragOver(e) {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
        }
        function slotDragEnd() { _dragSlotId = null; }
        async function slotDrop(e, targetId) {
            e.preventDefault();
            if (_dragSlotId === null || _dragSlotId === targetId) { _dragSlotId = null; return; }
            const ids = _slateData.map(s => s.id);
            const from = ids.indexOf(_dragSlotId);
            const to = ids.indexOf(targetId);
            _dragSlotId = null;
            if (from < 0 || to < 0) return;
            ids.splice(to, 0, ids.splice(from, 1)[0]);
            await api.post('/api/slots/reorder', { slot_ids: ids });
            loadSlate();
        }
```

- [ ] **Step 3: Commit**

```bash
git add templates/recommendations.html
git commit -m "feat(picks): drag-and-drop slot reordering on the grid"
```

---

## Task 8: Full verification

- [ ] **Step 1: Run the whole suite**

Run: `uv run python -m pytest -q`
Expected: PASS (all green; previous baseline 683 + new tests).

- [ ] **Step 2: Lint**

Run: `uv run ruff check slots.py decider.py app.py models.py`
Expected: `All checks passed!`

- [ ] **Step 3: Manual browser check (use the `run` skill / app on :5000)**

Verify, against the running app (owner-run; do not churn .py while it runs):
- A slot's ⚙ settings shows the **Completionist** checkbox; toggling it ON + Save makes
  beaten games appear as candidates in that slot (tagged "Beaten — chase 100%"), and the
  "Help me decide" chat can recommend beaten games there.
- A normal slot still shows no beaten games.
- Dragging a slot card by the ⠿ grip reorders the grid and the order survives a reload.
- Inputs/buttons inside a card are still clickable/editable (drag guard works).

- [ ] **Step 4: Final commit (if any docs/cleanup pending)**

```bash
git add -A
git commit -m "docs: completionist slots + reorder plan/spec"
git push
```

---

## Self-Review (author check)

- **Spec coverage:** Part 1 → Tasks 1–2; Part 2 → Task 3; Part 3 → Tasks 4 (PATCH), 5
  (reorder endpoint), 6 (toggle UI), 7 (drag UI). Interaction note (free-slot fix) needs
  no code — covered by existing tests.
- **Type consistency:** `completionist` is an int column (0/1) everywhere; `reorder(conn,
  slot_ids)` and `_suppressed_suggestion_ids(conn, messages, completionist=False)`
  signatures match their call sites; `COMPLETION_BOOST` / `COMPLETIONIST_EXCLUDED`
  defined in Task 2 before use.
- **Placeholder scan:** no TBD/TODO; every code step shows complete code.
