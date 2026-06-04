# Conservative score-delta IGDB re-audit — Design

**Date:** 2026-06-03
**Status:** Approved (owner)
**Supersedes:** the flag-on-cover-URL-difference behavior of `audit_igdb_matches`
introduced in `docs/superpowers/specs/2026-06-03-bundle-aware-igdb-matching-design.md`
(Phase 5). This is a follow-on refinement, not a new feature.

## Problem

The first live run of `audit_igdb_matches` over the real 766-game library flagged
**178 games (23%)** — overwhelmingly false positives, unusable as a review list.
Two distinct noise sources, both rooted in the same mistake: the audit flags on any
**cover-URL string difference** (`resolved_cover_url != stored_cover_url`).

1. **Cosmetic differences (39 of 178).** Stored covers ending in `.webp` always
   mismatch the resolver's `.jpg`, even when it is the *same image of the same IGDB
   entry* (e.g. Hades `cob9kr.webp` vs `cob9kr.jpg`, same igdb_id 113112; NieR
   `co5pcj`; Mega Man 11 `co1zyu`).
2. **Re-search drift (137 of 178 already had an igdb_id).** For an already-matched
   game the audit re-searches by title and often lands on a *different but not
   better* entry — frequently the old original instead of the owned edition
   (Cyberpunk 2077 stored `277807` → resolver `1877`; Portal on Switch stored
   `193776` → resolver `71`, the 2007 PC original; Breath of the Wild `237895` →
   `7346`). Applying these would **downgrade** correct covers.

