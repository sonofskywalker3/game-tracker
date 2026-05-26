# DLC SP3 — modal source-link, cover button, conflict resolution — design

**Date:** 2026-05-25
**Branch:** main (work directly on main, per repo workflow)
**Status:** approach approved; ready for implementation plan

This is **SP3** of the DLC source-of-truth rework. SP1 (vendor-source foundation +
Nintendo/Xbox owned-add-on fix) and SP2 (Steam end-to-end) have landed on `main`
(275 tests green; live Steam scrape pending manual verification). SP3 closes the
per-game UX side of the rework and surfaces SP1's queued conflicts.

Foundation spec: `docs/superpowers/specs/2026-05-25-dlc-vendor-source-foundation-design.md`.
SP2 spec: `docs/superpowers/specs/2026-05-25-dlc-steam-vendor-design.md`.

## Goal

Three coordinated UX/data changes around the existing per-game modal + Add Game
modal + DLC pipeline:

1. **Source-of-truth link** in the per-game modal replaces the existing "Cover Art
   URL" field. Accepts IGDB and Steam URLs; pins identity on save. The old foot-gun
   — pasting an IGDB URL into the field labelled "Cover Art URL" to make it
   smart-route — goes away.
2. **Cover image picking is decoupled** from the source link via a "Change cover"
   button (opens the source's media page in a new tab) and a dedicated literal
   "Cover image URL" input.
3. **A dedup-style resolution modal** surfaces SP1's queued `review` items so the
   user can resolve "no parent game" / "ambiguous parent" / "ambiguous dlc" cases
   interactively. The queue is persisted across scrapes.
4. **Manual game-add auto-populates DLC** when an IGDB URL is pinned during add;
   Steam-pinned games record the appid and defer DLC to the next Steam scrape.

After SP3, every game has a single canonical place to pin its source, a separate
place to change its cover, and every uncertain owned-add-on the scraper found has
a workflow to actually resolve.

## Decisions (approved in brainstorming)

- **Source-link scope:** IGDB + Steam URLs only. PSN / Xbox / Nintendo URLs are
  rejected with a clear message until SP4–6 ship each vendor's deep-fetch — we
  never silently pin an identity the rest of the app can't act on.
- **"Change cover" affordance:** a button that opens the pinned source's media
  page in a new tab (IGDB search by title if pinned to IGDB; Steam store page if
  pinned to Steam; Google image search otherwise) **plus** a separate small
  literal "Cover image URL" input. No in-app media picker (deferred).
- **Review queue:** a new `dlc_review_queue` table, persisted, UPSERTed by
  `(source, external_id)` on every scrape. Modal opens from the Add Game modal
  (count badge). No auto-open after scrape.
- **Resolution actions:** search-pick existing game / DLC + dismiss. No inline
  "create a new game" — if the base game isn't owned yet, dismiss and use the
  existing Add Game flow.
- **Manual-add enrichment:** sync IGDB enrichment (matching the existing
  `/api/games/<id>/igdb` pattern); Steam-pinned games only record the appid (DLC
  enrichment defers to next Steam scrape — Steam's per-DLC `appdetails` is
  rate-limited at 200/5min, so a sync path is fragile).

## Scope / deferred

**In scope:**
- The two-modal field rename + `setSourceLink` smart-route refactor.
- A new `POST /api/games/<id>/steam` endpoint that records a Steam appid in
  `game_external_ids`.
- The "Change cover" button + dedicated literal "Cover image URL" input in both
  modals.
- New `dlc_review_queue` table + `migrate_dlc_review_queue` + UPSERT writes in
  `dlc_ownership.mark_ownership`.
- New `dlc_review.py` engine + `/api/dlc/review/{count,list,resolve,dismiss}`
  endpoints.
- New `#dlc-review-modal` in `templates/base.html` with per-item cards.
- Refactor sliver in `dlc_ownership.py`: extract the per-addon inner block into
  `_apply_addon_to_parent` so `dlc_review.resolve` can reuse it.
- Add Game submit flow chained POSTs.

**Deferred (explicitly out of scope):**
- Steam media picker / IGDB media grid. "Change cover" just opens the source's
  page in a new tab.
- PSN / Xbox / Nintendo URL pinning. The deep-fetch lands in SP4–6 first; the
  source-link field rejects those URLs in SP3.
- Auto-running a Steam catalogue refresh on manual add (rate-limited; defers to
  the natural Steam scrape).
- Inline game-creation inside the resolution modal. If a "no parent game" item
  truly needs a new game, dismiss + use the existing Add Game flow.
- Auto-opening the DLC Review modal at scrape end. The badge surfaces the count;
  user opens when ready.
- Migrating existing transient review items from previous scrapes into the new
  table. Next scrape repopulates them.

## Background (verified facts)

- **Per-game modal "Cover Art URL" field** lives at `templates/base.html:812-834`.
  The save handler `setCoverUrl` (`templates/base.html:1044`) sniffs
  `igdb.com/games/<slug>` and routes to `POST /api/games/<id>/igdb`; anything else
  goes to `PUT /api/games/<id>` as a literal `cover_url`.
- **`POST /api/games/<id>/igdb`** (`app.py:380-417`) calls
  `igdb_dlc.slug_from_igdb_url`, then `igdb_dlc.enrich_game(..., slug=slug)`,
  which sets `igdb_id`, refreshes cover, and merges DLC. Synchronous; returns
  `{game, dlc, report}`.
- **`igdb_dlc.slug_from_igdb_url`** (`igdb_dlc.py:51-56`) is the existing URL
  parser pattern to mirror for Steam.
- **Add Game modal** (`templates/base.html:1126-1373`) currently has a hidden
  `new-game-cover-url` field populated only by IGDB-typeahead picks
  (`selectIGDBGame`, line 1315). On submit (`addNewGame`, line 1335), it
  passes-through to `POST /api/games` as a literal `cover_url`. No enrichment is
  triggered on add.
- **`POST /api/games`** (`app.py:179-235`) inserts a new game row with optional
  `cover_url`. Does not pin IGDB identity, does not enrich DLC.
- **`game_external_ids`** (`models.py:104-112`):
  `(game_id, source, external_id, source_title, created_at)`,
  `UNIQUE(source, external_id)`. The established per-vendor id pattern; SP3 writes
  `source='steam'` rows on Steam URL pin.
- **`dlc_ownership.mark_ownership`** (`dlc_ownership.py:153-232`) builds
  `report.review` containing `Match(addon_title, game_id, dlc_id, reason)` items
  for three cases — `"no parent game"`, `"ambiguous parent"`, `"ambiguous dlc"`.
  Review items are returned to the caller; **nothing persists today**. The web
  pipeline (`scrape_service._run_pipeline`, lines 110-121, 149) serialises them
  into `status.summary.review` as `{title, reason}`, which the post-scrape result
  panel renders inline (`templates/base.html:1223-1228`) and then loses on the
  next page reload.
- **`Match` shape** (`dlc_ownership.py:103-110`):
  `addon_title`, `game_id?`, `dlc_id?`, `reason`.
- **Dedup modal model** (`templates/base.html:170-182`): modal frame
  `max-w-3xl max-h-[90vh]` with header + scrollable body, populated by `renderDedup`
  (`base.html:267-291`) from `GET /api/duplicates`. Per-card actions
  (`mergeWholeGroup`, `markAllSafe`, etc., lines 383-440) POST to
  `/api/games/merge` and `/api/duplicates/dismiss`. The "dismiss is durable"
  precedent is `not_duplicates` (`models.py:115`, schema mirrored in
  `tests/test_dedup.py:36`).
- **`dlc_external_ids`** (SP1) is the established per-DLC id table; SP3 doesn't
  touch its schema.
- **Steam URL shape:** `https://store.steampowered.com/app/<appid>(/<slug>)?(/?...)?`
  with optional trailing slug and query string. `<appid>` is `\d+`.

## Section 1 — Source-of-truth link field

### Per-game modal (`templates/base.html:812-834`)

- Label `"Cover Art URL"` → **`"Source link"`**.
- Placeholder → `"Paste an IGDB or Steam URL to pin this game's source"`.
- The existing search-link helper line (IGDB / PSN / Nintendo / Xbox) stays as a
  "find a URL to paste" aid; the helper line currently reading *"Tip: paste an
  IGDB game URL above to set the right cover + DLC."* becomes *"Tip: paste an
  IGDB or Steam URL to pin this game's source. Use the cover field below to set
  a literal image URL."*
- Save handler `setCoverUrl` (`templates/base.html:1044-1055`) is renamed to
  `setSourceLink` and routes:
  - Matches `https?://(www\.)?igdb\.com/games/<slug>` → `POST /api/games/<id>/igdb`
    (existing behavior; pins `igdb_id`, refreshes cover + DLC).
  - Matches `https?://store\.steampowered\.com/app/(\d+)` → `POST /api/games/<id>/steam`
    (new endpoint below).
  - Anything else → inline error rendered next to the Save button:
    *"Only IGDB or Steam URLs are accepted here. Use the cover image field below
    for a literal image URL."*. **The field no longer accepts arbitrary image
    URLs** — that path moves entirely to the dedicated cover input.

### Add Game modal (`templates/base.html:1126-1373`)

- The hidden `new-game-cover-url` field is replaced by a **visible** text input
  labelled `"Source link"`, same placeholder + same sniff/route — but the value
  is held client-side until submit (no per-keystroke POSTs).
- A new sibling input `"Cover image URL"` (optional) holds a literal cover URL.
- `selectIGDBGame` (`templates/base.html:1315-1319`) — invoked from the IGDB
  typeahead — now populates `new-game-source-link` with the IGDB game URL
  (`https://www.igdb.com/games/<slug>`) instead of `new-game-cover-url`. The
  IGDB search endpoint (`app.py:1464-1518`) currently issues
  `fields name, cover.url; limit 8;` and returns `[{name, cover_url}, …]`. SP3
  extends the query to include `slug` and adds `slug` + `igdb_url` to each
  result, so the typeahead can populate the source-link field directly.
- On submit (`addNewGame`):
  1. **Client-side validate `source_link` first.** If non-empty and matches
     neither the IGDB nor Steam regex → render inline error
     *"Only IGDB or Steam URLs are accepted here"* and abort **before** any
     POST. No "ghost game" gets created from a bad paste.
  2. `POST /api/games {title, platforms, cover_url?}` (with the literal cover
     input only) → `game_id`.
  3. If `source_link` matches the IGDB regex → `POST /api/games/<game_id>/igdb {url}`
     synchronously; the per-game modal opens after the IGDB call returns
     (typically 1–2s).
  4. Elif `source_link` matches the Steam regex → `POST /api/games/<game_id>/steam {url}`.
  5. Else (`source_link` empty) → open the per-game modal as today.
  6. On a 4xx from the IGDB or Steam pin call: surface the inline error; the
     game is already created (we accept this — one stranded game vs. crossing
     two endpoints to roll back is the simpler trade), and the user can pin
     from the per-game modal or delete it.

### New endpoint `POST /api/games/<id>/steam`

In `app.py`, alongside the existing `/api/games/<id>/igdb` route at line 380:

```python
@app.route('/api/games/<int:game_id>/steam', methods=['POST'])
def api_pin_steam(game_id):
    """Pin a game's Steam identity from a store.steampowered.com/app/<appid> URL:
    writes game_external_ids(source='steam', external_id=str(appid), ...).
    Does NOT run DLC enrichment (Steam catalogue is rate-limited; defers to the
    next Steam scrape per SP3 decision)."""
```

- Body: `{url: "https://store.steampowered.com/app/123456/..."}`.
- Calls `steam_dlc.appid_from_steam_url(url)` (new helper, mirrors
  `igdb_dlc.slug_from_igdb_url`); returns 400 with
  `{error: "Not a Steam store URL"}` on no match.
- Loads the game's title; `INSERT OR IGNORE INTO game_external_ids (game_id,
  source, external_id, source_title) VALUES (?, 'steam', ?, ?)` (UNIQUE(source,
  external_id) makes this idempotent).
- Returns `{appid: <int>, game: {id, title, cover_url, igdb_id}}`.

### New helper `steam_dlc.appid_from_steam_url`

```python
def appid_from_steam_url(url: str | None) -> int | None:
    """Extract the appid from a store.steampowered.com/app/<appid> URL, else None."""
