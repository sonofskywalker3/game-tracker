# Library Scraping + Import — Design

**Date:** 2026-05-22
**Status:** Approved (pending written-spec review)
**Sub-project:** 2 of 2 (follows `2026-05-22-library-views-and-reskin-design.md`, which shipped the
five library views and the empty Legacy/PC scaffolding this work fills)

## Summary

Build Playwright-driven scrapers for the user's authenticated **PlayStation, Xbox, and Nintendo**
libraries and a dedicated importer that loads them into the Game Tracker SQLite database. Each
vendor scrape writes a normalized JSON file; a separate importer consumes those files, creating
per-console platform rows (so older titles populate the **Legacy** view) and deduplicating against
the existing 599 games **by stable vendor IDs** so renamed games are never re-added. The result is
a current local library: Modern fills out, Legacy populates wherever a vendor exposes legacy
purchases, and the database is in good shape to drive the (separately specced) PSPrices
status-reconciliation that follows.

## Goals

- Capture **everything owned** on the three vendors — including digital purchases *bought but never
  launched*, which trophy/achievement-based sources (PSPrices's own sync, psnawp, OpenXBL) miss.
- Load scraped games into the tracker so **Modern** fills out and **Legacy** populates from any
  legacy-console purchases the vendor sites expose.
- Make re-running safe and **rename-proof**: identity rides on stable vendor IDs, not on titles the
  user edits.
- Preserve the user's existing curation (status, rating, notes, series, physical tags) untouched.
- Keep scraping (reads a browser) and importing (writes the DB) **decoupled** so each is testable
  and runnable on its own.

## Non-goals (out of scope — deferred to the follow-up spec)

- **Pushing anything *into* PSPrices** (no API, no CSV import; would require automating the PSPrices
  website UI). The follow-up spec reconciles owned/wishlist status on PSPrices so price-drop alerts
  stop firing for already-owned games.
- **Physical-game sync to PSPrices.** Physical games are entered into the tracker manually (nothing
  to scrape); pushing them outward is the follow-up spec's concern.
- **Steam / PC scraping.** Not one of the three vendors. The **PC view stays empty** after this spec.
- Multi-user / accounts / hosting (todo #9). Remains a local single-user app.
- The Chrome extension bulk-add (todo #4) and Batch Add API (todo #1). The importer is a standalone
  module; a Batch Add API can reuse `classify_platform` and the match logic later when the extension
  actually needs it.

## Architecture

Three stages, decoupled by a JSON intermediate. The boundary is deliberate: **scraping never touches
the database; importing never opens a browser.**

```
scrape  (Playwright, reads vendor sites)  →  scraped/<vendor>_<date>.json  →  import  (writes games.db)
```

1. **Scrape** — a headed Playwright browser using a *persistent context*. The user logs in once per
   vendor (including 2FA); the session persists in a local profile directory, so later runs reuse it
   with no re-login. Each vendor scraper extracts a list of owned titles and writes a normalized
   JSON file.
2. **Normalized JSON** — the contract between the two halves. Human-readable, diffable, and the exact
   input the importer's tests run against.
3. **Import** — a CLI module reads the JSON files and reconciles them into `games.db` using the match
   cascade below, with `--dry-run` to preview every change first.

## Components / file structure

| File | Responsibility | Action |
|------|----------------|--------|
| `scrapers/__init__.py` | Package marker | Create |
| `scrapers/base.py` | `ScrapedGame` record; persistent-context lifecycle (headed login → session reuse); JSON writer; module-level path constants (`PROFILE_DIR`, `SCRAPE_DIR`) mirroring `models.DB_PATH` | Create |
| `scrapers/playstation.py` | Navigate `library.playstation.com`; extract `{title, external_id, platform, cover_url}`; map console label → canonical `short_name` | Create |
| `scrapers/xbox.py` | Same for the Microsoft/Xbox library/order history | Create |
| `scrapers/nintendo.py` | Same for the Nintendo account purchase history | Create |
| `scrape_libraries.py` | CLI: `--vendor playstation\|xbox\|nintendo\|all`; writes `scraped/<vendor>_<date>.json` | Create |
| `import_scraped.py` | CLI importer (sibling to `import_data.py`): JSON → `games.db`, `--dry-run` | Create |
| `models.py` | Add `game_external_ids` table to `init_db` schema + idempotent migration in `migrate_db` | Modify |
| `requirements.txt` | Add `playwright` | Modify |
| `.gitignore` | Add `.pw-profile/` and `scraped/` | Modify |
| `tests/fixtures/<vendor>_library_sample.html` | Saved sample pages for parse tests | Create |
| `tests/test_scraper_parse.py` | Per-vendor parse functions extract the right games from fixtures | Create |
| `tests/test_external_ids_migration.py` | Migration creates the table; idempotent | Create |
| `tests/test_import_scraped.py` | Match cascade, legacy row creation, curation preservation, idempotency | Create |

**Conventions:** run from project root. Tests: `python -m pytest`. New code uses the `logging`
module (not `print`), type hints on all signatures, and named constants — per `CLAUDE.md`. This repo
uses `requirements.txt` + `pip` (it predates `uv`).

## Scraping approach (auth)

- **Playwright for Python** (`playwright` in `requirements.txt`; one-time `playwright install chromium`).
- **Persistent context:** `chromium.launch_persistent_context(user_data_dir=PROFILE_DIR, headless=False)`.
  First run per vendor opens a real browser window; the user logs in and clears 2FA manually. Cookies
  and session live in `PROFILE_DIR` (gitignored), so subsequent runs scrape without a login prompt.
  This is the "log in once, then let it run" model the user asked for.
- **Why Playwright over vendor APIs:** the unofficial PSN/Xbox APIs and PSPrices's own sync are
  trophy/play-based and miss never-launched purchases — exactly the games this project targets. The
  authenticated library/purchase pages are the only complete source of *owned* titles, and Nintendo
  has no usable API.
- Each scraper exposes a **pure parse function** (`parse_<vendor>(html) -> list[ScrapedGame]`)
  separate from the browser navigation, so parsing is unit-testable against saved fixtures while the
  thin navigation/auth shell is verified manually.

## Normalized intermediate (JSON)

```json
{
  "source": "playstation",
  "scraped_at": "2026-05-22T15:00:00Z",
  "count": 205,
  "games": [
    {
      "title": "Hades",
      "source_title": "Hades",
      "external_id": "CUSA-12345",
      "platform": "PS5",
      "cover_url": "https://.../hades.png",
      "status_hint": null
    }
  ]
}
```

- `source` ∈ `{playstation, xbox, nintendo}`.
- `external_id` is the vendor's stable identifier (PSN concept/product ID, Xbox Store ID, Nintendo
  nsuid/title ID). If a page does not expose one, the field is `null` and that game falls back to
  title matching (graceful degradation).
- `platform` is the **canonical `short_name`** the vendor label maps to (e.g. `PS3`, `Vita`, `X360`,
  `WiiU`, `3DS`, `PS5`, `Switch`, `Xbox`) — chosen so `classify_platform()` already understands it.
- `source_title` preserves the exact vendor name even after the user later renames the game.
- `status_hint` is captured for completeness but **not acted on in this spec** (vendor libraries
  rarely expose play status; it's reserved for the follow-up PSPrices work). The importer always
  defaults new games to `backlog`.

## Data model changes

One new table; no changes to existing tables.

```sql
CREATE TABLE IF NOT EXISTS game_external_ids (
    game_id     INTEGER NOT NULL,
    source      TEXT    NOT NULL,   -- 'playstation' | 'xbox' | 'nintendo'
    external_id TEXT    NOT NULL,   -- vendor stable id (concept/product/nsuid)
    source_title TEXT,              -- original title as the vendor named it
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source, external_id),
    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_game_external_ids_game ON game_external_ids(game_id);
```

- `PRIMARY KEY (source, external_id)` makes the "have I imported this exact vendor entry before?"
  lookup O(1) and globally unique per source.
- `game_id` is **not** unique: one game carries many external IDs — one per vendor it's owned on, and
  even per edition within a vendor (PS4 + PS5 product IDs both pointing at one game). This is the
  rename/double-buy answer: a game owned on PS *and* Xbox is one `games` row with two
  `game_external_ids` rows and two `game_platforms` links — appearing once in the library with both
  platform tags (so an accidental cross-platform double-buy is *visible*).
- Migration follows the existing `models.py` pattern: `CREATE TABLE IF NOT EXISTS` in both `init_db`
  and `migrate_db` (idempotent, like the `series` table precedent).

## Era / platform mapping

Each scraped game carries a canonical console `short_name`. The importer ensures a `platforms` row
exists for it, **creating it on the fly** with `category = classify_platform(short_name)` when new:

- `PS4`, `PS5`, `Switch`, `Xbox` → `modern_console` (reusing the existing coarse rows; PS4/PS5 stay
  split as they already are).
- `PS3`, `Vita`, `PSP`, `X360`, `OGXbox`, `WiiU`, `3DS`, … → `legacy_console` → these populate the
  **Legacy** view automatically.
- Unknown/undeterminable console → default to the vendor's modern platform (`classify_platform`
  already returns `modern_console` for unknown short_names).

Per-vendor **label → short_name** maps live as module-level immutable constants in each scraper
(e.g. `"PlayStation 3" → "PS3"`, `"Xbox 360" → "X360"`, `"Wii U" → "WiiU"`), with values restricted
to short_names `classify_platform` recognizes.

**Legacy coverage is best-effort.** `library.playstation.com` is PS4/PS5-centric (PS3/Vita purchase
history lives elsewhere and is messier); Xbox back-compat and Nintendo's 3DS/Wii U purchase history
are more reachable. How much Legacy actually populates is discovered during implementation
(Phase-0 research), not promised here.

## Identity & dedup — the match cascade

For each scraped game, the importer resolves identity in order; the **first** match wins:

1. **`(source, external_id)` exact** — same vendor entry seen in a prior run → **rename-proof**, even
   if the user renamed the game in the tracker. *Attach platform link if missing; done.*
2. **`normalized_title` exact** — first time we've seen this ID but the game already exists (typical
   for the first scrape, or the same game owned on a second vendor) → attach this `external_id` +
   platform link to the existing game.
3. **Fuzzy similarity** — `difflib.SequenceMatcher` ratio on normalized titles ≥
   `FUZZY_MATCH_THRESHOLD` (a module-level named constant in `import_scraped.py`, default `0.85`;
   stdlib, **deterministic — no AI**) →
   a *probable* rename/variant → **flagged in `--dry-run` for the user to confirm or reject**, never
   auto-merged.
4. **No match** → create a new game.

On any match in 1–3, the new `external_id` and platform link are attached, so identity only ever
**consolidates, never duplicates**. Cross-vendor unification (PSN "Hades" ↔ Xbox "Hades") relies on
steps 2–3 since the two namespaces share no ID; once confirmed, the game owns both IDs and never
splits again.

**First-scrape caveat:** the existing 599 games have no vendor IDs yet, so a game renamed *before*
today can only be matched by steps 2–3. The fuzzy review in `--dry-run` surfaces these; confirming a
match locks the vendor ID onto the existing game, making every future scrape exact (step 1).

## Importer behavior & safety

Given 599 curated games, the importer is conservative:

- **Never overwrites** existing `status`, `rating`, `notes`, `priority`, `series`, or `sort_order`.
  Re-encountered games only gain platform links / external IDs. New games default to `status='backlog'`.
- **Cover URL** filled only when the game currently has none (prefer scraped; IGDB backfill remains a
  separate existing tool).
- **Never touches the `Physical` tag** — digital scrapes are not physical.
- **Idempotent:** re-running the same JSON changes nothing.
- **`--dry-run`** prints the full planned diff (new games, new platform links, new platform rows to
  create, and fuzzy-match candidates with their similarity scores) and writes **nothing**.
- Emits a summary via `logging`: new games, platform links added, external IDs added, exact-ID
  matches, title matches, fuzzy candidates needing review, unchanged.

## Security (standing rule)

- Add `.pw-profile/` (browser profile holding login cookies) and `scraped/` (personal library JSON)
  to `.gitignore`. Verify they are ignored **before** any commit.
- Never log cookies, tokens, or session data; `logging` is for operational counts/status only.
- Run a security check (no secrets, no personal data staged) before any push — per the user's
  standing rule. `config.json`, `.igdb_token.json`, `games.db`, `*.csv`, `.env`, `.superpowers/`
  remain gitignored.

## Testing

- **Parse (unit):** saved HTML fixtures per vendor in `tests/fixtures/`; assert `parse_<vendor>`
  extracts the expected titles, external IDs, and canonical platforms. No live browser.
- **Migration (unit):** `game_external_ids` is created and the migration is idempotent (re-run is a
  no-op), mirroring the existing migration tests.
- **Import (unit, temp DB via `conftest.py`):**
  - New game lands with the correct platform + category.
  - A legacy console (`PS3`/`X360`/`WiiU`/`3DS`) creates a `legacy_console` platform row.
  - Re-import by the same `(source, external_id)` is a no-op **even after the game is renamed**
    (the core rename-proof assertion).
  - Cross-vendor same-title scrape unifies into one game with two external IDs + two platform links.
  - Existing `status`/`rating`/`notes` are preserved across import.
  - Idempotency: importing the same file twice changes nothing.
  - Fuzzy candidates are flagged, not auto-added, in non-interactive/dry-run mode.
- **Live scrape:** the Playwright navigation/auth shell is verified manually (log in once, scrape,
  inspect the JSON). Selectors and ID availability are pinned during Phase-0 research.

## Phase-0 research (first step of the implementation plan)

Before building scrapers, confirm against the live logged-in sites and **save the HTML fixtures**:

- The exact library/purchase URL per vendor and whether it paginates / lazy-loads.
- Whether a **stable ID** is present in the DOM for each game (and where), per vendor.
- How much **legacy** purchase history each site actually exposes.
- The console label text each site uses (to build the label → short_name maps).

## Open questions

None blocking. The fuzzy threshold, exact selectors, and how much legacy each vendor exposes are
settled during Phase-0 research and tuned during implementation.
