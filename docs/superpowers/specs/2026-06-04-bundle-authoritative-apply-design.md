# Bundle-authoritative apply — design

**Date:** 2026-06-04
**Status:** Approved (owner)
**Follows:** `2026-06-03-igdb-audit-score-delta-design.md`, bundle-aware IGDB matching (phases 1-5)

## Problem

The IGDB matching pipeline computes the correct identity for a game and then
**discards it**, keeping only a cover URL. As a result:

- `fetch_covers.search_game()` calls `igdb_match.resolve_identity()` — which
  returns the full identity (`igdb_id`, `cover_url`, `source`) — but returns only
  `identity["cover_url"]`. The caller (`fetch_covers_generator`) then writes
  `UPDATE games SET cover_url = ?` and **never persists `igdb_id` and never locks**.
- `igdb_match.audit_igdb_matches()` re-discovers the authoritative bundle match on
  every run but only sets `needs_igdb_review = 1` (flag-only), never applying it.

Concrete failure: **Castlevania: Aria of Sorrow** (game id 860, collection
"Castlevania Advance Collection") has `igdb_id = None` and a cover (`co687k.jpg`)
that actually belongs to a fan ROM-hack ("Aria of Sorrow *Alter*"). The bundle
resolver already knows the correct entry (`igdb_id 222412`, cover `cob949.jpg`),
but the pipeline threw the id away and the audit only flags it. The wrong data
never heals, and the owner is sent to a confusing modal to hand-apply an answer
the code already computed.

A **bundle-source** candidate is authoritative by construction: it is a constituent
of the IGDB bundle that matches the game's `collection_name`, accepted only on an
**exact `normalize_title` match** (`candidates_for`, the `source == "bundle"` path).
This is identity-grade, not fuzzy — unlike the containment matching that caused past
mis-renames. It should be **applied**, not reviewed.

## Goals

1. The pipeline persists the **full identity** (`igdb_id` + `cover_url`), and
   **locks** it (`igdb_locked = 1`), whenever the resolved match is authoritative
   (bundle source, or a strict exact-title match) — instead of saving a cover alone.
2. The audit **self-heals**: its bundle branch applies + locks the authoritative
   match (clearing `needs_igdb_review` / `igdb_review_reason`) instead of flagging.
   Running it corrects the existing backlog (the 22 "bundle" flags) automatically.
3. Genuinely ambiguous matches — `stronger match`, `better platform match` — keep
   the existing flag-for-review behavior (owner reviews these by hand).

Non-goals: changing the review UI/modal (tracked separately); auto-applying the
non-bundle reasons; wiring the audit into an automatic trigger (run remains manual
for now, invoked once after validation).

## Design

### Change 1 — persist authoritative identity in the cover pipeline

In `fetch_covers.py`, the cover-write step resolves identity and persists by
authority:

- Resolve via `igdb_match.resolve_identity(title, platform_ids, collection_name, …)`.
- An identity is **authoritative** when `source == "bundle"` **or**
  `normalize_title(identity["name"]) == normalize_title(title)` (the existing
  `strict` gate in `search_game`).
- **Authoritative + has cover** → `UPDATE games SET igdb_id = ?, cover_url = ?,
  igdb_locked = 1, needs_igdb_review = 0, igdb_review_reason = NULL,
  updated_at = CURRENT_TIMESTAMP WHERE id = ?`.
- **Non-authoritative, non-strict (blank-cover fill)** → existing behavior:
  write `cover_url` only (no id, no lock).
- **Strict + non-authoritative** → skip (unchanged).
- **Miss** → existing null-on-miss behavior (unchanged).

`search_game` is refactored to expose the resolved identity (id + cover + source)
to the caller rather than collapsing to a cover string; its only non-test caller is
`fetch_covers_generator`. Locked games are still skipped at the top of the loop
(`igdb_locked` guard, unchanged), so this never overwrites a human-pinned match.

### Change 2 — audit applies bundle matches (self-heal)

In `igdb_match.audit_igdb_matches`, split the bundle branch from the flag path:

- `best["source"] == "bundle"` → **apply + lock**: `UPDATE games SET igdb_id = ?,
  cover_url = ?, igdb_locked = 1, needs_igdb_review = 0, igdb_review_reason = NULL,
  updated_at = CURRENT_TIMESTAMP WHERE id = ?`, record under `applied`. Do not flag.
- All other branches (`stronger match`, `better platform match`, `mobile->console`,
  `unmatched->match`) → unchanged flag behavior.

Return value changes from `list[int]` (flagged) to a result carrying both
`applied` and `flagged` id lists, so a runner can report "N applied, M flagged".
Only tests consume the return value today; they are updated.

## Data flow

```
import / cover fetch ─► resolve_identity ─► authoritative? ─► write id+cover+lock
                                          └► else ─────────► cover-only (as today)

manual audit run ─► per non-locked game ─► best candidate
                      ├─ source=bundle ─► APPLY id+cover+lock (heal)
                      └─ else ─────────► flag for review (as today)
```

## Safety / rollback

This is the same matching code that previously mis-renamed ~178 games, so:

- Built **test-first** (pytest, temp DB only — never the live `games.db`).
- Before any live run, the audit is dry-run against a **copy** of `games.db`; the
  owner confirms the bundle applies (Aria of Sorrow → `cob949`, etc.) look right.
- A live games.db backup (`games.db.bak-<ts>`) is taken before applying.
- Fully reversible: every applied match remains re-pickable in the modal, which
  re-locks to the human choice.

## Testing

- `fetch_covers`: authoritative bundle identity → id + cover + lock written;
  authoritative exact-title → same; non-authoritative non-strict → cover-only,
  no id/lock; locked game → skipped; miss → null-on-miss preserved.
- `audit_igdb_matches`: bundle candidate → applied + locked, not flagged, returns
  it under `applied`; search-source stronger/better-platform → still flagged;
  locked game → untouched; cosmetic cover-stem equality → still skipped.
- Run via `uv run python -m pytest`; lint gate `ruff check`.
