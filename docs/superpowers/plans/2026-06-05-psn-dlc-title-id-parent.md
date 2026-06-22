# PSN DLC parent-by-title-id + review re-match

## Problem
`dlc_ownership.mark_ownership` resolves an add-on's parent GAME only by the add-on's
NAME (prefix match via `parent_of`). PSN add-on names routinely omit the game name
(e.g. "Legendary Outfit Pack PS4 & PS5", ext id
`JP0177-PPSA24478_00-MAJIMAOUTFITPACK`), so name matching fails → "no parent game".

But every PS Store product id has the shape `REGION-TITLEID_00-CONCEPT16`. The
title-id prefix `external_id.rsplit('-', 1)[0]` (e.g. `JP0177-PPSA24478_00`) is shared
by the base game's id stored in `game_external_ids` (source='playstation', e.g.
`JP0177-PPSA24478_00-DELUXEEDITION000`). Title-ids are unique per game, so a shared
prefix is a deterministic, zero-false-positive parent link.

Verified on live DB: of 88 "no parent game" PSN rows, 67 have a unique title-id-prefix
match in `game_external_ids`, 0 are ambiguous (multi-game prefix), 21 have no base game
in the library. (29 Xbox rows are a separate vendor problem — out of scope.)

## Goal
1. Add PSN parent resolution by title-id prefix to the matcher — source-guarded, used
   as a fallback when name-based `parent_of` does not yield a single game. This improves
   BOTH the live scrape (`mark_ownership`) and the re-match pass (general fix, not a
   one-off DB patch).
2. Add a re-match pass that re-runs the improved matcher over unresolved
   `dlc_review_queue` rows and marks the newly-matched rows owned + resolved, reusing the
   existing engine (`apply_addon_to_parent`), NOT a one-off UPDATE.
3. Wire the re-match so it can be triggered (endpoint + button). Clears the existing 67
   rows with no re-scrape.

## Constraints
- TDD. Tests ONLY via `uv run python -m pytest`. Lint ONLY `uv run ruff check` (never
  `ruff format`).
- Type hints on signatures; named constants; specific exceptions; `logging` not `print`.
- Subagents: pytest temp-DB + static review only; never touch live `games.db` or the
  running app on :5000.

## Task 1 — dlc_ownership: title-id prefix helpers + PSN fallback
File: `dlc_ownership.py`, tests in `tests/test_dlc_ownership_title_id.py` (new).

Add:
- `PLAYSTATION_SOURCE = "playstation"` (module constant).
- `title_id_prefix(external_id: str | None) -> str | None`: returns
  `external_id.rsplit("-", 1)[0]` when `external_id` contains a `-`, else None.
- `parent_by_title_id(prefix_map: dict[str, set[int]], source: str | None, external_id: str | None) -> int | None`:
  source-guarded (returns None unless source == PLAYSTATION_SOURCE); returns the game_id
  only when the title-id prefix maps to exactly one distinct game_id, else None (no match
  OR ambiguous multi-game prefix both → None, leave for review).
- `psn_prefix_map(conn) -> dict[str, set[int]]`: build `{title_id_prefix: {game_id, ...}}`
  from `SELECT game_id, external_id FROM game_external_ids WHERE source='playstation'`.
- In `mark_ownership`: build the prefix map once; for each add-on, after
  `parent = parent_of(title, library)`, if `parent` is not an int (None or AMBIGUOUS),
  try `parent_by_title_id(prefix_map, source, ext)`; if it returns an int, use it as the
  parent. Preserve existing review reasons ("no parent game" for None, "ambiguous parent"
  for AMBIGUOUS) when the fallback also fails. `parent_norm` for the matched parent comes
  from the `library` list.

Tests (temp sqlite, build schema like existing dlc_ownership tests; include a
`game_external_ids` table):
- `title_id_prefix` strips the trailing concept segment; returns None for input without
  `-` and for None.
- `parent_by_title_id`: unique prefix → game_id; non-playstation source → None; unknown
  prefix → None; prefix mapping to two distinct game_ids → None.
