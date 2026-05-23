# Deduplication — design

**Date:** 2026-05-23
**Status:** approach approved (pending spec review); ready for implementation
**Workstream:** 3 of 4 (dedup) — see [[post-import-cleanup-workstreams]]

## Goal

Collapse duplicate library entries so there is **one row per playable game** —
the thing you launch and play — not one row per purchase, edition, region, or
platform. Driven from a **GUI "Dedup" modal**: exact matches auto-merge and are
reported; everything less than a 100% match is confirmed with a yes/no; a "no" is
remembered so it never reappears. Durable, general logic + extensible tables, not
one-time DB patches ([[cleanup-fixes-must-be-general]]).

Concrete duplicates observed (same game, different rows):
- Cross-platform: `Don't Starve` Switch (262, curated) + `Don't Starve: Console
  Edition` PS4 (44, curated) + `Don't Starve` Switch w/ NSUID (822) — one game.
  `Don't Starve Together` (263) is a **separate** game and must not merge.
- Subtitle/edition: `Disco Elysium` (715) + `Disco Elysium: The Final Cut` (629);
  `Connection Haunted` (234) + `/Connection Haunted <SERVER ERROR>` (824);
  `The Outer Worlds` + `…: Spacer's Choice Edition`.
- Exact-key collisions after the workstream-2 reclean: Brotato (216/620),
  Don't Starve (262/822), Fantasy Life i (633/794), Zelda BotW (465/700).

## Project intent (README + code)

This app tracks games and your progress **to decide what to play or buy next** —
not a wishlist, checklist, or a catalog of every edition/version you own. It can
be bent that way, but you will be fighting the grain; the data model is one row
per playable game. The repo is **MIT-licensed** — fork it freely. State this in
`README.md` ("What this is / isn't"), echo it in `dedup.py`'s module docstring
(dedup enforces the one-row-per-game rule), and add a `LICENSE` (MIT) if absent.

## Non-goals

