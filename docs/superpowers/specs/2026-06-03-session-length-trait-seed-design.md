# Spec C — `session_length` Trait Seed

**Date:** 2026-06-03
**Status:** Approved (brainstorming)
**Arc:** Spec A (bundle/compilation expansion — landed+applied) → Spec B (catalog-driven
series defaulting — landed+applied+pushed) → **Spec C (this — the trait seed).**

## Goal

Populate `games.session_length` (`short`/`long`) across the cleaned, series-assigned
764-game library so the Slate's Quick/Long slot axis has real signal. This mirrors the
Spec A/B controller-writes pattern: an AI-draft classification → owner review → committed
catalog → backup → apply to the live DB.

**This is a seed operation, not new infrastructure.** The mechanism already exists and is
wired into `migrate_db` + on-add:

- `models.load_game_traits()` — loads `game_traits.json` (gitignored per-user override) or
  the committed `game_traits.default.json`.
- `models.TRAIT_FIELDS = ("session_length",)` — `series_role` is owned by the series
  catalog now, so traits are session_length-only.
- `models.apply_traits_catalog(conn, game_id=None)` — writes the catalog value (keyed by
  `normalized_title`) to `games.session_length` with `session_length_source='catalog'`,
  skips rows whose source is `manual` (LOCKED), and no-ops on a missing catalog/entry.
  Runs for every game at startup (`migrate_db`) and for one game on add.
- `game_traits.default.json` — committed, currently empty (`{}`).

Live state confirmed 2026-06-03: 764 games, `session_length` set on **0**, `manual` on
**0**. So the seed starts from a clean slate with nothing to override.

## Definition (approved, verbatim)

`session_length` keys off **session tolerance / natural stopping points**, *independent of
total play-time*:

- `short` — clean short stopping points (levels, chapters, battles, cases). Works in ~1
  hour. (e.g. Final Fantasy Tactics, Phoenix Wright are *long games* but `short` session.)
- `long` — needs a dedicated block; open-world grind, few natural breaks.
- `null` — unknown / the AI abstained.

In ranking (already implemented in `slots.py`): a Quick slot (`max_session_minutes` set)
**excludes** `long` and **boosts** `short`; a Long slot (`min_session_minutes` set) boosts
`long`; `null` is neutral (allowed everywhere, boosted nowhere). A wrong `long` is the
costliest error — it banishes a game from Quick entirely — which drives the abstain policy
and the review gate below.

## Components

### 1. Classifier Workflow (controller-run, throwaway script)

A `Workflow`-tool script the controller runs. Hard-won constraints from Spec A/B:

- **Titles embedded as a `const` literal in the script body — NEVER via the `args`
  channel.** Passing an array via `args` arrives as a string and `.slice`/`.map` shred it
  into hundreds of char-fragment agents (cost ~1.8M tokens once). Guard `Array.isArray(...)`
  and `length` before fanning out.
- Batched **~20 titles/agent**, `parallel()` over batches (~38 batches for 764), each agent
  forced to structured output via the `schema` option.
- **Match verdicts back by `normalized_title`, never a returned `id`** — the AI transposes
  ids.

**Per-title input to each agent:** `{normalized_title, platform, tags?}`. Genre/theme tags
are passed **only for the ~19 games that have them** (the `game_tags` table covers just
19/764 — see Notes); everyone else is title-only. The structural reasoning comes from the
AI's world-knowledge of each game, steered by the prompt — not from our (near-empty) tag
columns. HLTB / time-to-beat is **withheld** (it is the play-time red herring the
definition bans).

**Prompt requirements:** (a) define `session_length` by structure / natural stopping
points; (b) explicitly forbid conflating "long game" (total hours) with "long session";
(c) permit **abstain → `null`** when genuinely unsure (an unknown game stays neutral rather
than risking a wrong `long`).

**Per-title output:**
```json
{ "normalized_title": "...", "session_length": "short" | "long" | null,
  "confidence": "high" | "low", "reason": "..." }
```
`confidence` and `reason` are for the **owner's review only** — they are NOT persisted to
the catalog.

