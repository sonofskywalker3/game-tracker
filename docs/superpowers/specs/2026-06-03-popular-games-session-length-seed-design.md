# SP-B — Seed `session_length` for top-N popular games (design)

**Date:** 2026-06-03
**Status:** Approved (owner), pending plan
**Sub-project of:** Generic catalog buildout (see "Decomposition" below)

## Problem

The three reference catalogs (`bundle_catalog.default.json`,
`series_catalog.default.json`, `game_traits.default.json`) currently only cover
titles in the owner's library. The Slate system is meant to work for **any**
user ([[slate-system-must-be-generic]]) — someone importing a 3,000-game library
should get bundle-splitting, series grouping, and session-length filtering
working out of the box, ideally **without an AI in the loop at add-time**.

The system should either already **know** (local curated catalog) or be able to
**find out** (online lookup on a miss), with results flowing back to a shared
catalog so everyone benefits (local-first, online-fallback, crowdsourced).

The three catalogs are *not* the same kind of problem:

| Catalog | Source of truth | AI needed? |
|---|---|---|
| **Series** | Objective — IGDB collections | No |
| **Bundles** | Objective — IGDB/Steam bundle relationships | No |
| **`session_length`** | **Subjective** — no online source; not derivable from play-time (Phoenix Wright is a long game with short sessions) | Only to seed / opt-in |

`session_length` is the outlier: it is the one catalog that genuinely needed an
AI to seed and cannot be looked up online for free.

## Decomposition (parent initiative)

The full "generic catalog" goal is four separable sub-projects, each with its own
spec → plan → build:

- **SP-A — Online auto-resolution of series & bundles at import.** Local catalog
  → IGDB fallback on miss → cache the result. One code path serves both
  build-time seeding and runtime fallback. *(First task: verify IGDB exposes
  bundle constituents + collections.)*
- **SP-B — Seed the shipped `session_length` defaults for ~top-3000 popular
  games. ← THIS SPEC.**