```

Regex: `r"https?://store\.steampowered\.com/app/(\d+)"` (case-insensitive,
trailing path / query irrelevant). Mirrors `slug_from_igdb_url`'s shape.

## Section 2 — Change-cover affordance

In the per-game modal, **immediately below** the Source link row, two new sibling
elements:

- **"Change cover" button** — a plain `<a target="_blank">` styled as a button.
  Resolved client-side at render time from the game's pinned source:
  - IGDB-pinned (`game.igdb_id` set) → `https://www.igdb.com/search?type=1&q=<title>`.
    *(The IGDB game URL uses a slug we don't currently store; the search URL is
    deterministic and lands on the same game's media reliably.)*
  - Steam-pinned (`game.external_ids.steam.external_id` set — see API change
    below) → `https://store.steampowered.com/app/<appid>`.
  - Neither → `https://www.google.com/search?q=<title>+game+cover+art&tbm=isch`.
- **"Cover image URL" input + Save** — identical structure to the source-link
  row above. Save calls `PUT /api/games/<id> {cover_url: <value>}`. Accepts any
  URL string; no sniffing. This is now the **only** place to set a literal cover
  URL.

### `GET /api/games/<id>` response addition

Today the per-game GET (`app.py:237`-ish) returns `id, title, cover_url, igdb_id,
status, …`. SP3 adds `external_ids` as a small dict:

```json
"external_ids": {"steam": "123456", "playstation": "...", ...}
```

— populated by a join against `game_external_ids` filtered to known vendor
sources. Used by the front-end to decide the "Change cover" button's href and by
future SPs.

### Add Game modal cover field

The existing literal-cover behavior moves into a `"Cover image URL"` input
sibling to the new `"Source link"` input. The submit handler passes the cover
value through to `POST /api/games` as `cover_url` (existing route, no change).

## Section 3 — `dlc_review_queue` table

Persistence for `OwnershipReport.review` items so they survive across scrapes
and the resolution modal can be opened at any time.

```sql
CREATE TABLE IF NOT EXISTS dlc_review_queue (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    addon_title   TEXT    NOT NULL,
    source        TEXT,                              -- vendor label; null only for legacy/test items
    external_id   TEXT,                              -- vendor add-on id; null with source above
    source_title  TEXT,                              -- full vendor add-on string (provenance)
    reason        TEXT    NOT NULL,                  -- 'no parent game' | 'ambiguous parent' | 'ambiguous dlc'
    game_id       INTEGER,                           -- known parent when reason is 'ambiguous dlc'
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at   TIMESTAMP,                         -- when picked + reconciled by the modal
    dismissed_at  TIMESTAMP,                         -- when user said "not a real add-on"
    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE SET NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_dlc_review_vendor_id
    ON dlc_review_queue(source, external_id)
    WHERE source IS NOT NULL AND external_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_dlc_review_open
    ON dlc_review_queue(resolved_at, dismissed_at);
```

- **Partial unique index** (not a table-level `UNIQUE`) because SQLite treats
  NULLs as distinct in regular `UNIQUE`, which is the wrong shape — a same-vendor
  re-scrape of the same add-on should UPSERT, not duplicate, while legacy rows
  with null source/ext can still exist.
- **`ON DELETE SET NULL`** on `game_id` mirrors dedup's defensive posture — a
  dismissed parent game shouldn't tombstone the review row.
- **No FK on `dlc_id`** — the engine only writes `game_id` for "ambiguous dlc";
  the chosen DLC is recorded by the resolve action against the live `dlc` row at
  resolve-time.

### Write timing (in `dlc_ownership.mark_ownership`)

After building `report.review`, before returning, persist each review item with
an UPSERT:

```sql
INSERT INTO dlc_review_queue
    (addon_title, source, external_id, source_title, reason, game_id)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT (source, external_id) WHERE source IS NOT NULL AND external_id IS NOT NULL
DO UPDATE SET
    addon_title  = excluded.addon_title,
    source_title = excluded.source_title,
    reason       = excluded.reason,
    game_id      = excluded.game_id;
```

— so a re-scrape refreshes the reason (e.g. an ambiguous parent stopped being
ambiguous because the user merged games) without multiplying rows.
`resolved_at` / `dismissed_at` are intentionally **not** touched by the UPSERT;
once the user resolves or dismisses, a re-scrape of the same vendor id is a no-op
for that row.

### Open-queue read

Single shared SELECT used by the count endpoint, list endpoint, and tests:

```sql
SELECT * FROM dlc_review_queue
WHERE resolved_at IS NULL AND dismissed_at IS NULL
ORDER BY created_at, id;
```

### Migration

`models.migrate_dlc_review_queue(conn)` called from `migrate_db()` after
`migrate_not_duplicates(conn)`. Idempotent — re-running creates nothing new.

## Section 4 — DLC Review modal

### Entry point

In the Add Game modal's right-hand "Sync a whole library" column (where PS / Xbox
/ Nintendo / Steam scrape buttons live), one row above the vendor buttons:

