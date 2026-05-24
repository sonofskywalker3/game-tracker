# DLC tracking (foundation) — design

**Date:** 2026-05-24
**Branch:** main (work directly on main, per repo workflow)
**Status:** approach approved; ready for implementation plan

## Goal

Each game gets a **DLC tab** in its modal listing all DLC/expansions for that game,
with a checkbox per item for what the user owns. The list is auto-populated from
**IGDB** (the only cross-platform source of a game's full DLC catalogue), wired
into the import pipeline so it happens when the user scrapes & imports. Ownership
is manual (checkboxes); manual DLC entries can be added/removed.

This is **Feature A (foundation)** = pieces 1 + 2 of the larger DLC idea:
1. DLC data model + tab UI with manual own/un-own checkboxes.
2. IGDB auto-populates the full DLC list per game.

Pieces 3 (scrape-driven ownership) and 4 (edition→included-content auto-check) and
the separate **GUI "scrape now" button** are explicitly later specs (see Out of
scope). This follows the project principle: durable general pipeline logic plus an
extensible/idempotent enrichment, never one-time DB patches
(`cleanup-fixes-must-be-general`).

## Background (verified facts)

- No DLC concept exists in the schema. Tables: `games`, `user_ratings`
  (game_id PK), `game_platforms` (with `owned` flag), `game_external_ids`
  (`source_title` preserves the exact vendor string), `series`, `tags`,
  `game_tags`, `not_duplicates` (`models.py:90-162`).
- Vendor scrapers read the user's *purchased library* and currently **drop** all
  DLC (Nintendo `7005` NSUIDs skipped `scrapers/nintendo.py:46-66`; Xbox keeps only
  `itemTypeName == "Game"`; `import_scraped.NON_GAME_PATTERN` drops
  "dlc/season pass/expansion" `import_scraped.py:44-49`). No vendor exposes a
  per-game DLC catalogue — only owned items. So scraping can later mark *owned*
  DLC (piece 3), but cannot produce the full list.
- IGDB has `dlcs`, `expansions`, `standalone_expansions` (and `bundles`) on
  `/v4/games`. The existing auth + query path is reusable: `fetch_covers.py`
  loads Twitch creds from `config.json` (`config.get_twitch_credentials`), gets a
  token via `get_access_token` (cached in `.igdb_token.json`,
  `fetch_covers.py:96-124`), and POSTs apicalypse to `https://api.igdb.com/v4/games`
  (`fetch_covers.py:127-151`). Today it requests only `name, cover.url`. We do NOT
  currently store the IGDB numeric id.
- The game modal is JS-rendered from `GET /api/games/<id>` in
  `loadGameModal` (`templates/base.html:735+`); the DLC tab slots into that render.

## Data model

New child table (a DLC is a child of a game, never a `games` row):

```sql
CREATE TABLE IF NOT EXISTS dlc (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id    INTEGER NOT NULL,
    name       TEXT    NOT NULL,
    igdb_id    INTEGER,            -- the DLC's own IGDB id; NULL for manual entries
    kind       TEXT    DEFAULT 'dlc',   -- 'dlc' | 'expansion'
    owned      INTEGER DEFAULT 0,  -- 0/1 ownership checkbox
    source     TEXT    DEFAULT 'igdb',  -- 'igdb' | 'manual'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (game_id, name),
    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_dlc_game ON dlc(game_id);
```

One new column on `games`:

```sql
ALTER TABLE games ADD COLUMN igdb_id INTEGER;  -- resolved parent IGDB id (additive)
```

`UNIQUE(game_id, name)` de-dupes on re-fetch. `ON DELETE CASCADE` removes a game's
DLC with the game.

*Approaches considered:* (a) dedicated `dlc` child table **[chosen]**; (b) DLC as
`games` rows with a parent id — rejected (DLC isn't a library game; pollutes
counts/lists); (c) JSON blob on `games` — rejected (not queryable; no clean
per-DLC checkbox state).

## IGDB enrichment

New module **`igdb_dlc.py`** with three concerns, the pure ones unit-tested and the
network one isolated/mockable:

1. **Fetch** (`fetch_game_dlc(...) -> dict`, network): reuse
   `fetch_covers.get_access_token` for auth (no duplicated token logic). Resolve
   the game's IGDB id, then query
   `fields name, cover.url, dlcs.name, expansions.name, standalone_expansions.name; where id = <igdb_id>;`.
   Resolution has two paths:
   - **By title** (auto, during import): reuse the existing title search; store the
     top match on `games.igdb_id`. Best-effort (may mis-match — see "Pinning the
     IGDB identity").
   - **By slug** (manual correction): when an IGDB game URL is supplied, query
     `where slug = "<slug>"; fields ...;` to resolve the exact game.
   The cover URL is formatted with the existing `t_thumb`→`t_cover_big` /
   `https:`-prefix rule (`app.py:1086-1088`).
2. **Parse** (`parse_dlc_payload(igdb_game: dict) -> list[dict]`, pure): flatten
   `dlcs` + `expansions` + `standalone_expansions` into
   `{name, igdb_id, kind}` dicts. `kind='expansion'` for expansions/standalone
   expansions, else `'dlc'`. Drop blanks; de-dupe by name within the payload.
   `bundles` are excluded.
3. **Merge** (`merge_dlc(conn, game_id, parsed) -> dict` counts, writes): for each
   parsed DLC, `INSERT OR IGNORE` into `dlc` (the `UNIQUE(game_id, name)` keeps it
   idempotent). **Never updates existing rows** — `owned` and `manual` entries are
   preserved; only genuinely new DLC is appended. Returns `{added, existing}` for
   reporting.

**Idempotency / generality:** re-running enrichment (re-import or the refresh
button) only appends newly released DLC and never clobbers ownership or manual
entries — re-import reproduces the same clean state.

**Trigger:** wired into `import_scraped` after each vendor import, **incremental** —
only games whose DLC has not yet been resolved (no `games.igdb_id` set, i.e. never
enriched) are fetched, so re-imports stay fast. A `--no-dlc` CLI flag skips
enrichment entirely. The per-game refresh endpoint forces a re-fetch for one game.
Enrichment failures (network/credentials/no IGDB match) are logged and skipped —
they never abort an import. All fetched DLC starts `owned = 0`.

## API (Flask, JSON; follows existing `/api/games/...` patterns)

- `GET /api/games/<id>` — extend the response with
  `"dlc": [{id, name, kind, owned, source}, ...]`, ordered by `kind` then `name`
  (stable), so the modal renders from one fetch.
- `POST /api/dlc/<dlc_id>/owned` body `{owned: bool}` — set the checkbox; returns
  `{ok, owned}`. 404 if the dlc id is unknown.
- `POST /api/games/<id>/dlc` body `{name, kind?}` — add a manual entry
  (`source='manual'`, `igdb_id=NULL`); returns the row. If a row with that
  `(game_id, name)` already exists, return the existing row (200, no duplicate
  created).
- `DELETE /api/dlc/<dlc_id>` — remove an entry; returns `{ok}`.
- `POST /api/games/<id>/dlc/refresh` — re-fetch from IGDB using the stored
  `games.igdb_id` (or by title if unset), merge, return the updated `dlc` list and
  `{added, existing}`. Network-dependent; on failure returns a JSON error with a
  non-500 status the UI can surface.
- `POST /api/games/<id>/igdb` body `{url}` — **pin the IGDB identity**. Parse the
  slug from an `igdb.com/games/<slug>` URL, resolve the exact game via IGDB, then
  set `games.igdb_id`, update `cover_url` from IGDB, and merge its DLC. Returns the
  updated game (incl. `cover_url`) + `dlc` list. Rejects (400) a URL that isn't an
  IGDB game URL.

## Pinning the IGDB identity (cover + DLC)

Auto title-resolution is best-effort and can match the wrong IGDB game, which would
show the wrong cover *and* the wrong DLC. The correction is a single user action:
paste the game's IGDB page URL (`https://www.igdb.com/games/<slug>`) into the
existing **Cover Art URL** field. On Save, the frontend detects an IGDB game URL
(regex on host `igdb.com` + path `/games/<slug>`) and routes it to
`POST /api/games/<id>/igdb` (which pins `igdb_id`, refreshes the cover, and
re-fetches DLC); any other value is treated as a literal cover image URL via the
existing `PUT /api/games/<id> {cover_url}` path, exactly as today. The field's
placeholder/help text is updated to mention this. This one field thus corrects
identity for both cover art and DLC; there is no separate "add the game" control.

## UI — DLC tab in the game modal

In `loadGameModal` (`templates/base.html`), split the modal body into a small tab
strip and two panels:

- Tab strip: **`Details | DLC (owned/total)`** — the count comes from the `dlc`
  array; `DLC (0)` when empty. Clicking toggles which panel shows (client-side
  only; no reload).
- **Details** panel = today's modal content, with one change: the **Cover Art URL**
  field's Save now smart-routes an `igdb.com/games/<slug>` URL to
  `POST /api/games/<id>/igdb` (pins identity + cover + DLC); any other value is a
  literal cover URL as today. Placeholder/help text updated to say so.
- **DLC** panel: a checklist; each row `[checkbox owned] Name  <kind badge>`. The
  checkbox calls `POST /api/dlc/<id>/owned` on change (same inline-save UX as other
  fields). A **"Refresh from IGDB"** button calls the refresh endpoint and
  re-renders the list. An **"+ Add DLC"** inline text input posts a manual entry.
  Manual rows show a `×` that calls DELETE. Empty state:
  "No DLC found — Refresh from IGDB or add manually."

DLC is shown in the modal only (not on the grid card) for this foundation.

## Testing (TDD)

Pure / unit (no network):
- `parse_dlc_payload`: a fake IGDB game dict with `dlcs` + `expansions` +
  `standalone_expansions` → normalized dicts with correct `kind`; blanks dropped;
  in-payload de-dupe.
- `merge_dlc` idempotency (temp DB): first merge inserts; second merge with the
  same payload adds nothing and preserves an `owned=1` row and a `source='manual'`
  row.

API (temp-DB `client` fixture, IGDB mocked):
- `GET /api/games/<id>` includes the `dlc` array.
- toggle owned; add manual (and duplicate no-ops); delete; refresh (monkeypatch the
  `igdb_dlc` fetch to return a fixed payload) merges and returns the list.
- `POST /api/games/<id>/igdb` with an IGDB game URL (fetch mocked): sets
  `games.igdb_id`, updates `cover_url`, and populates `dlc`. A non-IGDB URL → 400.
- slug parse helper: `https://www.igdb.com/games/elden-ring` → `elden-ring`;
  a raw image URL / non-IGDB URL → None (so the UI routes it to the cover path).

Import integration (temp DB, IGDB fetch mocked):
- importing a game runs enrichment and populates `dlc`; a second import skips the
  already-enriched game (incremental); `--no-dlc` skips enrichment.

Migration: `migrate_db` adds the `dlc` table and `games.igdb_id` to an existing DB
without data loss; startup is idempotent.

Existing suite stays green (currently 179 in the bundle branch; this adds the new
DLC tests).

## Out of scope (later specs)

- **Edition → included-content auto-check** ("Ultimate Edition" / "Day One
  Edition" auto-checking the DLC they bundle) — needs a curated edition→DLC lookup
  table (à la `BUNDLE_CONTENTS`). Piece 4.
- **Scrape-driven ownership** — un-drop vendor DLC and mark owned by matching
  vendor add-on SKUs to DLC entries. Piece 3.
- **GUI "scrape now" button** (Add Game modal) — run the browser-driven vendor
  scrapers + import + enrich from the web app; its own subsystem (subprocess
  orchestration, interactive vendor login/2FA, progress UI). Separate spec, planned
  next after this one.
- DLC count/badge on the grid card.

## Constraints

- Public repo: never commit `games.db`, `games.db.bak*`, `.recon/`, `scraped/`,
  `.pw-profile/`, `config.json`, `.igdb_token.json`, `excluded_games.json`,
  `series_patterns.json` (gitignored; `series_patterns.default.json` is committed).
- Conventional commits, no co-author trailer. Work on `main`.
- Network/credentials isolated behind `igdb_dlc` so the suite runs offline; back up
  `games.db` before any live enrichment run.
