# Dedup grouping, series creation & manual exclusion — design

**Date:** 2026-05-23
**Status:** approach approved (pending spec review); ready for implementation plan
**Workstream:** 3 (dedup) — extends [[post-import-cleanup-workstreams]] and
`2026-05-23-dedup-design.md`. All durable/general per [[cleanup-fixes-must-be-general]].

## Why

The dedup engine works, but the live scan returns **4 definite groups + 279
candidate _pairs_** (159 contains / 27 edition / 93 similar). Reviewing 279 pairs
one at a time is unworkable, and the noise is concentrated in **series entries
that differ only by a number/numeral** — the strongest "NOT a duplicate" signal:

- `similar`: **74 / 93** differ only by a number/numeral (Final Fantasy VII vs II,
  III, IV, V, VI, IX, XIV, XV, XVI …).
- `contains`: **39 / 159** number-only, plus most of the rest are series-prefix
  matches (`Final Fantasy` ⊆ everything, `Batman` ⊆ `Batman: Arkham VR`).

The user's rule: **detection must not drop anything** ("I don't want it to miss
anything"). So recall stays 100%; the fix is **presentation + bulk actions** —
cluster related candidates into families and act on a whole family at once.

Connected components of the candidate graph already produce coherent families on
the real library: **74 groups** over 246 games — Final Fantasy (34), Assassin's
Creed (18), Dragon Quest (9), SteamWorld (7), Borderlands (7), Batman (5), and
**50 simple 2-game pairs**. No unrelated franchises chain together.

## Goals