```
DLC review (N)   [Open]
```

— hidden when N=0. `refreshScrapeSection` (`templates/base.html:1249`) gains a
parallel `GET /api/dlc/review/count` fetch to populate the badge on Add Game
modal open.

The post-scrape result panel's existing "Needs review" `<details>` section
(`templates/base.html:1223-1228`) keeps its inline list (it's helpful right after
a scrape) but its `<summary>` becomes a clickable link that calls
`openDlcReviewModal()` — same modal, no duplicated UI.

### Modal frame

New `#dlc-review-modal` next to `#dedup-modal` in `templates/base.html` (around
line 170). Same chrome (`max-w-3xl w-full max-h-[90vh] overflow-y-auto`), same
header pattern (title `"Resolve DLC review"` + `×` close button), body in the
same `space-y-6` rhythm.

### Per-item cards (not group cards)

Review items are independent — no analogue to dedup's "group of duplicate games"
clustering. Each item is its own card:

- **Header row** — full add-on title, vendor pill (`steam` / `nintendo` /
  `xbox`), reason badge (`no parent game` / `ambiguous parent` / `ambiguous dlc`).
- **Action row, varies by reason:**
  - **no parent game** — typeahead input `"Search games by title…"` posting to
    a tiny new `GET /api/games/search?q=<term>` (returns `[{id, title, cover_url,
    platforms[]}, ...]`, limit 10). Results render as game tiles. Click a tile →
    `POST /api/dlc/review/<id>/resolve {game_id}`.
  - **ambiguous parent** — server pre-resolves the candidate parent game IDs and
    inlines them on the `GET /api/dlc/review` payload (no extra round-trip).
    Candidate game tiles render at the top of the action row; below them, the
    same typeahead labelled *"None of these — search instead"*. Click a tile or
    a typeahead result → `POST /api/dlc/review/<id>/resolve {game_id}`.
  - **ambiguous dlc** — server pre-resolves the candidate DLC IDs (the same
    equality-tied rows the engine flagged) and inlines them with names.
    Candidate DLC tiles render. Click → `POST /api/dlc/review/<id>/resolve
    {dlc_id}`. Plus a small text-link *"None of these — create a new DLC row
    instead"* → `POST /api/dlc/review/<id>/resolve {create_new_dlc: true}`.