### 2. Pilot (~20 titles)

Controller selects ~20 titles spanning genres + a few obscure / broken-out constituent
titles (to exercise the abstain path). Owner reviews label quality and the structural
reasoning before any full run.

### 3. Full run (~38 batches) + flag list

After the owner approves the pilot, the controller runs the full library and assembles a
**flag list** for owner spot-check:

- **every `long` call** (the costly, exclude-from-Quick decisions),
- **every abstention** (`null`),
- **every `low`-confidence call**.

The owner reviews/corrects the flag list; the remaining `high`-confidence `short` calls
ride on pilot-validated quality.

### 4. Catalog

Controller writes `game_traits.default.json`:
```json
{ "<normalized_title>": { "session_length": "short" | "long" } }
```
Nulls/abstentions are **omitted** (absent entry = neutral `null` in the DB). `confidence`
and `reason` are dropped. Commit the catalog.

### 5. Apply + verify

- **Backup** `games.db` (e.g. `games.db.bak-20260603-pre-traits-seed-apply`).
- Apply via `apply_traits_catalog` — runs automatically on the next `migrate_db`
  (startup/CLI), writing `session_length` with `source='catalog'`, skipping the 0 manual
  locks.
- Controller **UI-verifies live**: start the app, confirm a Quick slot excludes `long`
  games and boosts `short`, a Long slot boosts `long`; then **stop the app**.

## Data flow

```
AI verdicts (normalized_title → label, confidence, reason)
  → owner pilot review → full run → owner flag-list review/correct
  → game_traits.default.json (committed; session_length only, nulls omitted)
  → apply_traits_catalog  (writes games.session_length, source='catalog';
                           skips source='manual'; missing entry = no-op)
  → slots.py ranking already consumes games.session_length
```

## Error handling / safety

- **Subagents never touch the live `games.db` or run the app.** They return verdict data
  only. The **controller** performs all live writes and the app run, and backs up the DB
  first. (See [[subagent-impl-never-touch-live-db]].)
- Impl/seed subagents are told: do **NOT** `git push`.
- **`--dry-run` wart:** `migrate_db` auto-applies the catalog on every startup/CLI run, so
  a `--dry-run` preview is undercut (migrate applies for real first). The real preview is
  the **AI verdict list**; the `games.db` backup is the safety net.
- Catalog file missing/malformed → `load_game_traits()` returns `{}` → `apply_traits_catalog`
  no-ops. No crash.
- A `manual` lock always wins (skipped by apply); users override per-game in the UI, which
  stamps `source='manual'`.

## Testing

The apply path is already exercised by the existing suite (504 tests green). Net new code
is expected to be near-zero. During planning, confirm there is a focused test that
`apply_traits_catalog`:

1. writes `session_length` + `session_length_source='catalog'` from a populated catalog,
2. **skips** a row whose `session_length_source='manual'`,
3. no-ops on a title absent from the catalog.

Add only the missing assertions (pytest temp DB — `uv run python -m pytest`).

## Genericity

This seeds **default** values only. The slot/trait system stays configurable for any user:
the per-user `game_traits.json` override and per-game manual UI edits both win over the
shipped default. The persona seeds sensible defaults; it never hardcodes behavior. (See
[[slate-system-must-be-generic]], [[cleanup-fixes-must-be-general]].)

## Out of scope (YAGNI)

- `series_role` seeding — owned by the series catalog (Spec B); `TRAIT_FIELDS` is
  session_length-only.
- HLTB / heuristic-based seeding — the axis is structure, not play-time.
- Real-time on-add AI classification (Phase B) — deferred; rides with the Settings-UI
  Anthropic-key work.
- Genre/tag backfill from IGDB — a separate enrichment effort; not a prerequisite.

## Notes

- Genre/tag coverage is sparse: `game_tags` has 19 rows across 764 games; `tags` has 27
  entries (17 genre, 8 theme, 2 custom). Only 19 distinct games carry any tag. Hence
  "append tags when present" is effectively title-driven classification.
- `normalized_title` is the join key everywhere (catalog key + verdict match key).