1. **Grouped review** — cluster candidate pairs into families (connected
   components). Per group: **Mark all safe** (bulk-dismiss), **inline quick-merge**
   for the real dups, and per-item **remove from group** (pull out a false member
   so it stops affecting the group's bulk actions).
2. **Create a series from a group** — IGDB-canonicalized name, membership confirmed
   in a dialog, persisted to a **per-user, durable** pattern table so a re-import
   reproduces it.
3. **Manual "Not a game"** — a durable exclusion that survives re-import (e.g.
   `Animal Crossing: New Horizons Island Transfer Tool` 819, which today returns on
   every scrape), keyed by `(source, external_id)` + exact normalized title.

## Non-goals

- **Bundle expansion** (`Final Fantasy I-VI Bundle` 809 → its 6 Pixel Remaster
  games, which do **not** yet exist as rows; `Holy Potatoes! A Bundle?!` →
  its constituent shops). Bundles need *expansion*, not deletion — that is
  **workstream 4** (`2026-05-22-bundle-expansion-design.md`). The dedup modal only
  lets you *remove from group* or *mark not-a-game* a phantom; it never expands.
- **Filtering/suppressing candidate detection** — recall stays 100%.
- **Full library-wide IGDB franchise auto-classification** — possible later
  workstream; here IGDB is consulted only at series-create time for one name.
- **Community-contributed shared database sync** — a future opt-in. The per-user
  files below use a stable, mergeable schema *so that* contribution is possible
  later, but no sync/upload is built now.

## Architecture

Pure, testable core stays in `dedup.py`; `app.py` gets thin endpoints; the
frontend gets the grouped modal. IGDB calls reuse the existing helpers
(`fetch_covers.py` / `app.py:api_series_missing_games`); if that pushes `app.py`
past the ~400-line split threshold, extract the shared client into `igdb.py`.

## A. Grouping — `dedup.py` (pure)

```python
def group_candidates(candidates: list[dict]) -> list[dict]: ...
# union-find over the candidate pairs -> connected components.
# returns [{"members": [ids sorted], "pairs": [[lo, hi], ...]}], sorted size desc.
```

`find_duplicate_groups` is unchanged (still returns `definite` + `candidates`);
grouping is layered on top so existing tests stay valid. Members are only games
that appear in at least one candidate pair.

## B. Series naming — `dedup.py` + IGDB

```python
def infer_series_name(titles: list[str]) -> str: ...
# pick the member title that is a word-prefix of the most other members
# ("Final Fantasy" 287 prefixes ~15 others); fall back to longest common word
# prefix; strip trailing separators/colons. Pure.
```

- **Existing-series detection:** match the inferred name (case-insensitive)
  against the `series` table, and check whether members already carry a
  `series_id`. Group payload exposes `existing_series_id | None` so the card shows
  **Create series** vs **Add to series**.
- **IGDB canonicalization (create time, server-side):** query `/franchises` then
  `/collections` by the inferred name (same pattern as `api_series_missing_games`)
  and return a canonical name suggestion plus an optional release-ordered title
  list. If IGDB is unconfigured/unreachable, fall back to the inferred name — the
  feature must work offline.

## B2. Durable series patterns (per-user)

The hardcoded `known_series` dict in `models.auto_populate_series` becomes data:

- `series_patterns.default.json` — **committed** seed `{prefix: name}`, migrated
  verbatim from today's `known_series`.
- `series_patterns.json` — **gitignored** per-user file; created by copying the
  default if missing.
- `models.load_series_patterns() -> dict[str, str]` — load the user file (or
  default).
- `models.add_series_pattern(prefix, name) -> bool` — idempotent append to the
  user file.
- `auto_populate_series()` reads `load_series_patterns()` instead of the literal.

Creating a series with **"remember for future imports"** checked appends the
inferred prefix → canonical name. Prefix-coherent series re-import cleanly;
fuzzy-only groups (no shared prefix) are best-effort — noted to the user in the
dialog.

## C. Manual "Not a game" durable exclusion

The committed title defaults (`NON_GAME_APPS`, `NON_GAME_PATTERN` in
`import_scraped.py`) stay as the shared seed. Add a **per-user** layer for manual
marks, keyed precisely so real games are never caught (`Tools Up!` 473,
`Holy Potatoes! A Bundle?!` 639 stay safe):

- `excluded_games.json` — **gitignored** list of
  `{source, external_id, normalized_title, title}`.
- `import_scraped.is_excluded(source, external_id, title) -> bool` — true if the
  scraped row matches an entry by `(source, external_id)` **or** (for source-less
  rows) by exact `normalize_title(clean_title(title))`.
- Importer skips a row when `is_non_game(title) or is_excluded(...)`.
- `POST /api/games/<id>/not-a-game` — record **all** of the game's
  `game_external_ids` plus its normalized title to `excluded_games.json`, then
  delete the game row (`ON DELETE CASCADE` cleans children). Re-import then skips
  it forever.

Exposed both as a per-item action in the dedup modal **and** on the game
detail/edit view (manual marking is a general capability, per the user).

## Modal UX — grouped review

"Exact matches → Merge all" stays on top, unchanged. The flat one-pair walker is
replaced by groups (largest first, collapsed by default; header alone is enough to
"Mark all safe" an obvious family).

```
Review — 74 groups (246 games)
──────────────────────────────────────────────────────────────
▾ Final Fantasy · 34 games · no series yet  [Create series] [Mark all safe]
    ☐ Final Fantasy VII                PS4 · completed ★4     ✕ 🚫
    ☐ Final Fantasy VII Remake         PS4 · playing          ✕ 🚫
    ☐ Final Fantasy VII Remake Intergrade  PS5 · backlog      ✕ 🚫
    ☐ XIII                             PS4 · backlog          ✕ 🚫   ← remove (false member)
    ☐ Final Fantasy I-VI Bundle        —   · backlog          ✕ 🚫   ← bundle (workstream 4)
    …
                                              [ Merge selected (0) ]
▸ Assassin's Creed · 18 games · series exists  [Add to series] [Mark all safe]
▸ Ghost of Tsushima · 2 games · series exists  [Add to series] [Mark all safe]
```

Per item: **checkbox = check-to-merge** (never overloaded), **✕ remove from
group**, **🚫 not a game**. Footer **Merge selected (n)** enables at ≥2 checked and
drops the inline quick-merge bar (survivor dropdown auto-picking the curated/covered
row, editable name, Confirm) — no separate step.

**Per-group actions**

| Action | Effect |
|--------|--------|
| Merge selected | ≥2 checked → inline confirm → `POST /api/games/merge` |
| ✕ Remove from group | dismiss that game's pairs with the other members (`not_duplicates`), drop it from the card; shrinks Mark-all-safe & Create-series scope |
| 🚫 Not a game | `POST /api/games/<id>/not-a-game` (exclude + delete) |
| Mark all safe | dismiss all remaining internal pairs (the family is distinct, none are dups) |
| Create series | opens the confirm dialog (below); shown only when no series exists |

**Create-series confirm dialog**

```
Create series
─────────────────────────────────────
Name: [ Final Fantasy ]        (IGDB-canonicalized, editable)
Include:  ☑ Final Fantasy VII   ☑ …Remake   ☑ Final Fantasy II   …
☑ Remember for future imports   (adds "Final Fantasy" to your pattern table)
☐ Order by release date (IGDB)
                       [ Create ]   [ Cancel ]
```

## API (`app.py`, thin)

- `GET /api/duplicates` → add `groups` (keep `definite`, `games`).
- `POST /api/duplicates/dismiss` → also accept `{pairs: [[a, b], ...]}` for bulk
  (Mark-all-safe, Remove-from-group); keep the single-pair shape.
- `GET /api/series/igdb-suggest?name=<inferred>` → `{canonical_name,
  ordered_titles?}` for the confirm dialog; degrades to the inferred name.
- `POST /api/series/from-group` `{name, prefix, game_ids, remember,
  sort_by_release}` → create-or-find the series, assign games in order, optionally
  `add_series_pattern`, optionally IGDB release-sort. Atomic; reuses existing
  create/assign internals.
- `POST /api/games/<id>/not-a-game` → exclude (record ids + title) then delete.

## Testing (TDD)

Pure core unit-tested; API via the existing Flask test client/conftest:

- `group_candidates`: components, isolated 2-game pairs, the 34-member FF
  component, size-desc ordering, no cross-family chaining.
- `infer_series_name`: Final Fantasy / Assassin's Creed / SteamWorld; no-common-
  prefix fallback; trailing-separator trim.
- `load_series_patterns` / `add_series_pattern`: seed-from-default when user file
  missing, idempotent append, writes only the user file.
- `is_excluded` + `/not-a-game`: id-keyed and title-keyed match; delete cascades;
  a follow-up import skips the excluded row (in-memory import test).
- bulk `dismiss` (pairs) endpoint.
- `from-group`: creates/assigns in order, appends pattern when `remember`, IGDB
  mocked; `igdb-suggest` falls back when IGDB absent.
- Existing 114 tests stay green.

## Constraints

- Add `series_patterns.json` and `excluded_games.json` to `.gitignore` **before**
  creating them; ship `series_patterns.default.json` **committed**. Verify
  `.gitignore` first (CLAUDE.md security pattern).
- Never commit `games.db`, `games.db.bak*`, `config.json`, `.igdb_token.json`,
  `.recon/`, `scraped/`, `.pw-profile/`.
- Conventional commit style, no co-author trailer.
- Back up `games.db` (timestamped `games.db.bak-*`) before any live merge/exclude
  run ([[canonical-rename-equality-only]]).
- Keep `dedup.py` / `app.py` under the ~400-line split threshold; IGDB helpers in
  `igdb.py` if needed.
- Per-user JSON files use a stable, mergeable schema so a future community-
  contribution opt-in can fold them into the committed defaults.

## Suggested build phases (for the implementation plan)

1. **Grouping** — `group_candidates`, grouped modal view, bulk `dismiss`,
   remove-from-group, inline quick-merge. (Thread A — the original ask.)
2. **Series** — patterns refactor (default + per-user files), `infer_series_name`,
   IGDB suggest, `from-group`, create-series dialog. (Thread B.)
3. **Not-a-game** — `excluded_games.json`, `is_excluded`, `/not-a-game`, modal +
   game-detail actions. (Thread C.)