- **"Dismiss" button** on the right of every card → `POST
  /api/dlc/review/<id>/dismiss`. Card removes from the list on success; the
  modal's header count decrements.

### New endpoints in `app.py`

```
GET  /api/dlc/review/count            -> {count: N}
GET  /api/dlc/review                  -> {items: [...], count: N}
POST /api/dlc/review/<id>/resolve     -> {ok, marked, count}     body: {game_id?, dlc_id?, create_new_dlc?}
POST /api/dlc/review/<id>/dismiss     -> {ok, count}
GET  /api/games/search?q=<term>       -> [{id, title, cover_url, platforms[]}, ...]   limit 10
```

`GET /api/dlc/review` payload per item:

```json
{
  "id": 7,
  "addon_title": "...",
  "source": "nintendo",
  "external_id": "70050000000003",
  "source_title": "...",
  "reason": "ambiguous parent",
  "game_id": null,
  "candidates": {
    "games": [{"id": 12, "title": "...", "cover_url": "...", "platforms": ["Switch"]}, ...],
    "dlc":   [{"id": 34, "name": "Hearts of Stone"}, ...]
  }
}
```

The server resolves candidates per row by re-running the engine's `parent_of` /
`match_equal` helpers against the **current** library — so candidates reflect any
changes since the scrape (e.g. a merge that disambiguated a parent).