- `mark_ownership` end-to-end: a PSN add-on whose NAME lacks the game name but whose
  title-id prefix matches a base game in `game_external_ids` → a new owned DLC row is
  created under that game (report.marked == 1), NOT a "no parent game" review item.
- Regression: a PSN add-on with no matching base game still → "no parent game" review.
- Source guard: a non-playstation add-on (e.g. xbox) with a name miss is unaffected
  (still "no parent game"), even if a same-looking prefix exists.

## Task 2 — dlc_review: rematch_unresolved pass
File: `dlc_review.py`, tests in `tests/test_dlc_review_rematch.py` (new).

Add:
- `@dataclass RematchReport`: `resolved: int = 0` (review rows newly resolved),
  `marked: int = 0` (dlc rows newly owned), `resolved_items: list[Match] = field(...)`.
- `rematch_unresolved(conn) -> RematchReport`: loads `library`, `titles`, and the PSN
  prefix map (reuse `dlc_ownership.psn_prefix_map`); selects open rows
  (`resolved_at IS NULL AND dismissed_at IS NULL`); for each row:
  - resolve a parent: `parent_of(addon_title, library)`; if not an int, fall back to
    `dlc_ownership.parent_by_title_id(prefix_map, source, external_id)`.
  - if no int parent → skip (leave in queue).
  - else apply via the existing engine into a fresh `OwnershipReport`:
    `dlc_ownership.apply_addon_to_parent(conn, sub, parent, parent_norm, titles, addon, dry_run=False)`.
    If `sub.marked or sub.already_owned` (a real flip/create/already-owned), set
    `resolved_at = CURRENT_TIMESTAMP` on that row and accumulate counts +
    `sub.marked_items`. If apply produced only a review item (e.g. "ambiguous dlc"), do
    NOT mark resolved (leave the row; the refined reason/game_id UPSERT is acceptable).
  - Caller owns commit (function does not commit), matching `resolve`/`dismiss`.

Tests (reuse the `conn` fixture pattern from `test_dlc_review_resolve.py`; add a
`game_external_ids` table to that schema):
- Seed a "no parent game" PSN row + a base game with a matching `game_external_ids`
  prefix → `rematch_unresolved` marks it resolved, creates an owned DLC, report.resolved
  == 1, report.marked == 1; the row's `resolved_at` is set.
- A "no parent game" PSN row with NO matching base game → untouched (resolved == 0, row
  still open).
- A non-playstation "no parent game" row whose name still misses → untouched.
- Idempotent: a second `rematch_unresolved` over the same queue resolves nothing new and
  does not double-create DLC.
- A row resolvable by NAME (parent_of now matches) is also resolved (general re-match,
  not PSN-only).

## Task 3 — endpoint + button
Files: `app.py`, `templates/base.html`, tests in `tests/test_app_dlc_review.py` (extend).

- `POST /api/dlc/review/rematch`: opens a db conn, calls
  `dlc_review.rematch_unresolved(conn)`, commits, returns
  `{"ok": True, "resolved": r.resolved, "marked": r.marked, "count": <open count>}`.
  Close the conn; on no rows, returns zeros.
- `templates/base.html`: add an "Auto-match" button in the DLC review modal header
  (near the count header / close button) that calls a new `rematchDlcReview()` JS
  function → `POST /api/dlc/review/rematch` → on success reloads the list
  (`loadDlcReview()`) and refreshes the badge. Disable while busy (reuse `dlcReviewBusy`
  pattern).
- Tests (Flask test client, temp DB): seed an open PSN "no parent game" row + a base game
  with a matching `game_external_ids` prefix; POST `/api/dlc/review/rematch`; assert
  200, body `resolved == 1`, `marked == 1`, `count == 0`, and the DLC row is owned.
  Empty-queue POST returns zeros and 200.

## Verify (read-only on live DB, after all tasks)
Before: 88 PSN + 29 Xbox open. After fix + re-match: ~67 PSN cleared (owned+resolved),
~21 PSN + 29 Xbox remain; owned DLC count rises ~195 → ~262. (The actual re-match run on
the live DB is the owner's call — do not run it against the live app unprompted.)
