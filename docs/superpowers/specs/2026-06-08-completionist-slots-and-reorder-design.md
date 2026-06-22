# Completionist slots + drag-to-reorder — design

## Problem
1. Beaten-but-not-100% games (`status = 'completed'`) are invisible to every
   recommendation path. `slots.rank_candidates` excludes
   `FINISHED_STATUSES = {completed, 100, dropped}` (`slots.py:18,95`) and the AI
   decider strips the same set as a backstop (`decider.py:68,205`). The owner has
   created "grind" slots for post-credits completion (achievements, the pokedex,
   collectibles) but nothing can populate them.
2. Slots cannot be reordered. `slots.sort_order` exists and the grid already renders
   `ORDER BY sort_order` (`models.py:627`, `slots.get_slots_state`), but there is no
   save path or UI to change it.

## Decisions (owner-approved)
- Mode name: **Completionist**.
- Completionist slot pool: **beaten + backlog mixed** — a completionist slot adds
  `completed` games to its *normal* candidate pool (not a beaten-only pool).
- Reorder UX: **drag and drop** on the grid.
- Completed games get a score boost in completionist slots so they surface in the mix.

## Non-goals
- `100` and `dropped` games stay excluded everywhere (nothing left to grind / bailed).
- Normal (non-completionist) slots are unchanged — they still exclude all finished games.
- No new beaten-only slot type; completionist is a per-slot boolean option.

## Part 1 — Completionist slot mode (engine)
File: `models.py`, `slots.py`. Tests: `tests/test_slots_engine.py` (or a new
`tests/test_slots_completionist.py`).

- **Schema:** add `slots.completionist INTEGER NOT NULL DEFAULT 0`. Idempotent
  `PRAGMA table_info` guard + `ALTER TABLE`, matching `streamable_only` /
  `prioritize_started` in `models.py`.
- **Eligibility:** in `rank_candidates`, choose the excluded-status set per slot:
  - normal slot → `{completed, 100, dropped}` (today's `FINISHED_STATUSES`)
  - completionist slot → `{100, dropped}` (beaten games allowed in)
  Introduce a named constant, e.g. `COMPLETIONIST_EXCLUDED = frozenset({"100",
  "dropped"})`, and select it via `bool(slot.get("completionist"))`.
- **Surfacing boost:** new module constant `COMPLETION_BOOST` (in line with the
  existing `FOCUS_SERIES_BOOST` etc., value ~30). When a completionist slot ranks a
  game whose status is `completed`: `score += COMPLETION_BOOST` and
  `reasons.append("Beaten — chase 100%")`.

## Part 2 — AI decider (completionist slots only)
File: `decider.py`, `app.py` (chat route). Tests: `tests/test_decider*.py`.

- **`build_slot_context(conn, slot)`** — when `slot.get("completionist")`, append:
  "Completionist slot — the user has BEATEN these games and wants to 100% them
  (achievements, collectibles, postgame). Beaten games (status 'complete') ARE welcome
  here; still avoid already-100% and dropped games."
- **`_suppressed_suggestion_ids(conn, messages, completionist=False)`** — add the
  parameter. Logic:
  ```
  statuses = set(ABANDONED_STATUSES)        # dropped: always suppressed
  finished = set(FINISHED_STATUSES)         # {completed, 100}
  if completionist:
      finished.discard("completed")         # beaten games welcome
  if not replay_intent:
      statuses |= finished
  ```
  So: completionist (no replay intent) suppresses `{dropped, 100}`, allows `completed`.
  Replay-intent keyword behavior is preserved unchanged.
- **`decide(...)`** passes `completionist=bool(slot.get("completionist"))` into
  `_suppressed_suggestion_ids`. `build_slot_context` already receives the full slot.
- **Chat route** (`/api/slots/<id>/chat`) already loads the full slot row and passes it
  to `decide`, so the flag flows through automatically once `decide` reads it.

## Part 3 — Drag-and-drop reorder + completionist toggle UI
Files: `slots.py`, `app.py`, `templates/recommendations.html`. Tests:
`tests/test_api_slots.py`, `tests/test_slots_lifecycle.py`.

- **`slots.reorder(conn, slot_ids: list[int]) -> None`** — set `sort_order = index`
  for each id in order. Caller owns commit. Mirrors `/api/games/reorder`.
- **`POST /api/slots/reorder`** — body `{"slot_ids": [...]}`; calls `slots.reorder`,
  commits, returns `{"success": True}`. Empty list → 400.
- **PATCH `/api/slots/<id>`** — ensure `completionist` is an accepted field (0/1),
  alongside the existing slot fields.
- **UI (`recommendations.html`):**
  - Make each slot card in the grid `draggable="true"`; on `dragstart`/`drop`,
    reorder the rendered cards and `POST /api/slots/reorder` with the new id order,
    then `loadSlate()`.
  - Add a **Completionist** checkbox to the slot-settings (⚙) panel; on change,
    `PATCH /api/slots/<id>` with `{completionist: 0|1}` and `loadSlate()`.

## Interaction with the just-shipped "free slot on finished status" fix
Marking a game `completed` frees it from any slot's `current_game` (correct — it's done
in that slot). It then appears as a *candidate* in completionist slots, where the owner
can pin it for the grind. Pinned `current_game` vs. ranked candidate are independent;
no conflict.

## Testing (TDD throughout)
- Engine: completionist slot includes + boosts `completed` games; normal slot still
  excludes them (regression); `100`/`dropped` excluded in both.
- Decider: `_suppressed_suggestion_ids` lifts `completed` only when `completionist`,
  still strips `100`/`dropped`; `build_slot_context` adds the completionist line only
  when the flag is set.
- API: PATCH persists `completionist`; `POST /api/slots/reorder` rewrites `sort_order`;
  empty reorder → 400.

## Constraints
- TDD; tests ONLY via `uv run python -m pytest`; lint ONLY `uv run ruff check`.
- Type hints on signatures; named constants (no magic statuses/scores in conditions);
  specific exceptions; `logging` not `print`.
- Subagents (if used): pytest temp-DB + static review only; never touch live `games.db`
  or the running app on :5000.