### New module `dlc_review.py`

```python
def resolve(conn: sqlite3.Connection, review_id: int, *,
            picked_game_id: int | None = None,
            picked_dlc_id: int | None = None,
            create_new_dlc: bool = False) -> dlc_ownership.Match:
    """Apply one user-resolved review item.

    Reads the review row, builds a synthetic addon dict (title / source /
    external_id / source_title), forces a parent (and optionally a target dlc
    row), and calls dlc_ownership._apply_addon_to_parent. On success marks
    resolved_at and returns the same Match the engine would have produced.

    Idempotent: resolving an already-resolved row returns the existing Match
    without re-writing. A picked target that no longer exists raises a
    ValueError that the route converts to 404.
    """
```

Exactly one of `picked_game_id` / `picked_dlc_id` / `create_new_dlc=True` is set
per call (the route validates).

### Refactor sliver in `dlc_ownership.py`

Extract the existing per-addon inner block (reconcile-by-id → reconcile-by-name
→ create) from `mark_ownership` into:

```python
def _apply_addon_to_parent(
    conn: sqlite3.Connection,
    report: OwnershipReport,
    parent: int,
    parent_norm: str,
    titles: dict[int, str],
    addon,
    *,
    dry_run: bool,
    forced_dlc_id: int | None = None,
    force_create: bool = False,
) -> None:
    """Reconcile/create one add-on against a known parent.

    Pure factoring of the inner block of mark_ownership; adds the forced_dlc_id /
    force_create handles used by dlc_review.resolve to land a user-picked
    decision (overriding the prefix heuristic when the user disagrees with it).
    """
```