- **Bundle expansion** (e.g. the FF Pixel Remaster bundle that grants 6 separate
  games, each a real entry, making the bundle a phantom) — that is workstream 4
  (`2026-05-22-bundle-expansion-design.md`). Single-product launchers played as
  one thing (Assassin's Creed: The Ezio Collection, GTA Trilogy) deliberately
  stay one game — the bundle spec already lists these as non-goals.
- **DLC attachment** (listing owned DLC under its parent on the detail view) —
  future feature; import already skips DLC.
- **Auto-merging fuzzy/edition candidates** — those always require a yes/no.

## Architecture

New `dedup.py` holds the pure, testable core (detection + merge engine). `app.py`
gets thin API endpoints over it; the existing frontend gets a "Dedup" modal. This
mirrors `import_scraped.py` / `fetch_covers.py` and keeps `app.py` from growing.

## Data model

One idempotent migration in `models.py` (alongside the existing `migrate_*`):

```sql
CREATE TABLE IF NOT EXISTS not_duplicates (
    game_id_lo INTEGER NOT NULL,
    game_id_hi INTEGER NOT NULL,        -- store the pair ordered lo < hi
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (game_id_lo, game_id_hi),
    FOREIGN KEY (game_id_lo) REFERENCES games(id) ON DELETE CASCADE,
    FOREIGN KEY (game_id_hi) REFERENCES games(id) ON DELETE CASCADE
);
```

Records pairs confirmed *not* the same game, so they never resurface. Cascade-
cleaned when either game is deleted (e.g. by a later merge). No change to `games`.

## Detection — `find_duplicate_groups(conn) -> {definite, candidates}` (pure)

Computes a **fresh key** `normalize_title(clean_title(title))` for every game in
memory (so stale stored `normalized_title` does not matter), then tiers, skipping
any pair already in `not_duplicates`:

- **definite** — identical fresh keys, grouped (transitive). Auto-mergeable and
  reported. (The 4 collisions above.)
- **candidates** — pairs flagged for yes/no, each with a `reason` + `score`:
  - **edition**: equal after stripping a known edition/qualifier suffix from both
    (extensible `EDITION_QUALIFIERS` table — Definitive, Final Cut, Console
    Edition, Spacer's Choice Edition, GOTY, Complete, Deluxe, Ultimate, Remastered,
    Anniversary, Enhanced, …). Reuses workstream-2 suffix knowledge.
  - **contains**: one fresh key word-contains the other (Disco Elysium ⊆ …Final
    Cut; Connection Haunted ⊆ …<SERVER ERROR>).
  - **similar**: `difflib.SequenceMatcher` ratio of the fresh keys ≥ 0.85.

Siblings (Don't Starve vs Don't Starve Together) can appear as a *candidate* but
are never *definite*; answering "no" records them in `not_duplicates`.

## Merge engine — `merge_games(conn, survivor_id, drop_ids, *, title, curation, dry_run)`

For each drop:
- `UPDATE game_external_ids SET game_id = survivor` (PK is global `(source,
  external_id)`, so no conflict) — re-scrapes now match the survivor and never
  recreate the phantom (idempotent).
- `INSERT OR IGNORE` the drop's `game_platforms` and `game_tags` onto the survivor.
- Delete the drop's `games` row; `ON DELETE CASCADE` removes its leftover
  platform links / external ids / tags / rating.

Then on the survivor:
- Set `title` to the chosen/edited name; recompute `normalized_title =
  normalize_title(clean_title(title))`.
- Write the combined curation (below).

`dry_run` returns the planned changes and writes nothing. Supports a group (one
survivor, N drops). **Survivor default**: the curated row if exactly one is
curated, else the better-cover / lower-id row; the modal can flip which survives.

### `compute_merged_curation(rows) -> dict` (pure)

- `status`: furthest along — rank completed > playing > dropped > backlog > wishlist.
- `rating`, `hours_played`, `priority`: the higher value.
- `notes`: concatenate non-empty (survivor first), deduped.
- `series_id` / `series_order`, `started_at` (earliest), `completed_at` (latest),
  `sort_order` (survivor's): keep whichever is set; survivor wins true ties.

The modal shows these combined values and lets you edit before confirming.

## Closing workstream 2's loose end

After a dedup run, refresh stored `normalized_title = normalize_title(clean_title
(title))` for every game. This was deferred in workstream 2 to avoid `UNIQUE`
collisions; with the dupes merged it is now collision-free, so import matching
(`import_scraped.resolve_game`) uses the improved keys. Report any residual
collision instead of crashing (there should be none).

## API (`app.py`, thin over `dedup.py`)

- `GET /api/duplicates` → `{definite: [...groups...], candidates: [...pairs...]}`;
  each game carries id, title, platforms, cover_url, and a curation summary;
  `not_duplicates` pairs excluded.
- `POST /api/games/merge` `{survivor_id, drop_ids, title, curation}` → runs
  `merge_games`, returns the merged game. Validates ids exist.
- `POST /api/duplicates/dismiss` `{game_id_a, game_id_b}` → inserts the ordered
  pair into `not_duplicates`.

## Frontend — "Dedup" modal

A "Dedup" button in the top toolbar opens a modal that loads `GET /api/duplicates`:
- **Definite matches**: short list with **"Merge all"** (just merge + report:
  `Merged "…Switch Edition" → "Don't Starve"`). Survivor defaults as above.
- **Review candidates**: stepped one at a time, each showing both games side by
  side (cover, title, platforms, combined curation) and the **reason + score**:
  - **Keep-which** toggle (which row survives; affects surviving cover/id).
  - **Name**: radio of the candidate names (default = base name) + a free-text
    field to edit (others may prefer the expanded name).
  - **Curation preview**: the auto-combined values, editable.
  - **Merge** · **Keep separate** (writes `not_duplicates`, never re-asked).
- On finish, the `normalized_title` refresh runs.

The UX will be built then refined against feel (user reviews live).

## Testing

Pure core unit-tested (TDD), API via the Flask test client (existing conftest):
- `find_duplicate_groups`: identical keys → definite group; edition / contains /
  similar≥0.85 → candidate with reason; `not_duplicates` pair excluded; siblings
  never definite.
- `compute_merged_curation`: status rank, max rating/hours, notes concat, ties.
- `merge_games` (in-memory DB): moves external_ids, `INSERT OR IGNORE`
  platforms+tags, combines curation, sets survivor title + recomputes key, deletes
  drops (cascade); `dry_run` writes nothing; idempotent (re-detect finds nothing;
  a re-scrape matches the survivor via the moved external_id).
- `not_duplicates` migration: created, idempotent, FK cascade enforced.
- `normalized_title` refresh: updates keys with no `UNIQUE` error post-merge.
- API: `merge`, `dismiss`, `duplicates` list. Existing suite stays green.

## Constraints

- Public repo: never commit `games.db`, `games.db.bak*`, `config.json`,
  `.igdb_token.json`, `.recon/`, `scraped/`, `.pw-profile/` (all gitignored).
- Conventional commit style, no co-author trailer.
- Back up `games.db` (timestamped `games.db.bak-*`) before any merge run
  ([[canonical-rename-equality-only]] — preview/back up before bulk DB mutations).
- `dedup.py` keeps `app.py` under the split threshold.
- `not_duplicates` is id-keyed (cascade-cleaned); a from-scratch re-import (new
  ids) loses dismissals — acceptable, the live DB is the working copy.