Only ~34 flags were genuine bundle constituents (the feature's original target).

## Goal

A re-audit that surfaces matches the owner would *want* to fix — wrong-version
matches **and** genuinely better candidates ("possibly-better", owner's choice) —
while producing a small, trustworthy list. Flag-only: never mutates `cover_url` or
`igdb_id`.

## Approach: compare match *quality*, not URLs

Score the **currently-stored** IGDB entry with the same platform-aware,
mobile-penalized scorer (`score_candidates`) already used on candidates, and flag a
game only when a **different** candidate beats the stored entry.

This unifies every case under one comparison:
- Cosmetic `.webp`/`.jpg`: same entry → same cover-stem → excluded before scoring.
- Re-search drift to a worse entry (Portal-Switch): stored Switch entry has platform
  overlap (+50), the PC original does not → stored scores higher → not flagged.
- Mobile mismatch: stored mobile-only takes the −80 penalty, console candidate gets
  +50 → large positive delta → flagged.
- Better platform match: candidate scores higher → flagged.
- Bundle disagreement: the bundle reverse-lookup is authoritative for a constituent,
  so a bundle-source candidate with a different cover-stem is flagged directly (the
  score-delta gate is bypassed — constituents often share a non-overlapping platform
  with a wrong stored entry, which would otherwise tie on score).

## Components

### `igdb_match.fetch_entry(igdb_id, client_id, token) -> dict | None`
Fetch one IGDB entry by id with the fields the scorer needs:
`name, cover.url, platforms, first_release_date, total_rating_count, game_type`
(query `fields …; where id = (<id>);`). Returns the raw IGDB dict, or `None` if the
id no longer resolves. Goes through `igdb_dlc._igdb_query` (monkeypatched in tests).

### `igdb_match._cover_stem(url) -> str | None`
Extract the IGDB image id from a cover URL, ignoring size token and extension, so
`.../t_thumb/co1zyu.jpg`, `.../t_cover_big/co1zyu.webp` all collapse to `co1zyu`.
Returns `None` for falsy/parse-failure input. Used to decide "same image" without
URL-format false positives.

### `igdb_match.audit_igdb_matches(conn, *, client_id, token)` (rewritten)
Per non-locked game (`COALESCE(igdb_locked,0)=0`), ordered by title:

1. Load owned platform short_names → `platform_ids_for(...)`; read `title`,
   `igdb_id` (stored), `cover_url` (stored), `collection_name`.
2. Build the candidate suggestion list via existing `candidates_for(...)`
   (bundle-first, then scored search). Take the best candidate.
3. **Score uniformly.** Score the best candidate and the stored entry against the
   same `game_platform_ids` and `title` using `score_candidates`:
   - Stored entry: `fetch_entry(stored_igdb_id)` then score. If it cannot be fetched
     or its name fails the title match (`score_candidates` drops it), the stored
     score is treated as absent.
   - Best candidate: score its minimal dict (`name`, `platforms`,
     `cover` from its `cover_url`).
4. **Flag decision** (evaluated in order):
   - Skip if there is no best candidate or it has no cover.
   - Skip if `_cover_stem(best) == _cover_stem(stored)` (same image — cosmetic only).
   - **Best is bundle-source** → flag. The reverse-`bundles` lookup is authoritative
     for a constituent, so a different cover-stem means the stored match is the wrong
     version regardless of score. (Constituents often share a non-overlapping
     platform — e.g. NES — with a wrong stored entry, so a score-delta alone would
     miss real bundle disagreements. The owner's already-fixed constituents are
     either `igdb_locked` (skipped) or already agree with the bundle truth — same
     stem — so they do not re-flag.)
   - **Stored igdb_id present and scorable** (search-source best): flag iff
     `best_score - stored_score >= _REVIEW_MARGIN`.
   - **Stored entry absent / unscorable** (no stored igdb_id, ~41 games; or
     fetch/title-match failure): flag iff the best candidate is a **strong** match —
     score `>= _STRONG_MATCH` (exact title + platform overlap) **and** not
     mobile-only — to avoid reopening title-search drift.
5. On flag: `UPDATE games SET needs_igdb_review = 1, igdb_review_reason = ?`.
   Reason is derived from why the candidate won (see below). Never writes
   `cover_url`/`igdb_id`. Returns the list of flagged ids.

`_REVIEW_MARGIN` is a named module constant. Default = `1` (strictly-better — the
owner chose broadest recall without cosmetic noise; cosmetic ties are already
excluded by the stem check). `_STRONG_MATCH = _TITLE_EXACT + _PLATFORM_OVERLAP`
(= 150) is the bar a candidate must clear to flag a game with no scorable stored
entry.

### Reason labels
Derived per flag, surfaced in the UI for faster review:
- `bundle` — best candidate's `source == "bundle"`.
- `mobile→console` — stored entry is mobile-only and the candidate is not.
- `better platform match` — candidate has platform overlap, stored entry does not.
- `unmatched→match` — no scorable stored entry; strong candidate found.
- `stronger match` — fallback (higher score for another reason, e.g. exact vs
  contains title or higher rating).

## Schema

New migration `migrate_igdb_review_reason` adding `games.igdb_review_reason TEXT`
(nullable), registered in `migrate_db` after `migrate_igdb_review` and mirrored in
`tests/conftest.py`. Cleared (`= NULL`) wherever `needs_igdb_review` is cleared —
the `igdb-pick` and `igdb-pin` paths in `app.py`.

## API / UI

- `/api/games` row serialization adds `igdb_review_reason` alongside the existing
  `needs_igdb_review`.
- `templates/index.html` Needs-review grid renders a small reason chip on each
  flagged card. The modal "Fix match" grid is unchanged — its top candidate is
  already the suggestion.

## Cost

Adds one `fetch_entry` per game that has a stored `igdb_id` (on top of the existing
search). Still a few minutes over the library, rate-limited. Possible later
optimization: batch stored-entry fetches via `where id = (a,b,c,…)`. Out of scope
for this pass.

## Testing (subagent TDD, monkeypatched IGDB, temp-DB fixtures)

- `_cover_stem` collapses size/extension variants to the same stem; `None` on falsy.
- Cosmetic `.webp` vs `.jpg`, same entry → **not flagged**.
- Stored mobile-only vs console candidate → **flagged**, reason `mobile→console`.
- Portal-style: stored entry with platform overlap vs a no-overlap higher-search-rank
  original → stored scores higher → **not flagged**.
- Bundle constituent whose bundle-first candidate differs from stored → **flagged**,
  reason `bundle`.
- No stored igdb_id + strong candidate (exact title + overlap + non-mobile) →
  **flagged**, reason `unmatched→match`; weak candidate → **not flagged**.
- Locked games skipped; cover_url/igdb_id never mutated.
- `migrate_igdb_review_reason` adds the column (idempotent).
- `igdb-pick`/`igdb-pin` clear `igdb_review_reason`.

## Out of scope / deferred

- Auto-applying fixes (still owner-gated, flag-only).
- Batching stored-entry fetches.
- Persisting the suggested candidate (modal re-fetches candidates live).
- Caching resolved ids into `bundle_catalog` (the SP-A identity layer).

## Owner rules honored

`uv run python -m pytest`; `ruff check` only (never `ruff format`); work on `main`;
Phase-1–4-style code is **subagent TDD** (temp DB + monkeypatched IGDB, never the
live DB / app / live IGDB / push); the live re-audit is a **controller** operation;
commit trailer `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