`mark_ownership` calls it as today (after `parent_of`); `dlc_review.resolve`
calls it with the user's choice. **No behavior change for the scrape path** —
same `Match` shapes, same report counts. Existing `tests/test_dlc_ownership.py`
stays green.

### Defensive cases the resolve path handles

- The picked game / DLC was deleted between the modal load and the click → 404
  with `{error: "Picked target no longer exists"}`; modal re-fetches
  `GET /api/dlc/review` and re-renders.
- A re-scrape upserted the same vendor-id row while the modal was open and the
  user resolves an older view → UPSERT only refreshes reason/title;
  `resolved_at` is checked at resolve-time and a no-op response is returned if
  it's already set (idempotent).
- For "no parent game" / "ambiguous parent" with a picked `game_id`,
  `_apply_addon_to_parent` is called with `parent=<picked>` and the picked game's
  `normalized_title` — **the user's pick overrides the prefix heuristic** even if
  the picked game's title doesn't actually prefix the add-on title. The whole
  point of the modal is to let the user override the heuristic.

## Section 5 — Manual-add DLC enrichment wiring

The Add Game submit (`addNewGame`) grows three new steps, but every new step
calls an existing endpoint or the new `/api/games/<id>/steam` from Section 1 — no
new combined endpoint.

```
addNewGame()
  ├─ validate title; gather title, platforms, source_link?, cover_url?
  ├─ if source_link non-empty AND matches neither IGDB nor Steam regex:
  │     show inline error "Only IGDB or Steam URLs are accepted here"; abort  # no ghost game
  ├─ POST /api/games {title, platforms, cover_url?}                            # always (after validation)
  │   → game_id
  ├─ if source_link matches IGDB URL:
  │     POST /api/games/<game_id>/igdb {url: source_link}                      # sync; ~1-2s
  │     (on 4xx: surface inline error; game is created without IGDB enrichment)
  ├─ elif source_link matches Steam URL:
  │     POST /api/games/<game_id>/steam {url: source_link}                     # fast; appid only
  │     (on 4xx: surface inline error; game is created without an appid)
  └─ closeAddGameModal(); openModal(game_id); refreshGameList(); loadNavStats()
```

Sequential POSTs (not a combined endpoint) keeps the existing routes untouched;
the IGDB pin already commits within its own request, so a network failure on the
second POST doesn't roll back the game create — it just leaves the user with a
clear error toast, matching today's per-game-modal pin failure UX.

