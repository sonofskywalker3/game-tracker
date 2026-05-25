# DLC scrape-driven ownership (Piece 3) — design

**Date:** 2026-05-25
**Branch:** main (work directly on main, per repo workflow)
**Status:** approach approved; ready for implementation plan

## Goal

When a vendor library is scraped, owned **add-ons** (DLC / expansions / upgrade
packs) are captured instead of dropped, then matched to the IGDB-sourced rows
already in the `dlc` table, flipping `owned = 1` on the ones the user owns.

**Only existing DLC rows are flipped.** An owned add-on that matches no IGDB DLC
row under its parent game is *reported for manual handling*, never inserted — the
DLC list stays strictly IGDB-curated. This is **Piece 3** of the DLC feature;
Pieces 1+2 (data model + tab UI + IGDB auto-populate) are already on `main`.

Follows the project principle of durable, general pipeline logic plus an
idempotent pass, never one-time DB patches (`cleanup-fixes-must-be-general`), and
the matching discipline of `canonical-rename-equality-only` (equality for the
risky step; ambiguous matches held for review, never auto-applied).

## Background (verified facts)

- DLC is dropped at **scrape time**, not import time, for two vendors:
  - **Nintendo** `parse_orders` keeps only base-game/bundle NSUIDs via
    `is_game_nsuid`; `7005` (add-on) NSUIDs are skipped before the JSON is written
    (`scrapers/nintendo.py:59-105`). So today add-ons never reach the scrape file.
  - **Xbox** `parse_orders` keeps only `itemTypeName == "Game"`; `"Durable"` /
    `"Consumable"` add-on items are dropped (`scrapers/xbox.py:41-63`).
  - **PlayStation** `getPurchasedGameList` returns base games only; add-ons are
    **not** in that operation's payload (`scrapers/playstation.py`,
    `tests/fixtures/playstation_purchased_sample.json`). Capturing PSN add-ons
    needs a different GraphQL operation + persisted-query hash.
- **No vendor exposes a parent-game pointer for an add-on.** The only link is the
  add-on title, which embeds the parent name as a prefix, e.g. Xbox
  `"Sample Quest - Season Pass"` (fixture `9PDLC0001`, type `Durable`) and
  Nintendo `"Sample Game - Nintendo Switch 2 Edition Upgrade Pack"` (fixture
  NSUID `70050000000003`). Matching is therefore necessarily name-based.
- Existing scrape JSON files predate add-on capture, so there is nothing to
  backfill from — the feature takes effect on the next re-scrape.
- The `dlc` table already has the `owned` (0/1) column and
  `UNIQUE(game_id, name)` (`models.py`; DLC foundation spec
  `docs/superpowers/specs/2026-05-24-dlc-tracking-design.md`). IGDB enrichment
  runs after import via `import_scraped.run_dlc_enrichment` →
  `igdb_dlc.enrich_missing`.
- Reusable matching primitives exist: `models.match_key` /
  `models.normalize_title` / `models.clean_title` (with `KNOWN_EDITION_SUFFIXES`
  / `LEADING_TAGS` stripping) and `import_scraped.match_key`.

## Capturing add-ons in the scrapers

`ScrapedGame` gains a field:

```python
kind: str = "game"   # "game" | "addon"
```

Backward-compatible (defaults to `"game"`; `asdict`/JSON round-trips cleanly).
Add-ons ride in the existing `games` list, tagged; the import CLI partitions them
(see Integration). *Approaches considered:* a separate `addons` key in the scrape
payload — rejected as a wider schema/IO change for no gain over a tagged row.

- **Nintendo** — replace `is_game_nsuid` with
  `classify_nsuid(nsuid) -> str | None`: a 14-digit `700x` NSUID is `"addon"`
  when its 4-digit prefix is in `ADDON_NSUID_PREFIXES = {"7005"}`, else
  `"game"`; anything that is not a real 14-digit `700x` NSUID (short hardware /
  merch ids) returns `None` and is skipped, exactly as today. `parse_orders`
  emits `7005` items as `ScrapedGame(kind="addon", ...)`. The hardware/merch
  backstop is unchanged.
- **Xbox** — `parse_orders` keeps `itemTypeName == "Game"` as games and item
  types in `ADDON_ITEM_TYPES = {"Durable", "Consumable"}` as `kind="addon"`;
  `"Subscription"` (Game Pass etc.) and any other type are skipped as today.
- **PlayStation** — *recon-dependent, deferred within this spec.* PSN add-ons are
  not in `getPurchasedGameList`. This spec defines a new `collect_addons`
  operation + a pure `parse_addons`, but the operation name, persisted-query
  hash, and response shape must be captured from a fresh live recon
  (`.recon/playstation.responses.jsonl`) by the user; `parse_addons` is then
  written and unit-tested against a sanitized fixture. Nintendo and Xbox ship
  without this. Until the recon lands, PSN simply emits no add-ons (no
  regression).

## The matching engine — new module `dlc_ownership.py`

Pure, unit-tested helpers (no network, no live DB). A sentinel `AMBIGUOUS`
distinguishes "more than one plausible match" from "no match".

- `parent_of(addon_title, library) -> int | None | AMBIGUOUS` — normalize the
  add-on title with `match_key`, strip known edition/DLC suffixes, then find the
  library game whose normalized title is the **longest prefix** of the normalized
  add-on title. `library` is the list of `(game_id, normalized_title)`. No prefix
  match → `None`; a longest-prefix tie spanning different `game_id`s → `AMBIGUOUS`
  (several different-platform rows of the *same* game collapse to one id and are
  not ambiguous).
