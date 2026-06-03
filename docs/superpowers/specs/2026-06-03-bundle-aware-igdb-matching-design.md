# Bundle-aware IGDB identity matching + re-audit + disambiguation modal (design)

**Date:** 2026-06-03
**Status:** Approved (owner), pending plan
**Relates to:** generic-catalog buildout / SP-A IGDB resolution

## Problem

Bundle constituents broken out by the debundler (e.g. Mega Man 1–6 from "Mega Man
Legacy Collection") get the **wrong IGDB version** — usually a mobile port —
during cover/identity enrichment. Root cause: enrichment does a plain IGDB title
search and takes the first title-match (`fetch_covers.search_game`, `limit 5`,
fields `name, cover.url` only — **no platform data**), so a mobile port that
happens to sort first wins. The owner manually fixed several Mega Man covers; that
fix lives only in `games.db` and is not protected from re-enrichment, and the
matcher will keep mis-picking on future imports.

## Verified IGDB facts (probed live 2026-06-03)

- **`game_type == 3`** identifies a bundle (the older `category` field is
  deprecated and returns `None`).
- **The bundle→contents link is queryable via a REVERSE lookup:**
  `fields ...; where bundles = (<bundle_id>);` returns the constituent games.
  Confirmed: "Mega Man Legacy Collection 2" = IGDB id **28323** (platforms PS4 /
  PC / Xbox One / Switch); `where bundles = (28323)` → **Mega Man 7 (1720, SNES),
  Mega Man 8 (1721), Mega Man 9 (1722), Mega Man 10 (1723)** — the canonical
  entries, each with a cover. The bundle's *forward* fields do **not** include a
  contents list; the reverse `bundles` lookup is the mechanism.
- **Constituents already carry the signal needed.** `import_scraped._constituent_game`
  (import_scraped.py:316, "Inherits the bundle's platform") creates each
  broken-out game on the parent bundle's platform(s) and sets `collection_name`.
  Verified: every Mega Man constituent has `platforms=['Switch']` and
  `collection_name='Mega Man Legacy Collection …'`.
- IGDB platform IDs used: Switch 130, PS4 48, PS5 167, Xbox One 49,
  Xbox Series 169, PC (Windows) 6; **mobile = iOS 39, Android 34** (+ "Legacy
  Mobile Device" appears by name in data).

## Design

The system resolves IGDB **identity** (sets `igdb_id` **and** `cover_url`), not
just a cover, via one shared module used by enrichment, the audit, and the modal.

### 1. Shared module — `igdb_match.py` (new)

Consolidates matching logic currently split across `fetch_covers.py` and
`igdb_dlc.py`.

- **Platform map (module-level, frozen):** `IGDB_PLATFORM_IDS` mapping the app's
  `short_name`s → IGDB platform id(s); `MOBILE_PLATFORM_IDS = frozenset({39, 34})`.
- **`resolve_bundle(name, platform_ids, client_id, token) -> int | None`** —
  `search "<name>"; fields name, game_type, platforms; limit 10;`, keep
  `game_type == 3`, prefer the candidate whose platforms best overlap our bundle's
  platform(s) (handles "different platforms bundle different games" — picks the
  right edition), then exact normalized-name match. Returns the bundle's IGDB id
  or `None`.
- **`bundle_constituents(bundle_id, client_id, token) -> list[dict]`** —
  `fields name, cover.url, platforms, first_release_date; where bundles = (<id>);
  limit 50;`. Returns `[{igdb_id, name, normalized_title, cover_url, platforms}]`.
- **`fetch_candidates(title, client_id, token) -> list[dict]`** (fallback) —
  `search "<title>"; fields name, cover.url, platforms, first_release_date,
  total_rating_count, game_type; limit 10;`.
- **`score_candidates(cands, game_platform_ids) -> ranked list`** (fallback) —
  per candidate: title normalized-exact `+100` / containment `+40` (else drop);
  platform overlap `+50`; **mobile-only (platforms ⊆ mobile) `−80`**; has-cover
  `+10`; tiebreak by `total_rating_count` then earliest `first_release_date`.
  Exposes `best` + a `confident` flag (title-exact, not mobile-only, clear margin
  over #2). **No hard platform filter on the query** — a retro constituent's
  canonical entry (NES "Mega Man 2") isn't on the bundle's Switch platform, so a
  filter would discard the right answer; scoring keeps it and demotes the port.
- Cover URLs normalized to `t_cover_big` / `https:` as `fetch_covers` already does.

### 2. Resolution order (bundle-first)

For a game being resolved (enrichment, audit, or modal):

1. **Bundle constituent** (`collection_name` set, not locked): `resolve_bundle`
   the `collection_name` (with the game's platforms) → `bundle_constituents` →
   match an IGDB constituent to this game by `normalized_title` → that
   `igdb_id` + cover is the authoritative answer.
2. **Standalone, or a constituent the bundle lookup misses:** fall back to
   `fetch_candidates` + `score_candidates` (platform-aware, mobile-penalized).

`fetch_covers` and the `igdb_dlc` identity path both call this order so covers and
`igdb_id` agree. Locked games (below) are skipped.

### 3. Re-audit (flag-only) + lock

- **New column `games.igdb_locked` (INTEGER 0/1, default 0).** Set to 1 whenever
  the owner picks in the modal or uses the existing Source-link pin. Enrichment
  and the audit **skip locked games**.
- **`audit_igdb_matches(conn, *, client, ...)`** — for each non-locked game,
  run the bundle-first resolution; **flag** the game only when the authoritative/
  confident match's cover differs from the current `cover_url`, or the result is
  ambiguous/mobile-only/none. Games whose current cover already equals the
  resolved one are **not** flagged — so the owner's hand-fixed Mega Man covers do
  not resurface. The audit **never changes a game**; it only records flags.
- **Review queue:** a `games.needs_igdb_review` flag (INTEGER 0/1) set by the
  audit; surfaced as a "Needs review (N)" list the owner works through. (Storing
  candidate snapshots is unnecessary — the modal fetches candidates live.)

### 4. Disambiguation modal + endpoints

- **`GET /api/games/<id>/igdb-candidates`** → the bundle-derived canonical match
  first (if applicable), then scorer candidates: `[{igdb_id, name, cover_url,
  platforms, year, score, source: "bundle"|"search"}]`.
- **`POST /api/games/<id>/igdb-pick` `{igdb_id}`** → set `igdb_id` + `cover_url`
  from the chosen candidate, set `igdb_locked = 1`, clear `needs_igdb_review`.
  (Re-pulling DLC for the new identity is an optional follow-up, not required.)
- **UI:** a **"Wrong version? Fix match"** button in the game modal opens a
  candidate grid (cover thumb + name + **platforms** + year; click to apply). A
  **"Needs review (N)"** entry point lists audit-flagged games, each opening the
  same modal.

## Data model changes

- `games.igdb_locked INTEGER NOT NULL DEFAULT 0`
- `games.needs_igdb_review INTEGER NOT NULL DEFAULT 0`

Both added via a `migrate_*` step registered in `migrate_db` and mirrored in
`tests/conftest.py`.

## File structure

- **Create:** `igdb_match.py` — platform map, `resolve_bundle`,
  `bundle_constituents`, `fetch_candidates`, `score_candidates`. One
  responsibility: turn a game (title + platforms + bundle context) into a ranked
  set of IGDB identity candidates, bundle-first.
- **Create:** `tests/test_igdb_match.py` — scorer + bundle-resolution unit tests
  (IGDB query monkeypatched; no live calls).
- **Modify:** `fetch_covers.py` (`search_game` delegates to `igdb_match`),
  `igdb_dlc.py` (identity path delegates), `models.py` (migrations + audit helper
  or a new `igdb_audit.py`), `app.py` (two endpoints), `templates` (modal +
  review list).

## Out of scope (later / other sub-projects)

- Auto-fixing during the audit (owner chose flag-only).
- Caching resolved bundle/constituent IGDB ids back into the shared
  `bundle_catalog` (SP-A identity layer) — for now ids land per-game in `games.db`.
- Driving the debundler itself off the bundle reverse-lookup at break-out time.
- Re-pulling DLC on identity change; non-IGDB sources.

## Testing

- `score_candidates`: mobile-only demotion, platform-overlap boost, retro-canonical
  kept (NES entry beats mobile port for a Switch bundle constituent), tie-breaks.
- `resolve_bundle`: picks `game_type==3`, platform-preferred edition.
- `bundle_constituents` + bundle-first order: maps IGDB constituents to our games
  by `normalized_title`.
- `audit_igdb_matches`: flags only on disagreement; never mutates; skips locked;
  does not re-flag a game whose current cover already matches the resolved one.
- Endpoints on a temp DB with mocked IGDB. **No live IGDB in any test.**
- Controller (not subagents) runs any live IGDB verification and the real audit.

## Success criteria

- A bundle constituent resolves to IGDB's canonical entry (Mega Man 7 → 1720, not
  a mobile port) via the reverse `bundles` lookup.
- The audit flags genuinely-wrong matches without touching anything, and does not
  resurface the owner's already-correct hand-fixes.
- The modal lets the owner pick the right version, which locks the game.
- Tests green, ruff clean, no live IGDB in tests.