`POST /api/games` (`app.py:179`) is **not** modified to trigger enrichment by
itself — the Add Game submit handler owns the orchestration. This keeps the
endpoint's contract simple (it just inserts a row) and avoids surprising any
scrapers/CLI that also POST to it.

## Migration

Single new migration:

```python
def migrate_dlc_review_queue(conn: sqlite3.Connection) -> None:
    """Create dlc_review_queue + indexes if missing. Idempotent."""
```

Called from `migrate_db()` in `models.py` after `migrate_not_duplicates(conn)`.
Pure DDL; existing tables and data are untouched. Re-running `migrate_db` is a
no-op.

## Testing (TDD, offline)

All tests run with `uv run python -m pytest` in pytest temp DBs; no live DB, no
network. IGDB and Steam endpoints are mocked.

| File | What it covers |
|---|---|
| `tests/test_dlc_review_queue_migration.py` | `migrate_dlc_review_queue` creates the table + partial unique index; idempotent re-migration; the partial unique honors `WHERE source IS NOT NULL AND external_id IS NOT NULL`; null source/ext rows can coexist |
| `tests/test_dlc_review_persistence.py` | `mark_ownership` writes one row per review item; UPSERT by `(source, external_id)` on re-run (no dupes); a resolved/dismissed row stays so on re-scrape (timestamps preserved); the engine's existing `report.review` shape is unchanged |
| `tests/test_dlc_ownership_refactor.py` (or fold into the existing `tests/test_dlc_ownership.py`) | `_apply_addon_to_parent` reconciles-by-id → by-name → creates against a forced parent; `forced_dlc_id` flips that specific row; `force_create` skips reconcile and inserts; existing `mark_ownership` tests stay green |
| `tests/test_dlc_review_resolve.py` | `dlc_review.resolve` with picked `game_id` resolves a "no parent" item and marks `resolved_at`; with picked `dlc_id` reconciles that DLC + records ext_id; with `create_new_dlc=True` creates; resolving an already-resolved item is a no-op (idempotent); deleted target → ValueError → 404 from the route |
| `tests/test_app_dlc_review.py` | `GET /api/dlc/review` shape (includes candidates from re-running parent_of / match_equal against current library); `GET /api/dlc/review/count`; `POST /resolve` happy path + 404 paths; `POST /dismiss`; idempotent re-resolve |
| `tests/test_app_pin_steam.py` | `POST /api/games/<id>/steam` happy path writes `game_external_ids(source='steam', ...)`; rejects non-Steam URLs (400); idempotent re-pin (UNIQUE OR IGNORE); `steam_dlc.appid_from_steam_url` unit cases (`/app/123/`, `/app/123/Some_Name/`, querystring, http→https, malformed) |
| `tests/test_api_games_search.py` | `GET /api/games/search?q=` returns the expected shape; case-insensitive prefix + substring match; limit 10 |
| `tests/test_api_games_post.py` (extend existing or new) | `POST /api/games` no longer triggers any enrichment by itself (covered by an integration test that mocks IGDB + Steam and checks the two-call sequence from the front-end's perspective) |

Existing `tests/test_dlc_ownership.py` and `tests/test_api_games.py` stay green
otherwise — the refactor preserves the `mark_ownership` external contract.

## Constraints

- Public repo: never commit `games.db`/`games.db.bak*`, `.recon/`, `scraped/`,
  `.pw-profile/`, `config.json`, `.igdb_token.json`, `.steam_cache/`,
  `excluded_games.json` (all gitignored already).
- Conventional commits, **no co-author trailer**. Work directly on `main` — no
  feature branches or PRs unless explicitly asked.
- Run tests with `uv run python -m pytest`; lint gate is `uv run ruff check`
  **only** (the repo is hand-aligned; never run `ruff format`).
- Engine + endpoints are pure / temp-DB testable; the full SP3 suite runs
  offline.
- App runs with `use_reloader=False` (see project memory:
  `scrape-app-no-reloader`); don't churn `.py` files while it runs.
- Impl/review subagents must stay on pytest temp-DB + static review — never the
  running app, live scrapers, or real `games.db`.
- Back up `games.db` before any live ownership run (the pipeline already does
  this via `scrape_service.backup_db`).
