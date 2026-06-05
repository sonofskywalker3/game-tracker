# Fix-match modal redesign — design

**Date:** 2026-06-04
**Status:** Approved (owner)
**Follows:** `2026-06-04-bundle-authoritative-apply-design.md`

## Problem

The "Wrong version? Fix match" candidate grid (game-detail modal) is unusable for
the 79 remaining review items:

- It lists **non-game junk** — IGDB ROM hacks / fan mods (e.g. "Aria of Sorrow
  *Alter*", "...Persephone", "...Magician Mode") alongside the real entries.
- It shows **near-duplicate tiles** — the same cover art twice (bundle vs search
  source), distinguished only by a meaningless "source · N platforms" caption.
- Covers are **cropped to a thin strip** (`h-24 object-cover`), so the user can't
  judge the art they're picking.
- There is **no "keep what I have" action** — every choice changes the match, so a
  game whose current cover is already correct can't be cleared from review without
  picking something.

## Goals

1. Show only useful candidates: titles that actually match the game (or come from
   the bundle), each with a real cover, **de-duplicated by cover art** so identical
   art never appears twice. Never return empty when covers exist (graceful fallback).
2. Show **full box art** at natural proportions.
3. Lead with the game's **current cover** and a **"Keep this one"** action that
   clears the review flag without changing the match — the "it's fine" button.
4. Badge the top suggestion **"Recommended."**

Non-goals: changing the matching/scoring logic; touching the audit; redesigning the
rest of the game-detail modal.

## Design

### Unit 1 — `igdb_match.modal_candidates(cands, game_title)` (pure, unit-tested)

A pure helper that shapes raw `candidates_for` output for display:

- Drop candidates with no `cover_url` (can't judge art, not useful for a visual pick).
- Keep a candidate when `source == "bundle"` **or** `normalize_title(name) ==
  normalize_title(game_title)`.
- If that title filter yields nothing, **fall back** to all cover-bearing candidates
  (so a game whose only matches have slightly different titles still gets options).
- De-duplicate by `_cover_stem(cover_url)`, keeping the first occurrence. The input is
  already ordered bundle-first then by descending score, so "first" is the best of
  each identical-art group.
- Returns the filtered, de-duplicated list (order preserved).

### Unit 2 — `GET /api/games/<id>/igdb-candidates` returns shaped list + current

`api_igdb_candidates` passes `candidates_for(...)` through `modal_candidates(...,
game_title)` and returns `{"candidates": [...], "current": {"cover_url": ...,
"title": ...}}`. The game's title/cover come from the existing `games` row lookup.

### Unit 3 — `POST /api/games/<id>/igdb-keep` (new)

Clears review while keeping the current match: `UPDATE games SET igdb_locked = 1,
needs_igdb_review = 0, igdb_review_reason = NULL, updated_at = CURRENT_TIMESTAMP WHERE
id = ?`. Does not change `igdb_id` or `cover_url`. Returns `{"success": true}`; 404 if
the game is missing. Mirrors the flag-clearing already done by `api_igdb_pick` /
`api_pin_igdb`, minus any identity change.

### Unit 4 — modal grid (template `base.html`, `loadIgdbCandidates` + JS)

`loadIgdbCandidates(gameId)` renders, inside `#igdb-candidates-<id>`:

- A **Current** tile: the `current.cover_url` art (full, `aspect-[3/4]
  object-contain`), label "Current", and a **"Keep this one"** button →
  `keepCurrentIgdb(gameId)`.
- One tile per candidate: full cover art, the first tile badged **"Recommended"**, an
  **"Use this"** button → existing `pickIgdb(gameId, igdb_id, cover_url)`.
- The container grid widens to `grid-cols-3`. The cryptic "source · N platforms"
  caption is removed.

New JS `keepCurrentIgdb(gameId)`: `POST /api/games/<id>/igdb-keep`, then `closeModal()`
+ `refreshGameList()` (same post-action as `pickIgdb`). `escapeHtml` guards all
interpolated strings.

## Data flow

```
Fix match clicked ─► GET igdb-candidates ─► candidates_for ─► modal_candidates
                                                              (filter+dedupe+fallback)
                     ◄─ {candidates, current} ─────────────────────────────────────
modal renders: [Current | Keep] [Recommended | Use] [option | Use] ...
  Keep  ─► POST igdb-keep  ─► lock + clear review (no identity change)
  Use   ─► POST igdb-pick  ─► apply id + cover + lock + clear review (existing)
```

## Error handling

- `igdb-candidates`: existing 404 (no game) / 400 (no credentials) paths unchanged;
  `modal_candidates` of an empty list returns `[]` and the modal shows the existing
  "No candidates found" message.
- `igdb-keep`: 404 if the game id does not exist.

## Testing

- `modal_candidates` (unit): drops no-cover entries; keeps exact-title + bundle, drops
  mismatched-title junk; de-dupes identical cover stems; falls back to all-with-cover
  when no title match; empty input → empty output.
- `igdb-candidates` (route, `client`): response includes `current` and a shaped
  `candidates` list (monkeypatch `candidates_for`).
- `igdb-keep` (route, `client`): clears `needs_igdb_review`/reason and sets
  `igdb_locked` without altering `igdb_id`/`cover_url`; 404 on missing game.
- Frontend grid: no unit harness (vanilla JS in a template) — verified by running the
  app and exercising one game, per the project's run-the-app verification norm.
- `uv run python -m pytest`; lint `ruff check`.