- **SP-C — Opt-in AI trait fallback + post-import prompt** ("Found N games
  without session length — run AI?"), using the user's Anthropic key, reusing
  `decider.py`.
- **SP-D — Crowdsourcing contribution flow** (how local additions flow back to
  the shared upstream catalog). Fuzziest; deferred.

Build order: **B → A → C → D.** B is the quick, low-risk, high-value win and
grounds the data shape A and C build on.

## Goal (SP-B)

Grow the shipped `game_traits.default.json` from its current **735** entries
(owner-library-derived) to **~3,000** popular games, so a user importing a large
library gets mostly-populated `session_length` immediately. Shared default data;
a user's gitignored `game_traits.json` override always wins.

## Design

### 1. Source list — IGDB

Query the existing IGDB v4 `/games` endpoint (Client-ID + Twitch bearer, already
wired in `igdb_dlc.py`/`fetch_covers.py`) with Apicalypse:

```
fields name, genres.name, summary, first_release_date, total_rating_count;
where category = 0 & total_rating_count != null;   // main games only (no DLC/bundle/expansion)
sort total_rating_count desc;
limit 500; offset <0, 500, 1000, ...>;             // paginate to N
```

- **N = 3,000 is a dial**, not load-bearing. A first tranche can ship and be
  extended later.
- `category = 0` excludes DLC / bundles / expansions (those are SP-A's concern).
- `total_rating_count` is the popularity proxy (number of ratings ≈
  recognizability).
- Genre + summary are pulled to use as **classification context** (a game's
  genre/summary makes the short-vs-long-session call far more accurate than the
  title alone).

### 2. Identity / dedup

- Key everything by `normalized_title` (the project's canonical join key).
- **Drop** any title whose `normalized_title` already exists in
  `game_traits.default.json` — never reclassify or overwrite the 735 existing
  reviewed entries.
- On `normalized_title` collision between two IGDB games, keep the more popular
  one (higher `total_rating_count`); log the collision.

### 3. Classification — Claude Code

A **controller-run Workflow** (free on the owner's subscription):

- The game list (name + genre + summary + release year + `normalized_title`) is
  **embedded as a const literal in the script body, never passed via Workflow
  `args`** (the 1.8M-token char-fragment lesson). Guard `Array.isArray`.
- ~20 games per agent → each returns
  `{normalized_title, session_length: short|long|unknown, confidence, reason}`.
- Classification rule (embedded in the prompt, same as Spec C): classify by
  **session structure / natural stopping points, NOT total play-time**. A game
  with frequent natural stopping points (visual novels, puzzle games, roguelite
  runs, turn-based tactics) is `short` even if the full game is long
  (Phoenix Wright / Danganronpa = `short`). A game that rewards long uninterrupted
  sessions (immersive RPGs, sprawling open worlds) is `long`. `unknown` is an
  allowed abstention.
- Matched back by `normalized_title` (never the returned id — the
  Bloodstained-transposition lesson).

### 4. Review — pilot + spot-check

- A **~25-game genre-spread pilot** first (deliberately spanning VN, RPG,
  roguelike, puzzle, action, tactics, etc.) so the owner calibrates the
  classifier and the embedded instructions can be tuned before the full run.
- Full run (~150 batches). The owner skims only the **flag list = every `long`
  call + every low-confidence call**. High-confidence `short` rides free.
  `unknown` is omitted from the catalog (stays null / neutral in ranking).
- Rationale: a wrong `long` banishes a game from Quick slots (asymmetric cost),
  so `long` and low-confidence calls get eyes; the stakes per game are lower than
  for the owner's own library (a wrong trait only affects users who own that
  game, and they can override + crowdsource-correct).

### 5. Merge / output

- Accepted entries are added to `game_traits.default.json` keyed by
  `normalized_title`, written **sorted, minimal-diff** via
  `json.dumps(d, sort_keys=True, indent=2, ensure_ascii=False)` preserving the
  original trailing newline (validated to round-trip the file byte-for-byte).
- Commit the expanded catalog.
- **No live-DB write.** These are mostly games the owner does not own;
  `migrate_db` auto-applies `apply_traits_catalog` on startup to any entries that
  happen to match a real library (including the owner's) harmlessly and
  idempotently.

## Out of scope (other sub-projects / later)

- Series & bundle IGDB resolution — **SP-A**.
- Runtime "Found N games without session length — run AI?" popup + opt-in
  per-user AI classification — **SP-C**.
- Crowdsource sharing of local additions back upstream — **SP-D**.
- Edition / regional-name / fuzzy identity matching beyond `normalized_title`
  (the long tail of user-library titles that won't match canonical IGDB names) —
  handled by SP-A/SP-C + crowdsourcing, not here.

## Alternatives considered

- **Heuristic from HLTB play-time + genre (no AI).** Rejected: play-time does not
  capture session structure (the whole point of the trait), so it would be wrong
  exactly where it matters most. AI-classify with genre/summary context is worth
  the free Claude Code run.
- **AI at add-time for every unknown.** Rejected as the *primary* mechanism
  (costs the user tokens on every import); it returns as the *opt-in* SP-C
  fallback for the long tail not covered by this seed.

## Risks

- **IGDB popularity tail quality.** Around rank ~3,000 games are still reasonably
  recognizable; acceptable. N is a dial if the tail degrades.
- **`normalized_title` mismatch with real libraries.** Canonical IGDB names won't
  match every user's edition/regional title. Out of scope here; SP-A/SP-C +
  crowdsourcing close the gap.
- **Classifier errors on obscure titles.** Mitigated by genre/summary context,
  the flag-list review, and per-user override.

## Success criteria

- `game_traits.default.json` grows to ~3,000 entries (735 preserved verbatim +
  ~2,250 new, minus abstentions/collisions).
- Pilot reviewed and instructions calibrated before the full run.
- Full-run flag list (all `long` + low-confidence) reviewed and approved by owner.
- Diff is clean/minimal-format; tests green; ruff clean; committed.
- No live-DB mutation required; no per-user AI cost incurred by the seed.