- `match_dlc(remainder, dlc_rows) -> int | None | AMBIGUOUS` — within the
  parent's DLC rows, match the add-on's remainder (add-on title with the parent
  prefix removed) by **normalized equality** first, then by containment. `rows`
  is `(dlc_id, name)`. Multiple equality or containment hits → `AMBIGUOUS`; none
  → `None`.
- `classify(addon, library, dlc_by_game) -> Match` where
  `Match = {action, game_id, dlc_id, addon_title, reason}` and
  `action ∈ {"apply", "hold", "unmatched"}`:
  - `"apply"` only when the parent resolves to exactly one id **and** the DLC
    matches by equality.
  - `"hold"` when either stage is `AMBIGUOUS`, or the DLC matched only by
    containment (not equality) — plausible but not certain.
  - `"unmatched"` when there is no parent, or the parent has no matching DLC row.

Orchestrator:

```python
def mark_ownership(conn, addons, *, dry_run=False, include_flagged=False) -> OwnershipReport
```

Loads the library + each candidate parent's DLC rows once, classifies every
add-on, then sets `dlc.owned = 1` for `action == "apply"` (and for `"hold"` when
`include_flagged`). The update is **only ever 0 → 1, never 1 → 0** — idempotent
and safe to re-run. Returns counts (`marked`, `already_owned`, `held`,
`unmatched`) plus the held/unmatched lists for reporting. Writes nothing when
`dry_run`.

**Ownership re-flip trade-off:** because matching only sets `owned = 1`, a DLC the
user manually un-checks but actually owns per the vendor is re-checked on the next
scrape. The vendor is treated as the source of truth for ownership. (Accepted;
revisit with a provenance column only if it proves annoying.)

## Integration & CLI (`import_scraped.py`)

DLC rows must exist before matching, so the order is:

1. `import_games` (rows with `kind == "game"`) — creates/links games as today.
2. `run_dlc_enrichment` — populates `dlc` for newly imported games (incremental).
3. `mark_ownership` (rows with `kind == "addon"`) — flips `owned` on matches.

The CLI partitions each scrape file's `games` list by `kind` (default `"game"`
for older files). New flags, mirroring the existing DLC flags:

- `--no-ownership` — skip step 3 (parallel to `--no-dlc`).
- `--apply-flagged-ownership` — also apply `hold` matches (this flag is new for
  ownership; named to avoid confusion with the bundle path's unrelated
  `--include-curated`).
- `--dry-run` (existing) — steps 1–3 all preview; `mark_ownership` writes nothing.
  Note: under `--dry-run` the DLC-enrichment step is skipped, so the ownership
  preview omits not-yet-imported games (it still previews matches against DLC
  rows already in the DB); `main` logs a note to that effect.

`_log_summary` gains an ownership block: `owned marked / already owned / held /
unmatched` counts, plus a held-and-unmatched listing in the style of the existing
`FUZZY — needs your review` block, so the user can eyeball what did not
auto-apply and fix it manually in the DLC tab.

No standalone subcommand: re-running is just a re-import of the latest scrape
files (idempotent). There is no backfill from pre-add-on JSON.

## Data model

**No schema change.** The feature only executes
`UPDATE dlc SET owned = 1 WHERE id = ?` on existing rows. No provenance column is
added (minimal, per the "only flip existing rows" decision). The `ScrapedGame`
`kind` field is a scrape-payload addition, not a DB column.

## Testing (TDD, offline)

Pure / unit (no network, no live DB):
- `parent_of`: exact-prefix match; longest-prefix wins over a shorter one;
  different-platform rows of one game are not ambiguous; cross-game tie →
  `AMBIGUOUS`; no prefix → `None`.
- `match_dlc`: equality match; containment-only match; multiple hits →
  `AMBIGUOUS`; none → `None`.
- `classify`: apply (unique parent + equality), hold (ambiguous parent / DLC, or
  containment-only), unmatched (no parent, or parent with no DLC row).
- `mark_ownership` (temp DB): applies `apply`, holds `hold` unless
  `include_flagged`, reports `unmatched`, is 0→1-only, idempotent on re-run, and
  writes nothing under `dry_run`.

Scraper parse (existing fixtures already carry add-on rows):
- Nintendo `parse_orders`: NSUID `70050000000003` now surfaces as
  `kind="addon"`; base/bundle NSUIDs stay `kind="game"`; short hardware id
  `120833` still skipped.
- Xbox `parse_orders`: `Durable` `"Sample Quest - Season Pass"` now surfaces as
  `kind="addon"`; `Game` items stay `kind="game"`; `Subscription` still skipped.

Integration (temp DB, IGDB fetch mocked):
- Import a game → enrich DLC → feed a matching owned add-on → its `dlc.owned`
  flips to 1; an ambiguous add-on is held (not flipped) unless
  `--apply-flagged-ownership`;
  an add-on with no IGDB DLC row is reported `unmatched` and creates nothing;
  `--dry-run` writes nothing; a second identical run changes nothing.

Existing suite stays green; this adds the new ownership + scraper-kind tests.

## Out of scope (later)

- **PSN add-on scraper** beyond the `parse_addons` scaffold — blocked on a live
  recon capture; lands when the user provides a sanitized PSN add-on fixture.
- **IGDB-id bridge** (resolve each add-on to its own IGDB id, join on
  `dlc.igdb_id`) — a possible future confidence booster; name-matching first.
- **Piece 4** — edition→included-content auto-check.
- **GUI "scrape now" button.**

## Constraints

- Public repo: never commit `games.db`, `games.db.bak*`, `.recon/`, `scraped/`,
  `.pw-profile/`, `config.json`, `.igdb_token.json`, `excluded_games.json`
  (gitignored).
- Conventional commits, no co-author trailer. Work on `main`.
- Matching/orchestration is pure + temp-DB testable; the suite runs offline. Back
  up `games.db` before any live ownership run.
