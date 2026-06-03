# `session_length` Trait Seed Implementation Plan

> **For agentic workers:** This is a **controller-run runbook**, not a code-build plan.
> The trait infrastructure already exists and is fully tested (see Task 0). The controller
> executes these tasks directly in the main session. The ONLY work dispatched to subagents
> is the classifier Workflow — those agents **return verdict data only** and must NEVER
> touch `games.db`, run the app, or `git push`. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Populate `games.session_length` (`short`/`long`) across the 764-game library via
AI-draft classification → owner review → committed `game_traits.default.json` → backup →
apply, so the Slate's Quick/Long axis has real signal.

**Architecture:** No new app code. A throwaway `Workflow`-tool script classifies each title
by *session structure* (not play-time); the controller reviews with the owner, writes the
catalog, backs up `games.db`, and applies via the existing `apply_traits_catalog` (runs on
`migrate_db`). Matched by `normalized_title`, never a returned id.

**Tech Stack:** Python 3 / sqlite3 / Flask (existing); `Workflow` tool for classification;
`uv run python -m pytest` for tests; `ruff check` for lint.

**Spec:** `docs/superpowers/specs/2026-06-03-session-length-trait-seed-design.md`

---

## Task 0: Confirm infra + green baseline (verification only — NO code)

**Files:** none (read-only verification).

- [ ] **Step 1: Confirm the trait infra exists.**

Already verified 2026-06-03:
- `models.load_game_traits()`, `models.TRAIT_FIELDS == ("session_length",)`,
  `models.apply_traits_catalog(conn, game_id=None)` — present; wired into `migrate_db`
  (`apply_traits_catalog(conn)`) and on-add.
- `game_traits.default.json` committed and empty (`{}`).
- Live DB: 764 games, `session_length` set on 0, `manual` on 0.

- [ ] **Step 2: Confirm test coverage (the spec's 3 requirements are already met).**

`tests/test_game_traits_catalog.py` already covers: write + `source='catalog'`
(`test_apply_traits_catalog_sets_session_length_only`), manual-lock skip
(`test_apply_traits_catalog_skips_manual`), absent-entry no-op
(`test_apply_traits_catalog_absent_is_noop`), plus `game_id` scoping and all
`load_game_traits` load paths.

Run: `uv run python -m pytest tests/test_game_traits_catalog.py -q`
Expected: `10 passed`. **(Confirmed green 2026-06-03.)**

> **Result:** Net new code = zero. No TDD task. Proceed to the seed operation. If a future
> change to `apply_traits_catalog` is required, add a TDD task here first — but none is.

---

## Task 1: Extract the classification input set

**Files:**
- Create (throwaway, gitignored/temp): `_seed/titles.json` (intermediate; deleted at end).

- [ ] **Step 1: Pull normalized_title + platform + tags (read-only) from `games.db`.**

The controller runs this read-only extraction (NOT a subagent). Tags join is included only
for the ~19 games that have them; everyone else gets `tags: []`.

```python
# run via: uv run python _seed/extract_titles.py  (throwaway script, deleted at end)
import json, sqlite3
c = sqlite3.connect("games.db"); c.row_factory = sqlite3.Row
rows = c.execute("""
    SELECT g.id, g.normalized_title, g.title,
           COALESCE(GROUP_CONCAT(DISTINCT p.name), '')  AS platforms,
           COALESCE(GROUP_CONCAT(DISTINCT t.name), '')   AS tags
    FROM games g
    LEFT JOIN game_platforms gp ON gp.game_id = g.id
    LEFT JOIN platforms p       ON p.id = gp.platform_id
    LEFT JOIN game_tags gt      ON gt.game_id = g.id
    LEFT JOIN tags t            ON t.id = gt.tag_id
    GROUP BY g.id
    ORDER BY g.normalized_title
""").fetchall()
out = [{"normalized_title": r["normalized_title"], "title": r["title"],
        "platforms": [s for s in r["platforms"].split(",") if s],
        "tags": [s for s in r["tags"].split(",") if s]} for r in rows]
json.dump(out, open("_seed/titles.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0)
print(f"extracted {len(out)} titles; tagged: {sum(1 for o in out if o['tags'])}")
```

- [ ] **Step 2: Verify the extraction.**

Expected: `extracted 764 titles; tagged: 19`. Confirm the JSON is an array of objects with
`normalized_title` populated. This `titles.json` is the source the Workflow script embeds as
a `const` — it is NEVER passed via the Workflow `args` channel.

---

## Task 2: Build the classifier Workflow script + run the PILOT (~20 titles)

**Files:**
- Create (throwaway): the Workflow script (persisted by the Workflow tool under the session
  dir; not committed).

- [ ] **Step 1: Author the Workflow script.**

Requirements baked in:
- **Titles embedded as a `const` literal** in the script body (paste ~20 pilot objects from
  `titles.json`). Guard `if (!Array.isArray(TITLES) || !TITLES.length) throw ...`.
- Batch ~20/agent; `parallel()` over batches; `schema` option forces structured output.
- Prompt (per agent): define `session_length` by **session structure / natural stopping
  points** (levels, chapters, battles, cases = `short`; open-world grind / few breaks =
  `long`); **explicitly forbid** conflating total play-time/length with session length;
  pass each title's `tags` when present; permit **abstain → `null`** when unsure.
- Output schema per title: `{normalized_title, session_length: "short"|"long"|null,
  confidence: "high"|"low", reason}`.
- The script `return`s the flat verdict array.

Pilot composition (~20): a deliberate spread — a couple of open-world RPGs (expect `long`),
case/level/chapter games like Phoenix Wright / a Mega Man constituent (expect `short`
despite varying total length), a roguelike, a couple of obscure/broken-out constituents
(to exercise abstain), and a long-but-episodic title (the FF Tactics-style trap case).

- [ ] **Step 2: Run the pilot Workflow.**

Controller invokes the `Workflow` tool. Expected: ~20 verdicts, 0 char-fragment agents
(if you see hundreds of agents, the `args` mistake happened — abort).

- [ ] **Step 3: Match verdicts to games by `normalized_title` (NEVER a returned id).**

Build the review table: `normalized_title → {session_length, confidence, reason}`. Flag any
verdict whose `normalized_title` is not in `titles.json` (hallucination) — there should be
none.

---

## Task 3: Owner reviews the PILOT (gate)

**Files:** none.

- [ ] **Step 1: Present the ~20 pilot verdicts to the owner** as a table:
  `title | session_length | confidence | reason`. Call out the trap cases (long-but-`short`
  episodic games) and any abstentions explicitly.

- [ ] **Step 2: Capture owner corrections + any prompt-tuning asks.** If the owner wants
  prompt changes (e.g. a clearer structural rule), update the Workflow prompt and re-run the
  pilot. **Do not proceed to the full run until the owner approves pilot quality.**

---

## Task 4: Full run (~38 batches) + assemble the flag list

**Files:**
- Create (throwaway): `_seed/verdicts.json` (all verdicts; deleted at end).

- [ ] **Step 1: Run the full classifier Workflow over all 764 titles.**

Update the script's `const TITLES` to the full set from `titles.json` (still embedded, NOT
via `args`). ~38 batches of 20 via `parallel()`. Persist the returned verdict array to
`_seed/verdicts.json`.

- [ ] **Step 2: Integrity checks.**

```python
# uv run python _seed/check_verdicts.py  (throwaway)
import json
titles = {t["normalized_title"] for t in json.load(open("_seed/titles.json", encoding="utf-8"))}
v = json.load(open("_seed/verdicts.json", encoding="utf-8"))
seen = [x["normalized_title"] for x in v]
print("verdicts:", len(v))
print("missing :", len(titles - set(seen)))           # titles with no verdict
print("dupes   :", len(seen) - len(set(seen)))         # title classified twice
print("halluc  :", len([s for s in seen if s not in titles]))  # title not in library
from collections import Counter
print(Counter(x["session_length"] for x in v))         # short/long/null distribution
```
Expected: `missing 0`, `dupes 0`, `halluc 0`. Re-run any missing batch. Investigate any
non-zero dupes/halluc before continuing.

- [ ] **Step 3: Build the flag list for owner spot-check.**

Flag list = **every `long` call + every abstention (`null`) + every `low`-confidence call.**
The remaining `high`-confidence `short` calls ride on pilot-validated quality. Render as a
table the owner can scan: `title | session_length | confidence | reason`.

---

## Task 5: Owner reviews the flag list (gate)

**Files:** none.

- [ ] **Step 1: Present the flag list to the owner.** Apply owner corrections to the
  in-memory verdict set (match by `normalized_title`). Record corrected entries.

- [ ] **Step 2: Owner approves the corrected verdict set** before it becomes the catalog.

---

## Task 6: Write + commit `game_traits.default.json`

**Files:**
- Modify: `game_traits.default.json` (currently `{}`).

- [ ] **Step 1: Generate the catalog from the approved verdicts (nulls omitted).**

```python
# uv run python _seed/write_catalog.py  (throwaway)
import json
v = json.load(open("_seed/verdicts.json", encoding="utf-8"))  # post-correction
catalog = {x["normalized_title"]: {"session_length": x["session_length"]}
           for x in v if x["session_length"] in ("short", "long")}
json.dump(catalog, open("game_traits.default.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=2, sort_keys=True)
print("catalog entries:", len(catalog))
```
`confidence`/`reason` are dropped; `null` titles are absent (→ neutral in the DB).

- [ ] **Step 2: Sanity-check the catalog.**

Confirm entry count ≈ (764 − abstentions), every value is `short` or `long`, keys are
`normalized_title`s. Spot-check a few known cases.

- [ ] **Step 3: Run the trait-catalog tests against the populated default
  (no temp-DB mutation — these tests monkeypatch their own catalog).**

Run: `uv run python -m pytest tests/test_game_traits_catalog.py tests/test_slots_session_length_ranking.py -q`
Expected: all pass.

- [ ] **Step 4: Lint + commit the catalog.**

Run: `ruff check .` (NEVER `ruff format`). Expected: clean.
```bash
git add game_traits.default.json
git commit -m "feat(traits): populate game_traits.default.json (session_length seed)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Backup `games.db`, then apply

**Files:**
- Create: `games.db.bak-20260603-pre-traits-seed-apply`.

- [ ] **Step 1: STOP any running app.py before touching the DB / app.**

PowerShell, filter to the python.exe whose CommandLine matches `*app.py*` — do NOT kill the
`servosity_restore` python processes:
```powershell
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*app.py*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

- [ ] **Step 2: Backup the live DB.**

```powershell
Copy-Item games.db games.db.bak-20260603-pre-traits-seed-apply
```
Confirm the backup file exists and is non-zero.

- [ ] **Step 3: Apply the catalog to the live DB.**

`apply_traits_catalog` runs automatically inside `migrate_db`. Trigger a migrate run:
```bash
uv run python -c "import models; models.migrate_db(); print('migrate done')"
```
(Equivalently, the next app startup applies it. The `--dry-run` wart: migrate applies for
real first, so the pre-commit verdict review + this backup are the safety net — not a dry
run.)

- [ ] **Step 4: Verify the apply counts.**

```bash
uv run python -c "
import sqlite3; c=sqlite3.connect('games.db')
print('set     :', c.execute(\"SELECT COUNT(*) FROM games WHERE session_length IS NOT NULL\").fetchone()[0])
print('short   :', c.execute(\"SELECT COUNT(*) FROM games WHERE session_length='short'\").fetchone()[0])
print('long    :', c.execute(\"SELECT COUNT(*) FROM games WHERE session_length='long'\").fetchone()[0])
print('catalog :', c.execute(\"SELECT COUNT(*) FROM games WHERE session_length_source='catalog'\").fetchone()[0])
print('manual  :', c.execute(\"SELECT COUNT(*) FROM games WHERE session_length_source='manual'\").fetchone()[0])
"
```
Expected: `set` == catalog entry count from Task 6; `manual` == 0; `catalog` == `set`.

---

## Task 8: Live UI verification

**Files:** none.

- [ ] **Step 1: Start the app** (use_reloader=False already set):

```bash
uv run python app.py
```

- [ ] **Step 2: Verify in the picks/Slate tab** (browser or `/api/slots` inspection):
  a Quick slot (`max_session_minutes` set) **excludes** `long` games and surfaces `short`
  higher; a Long slot (`min_session_minutes` set) surfaces `long` higher. Confirm a couple
  of known titles land in the expected slot.

- [ ] **Step 3: STOP the app** (same PowerShell filter as Task 7 Step 1).

---

## Task 9: Cleanup, push, memory

**Files:**
- Delete: `_seed/` throwaway scripts + JSON.

- [ ] **Step 1: Remove throwaway artifacts.**

```powershell
Remove-Item -Recurse -Force _seed
```
Confirm `git status` shows only the intended changes (the catalog commit is already in).

- [ ] **Step 2: Full test + lint gate.**

Run: `uv run python -m pytest -q` (expect all green, ≥504) and `ruff check .` (clean).

- [ ] **Step 3: Push to origin/main** (hold lifted — pushing is fine):

```bash
git push origin main
```

- [ ] **Step 4: Update memory** (`session-traits-next-steps.md`): mark Spec C DONE +
  APPLIED + PUSHED with final counts (entries, short/long split, abstentions, backup name).

---

## Self-Review

**Spec coverage:**
- Classifier Workflow (const titles, batch 20, schema, structural prompt, abstain, match by
  normalized_title) → Tasks 2 & 4. ✓
- Per-title input (title + platform + tags-when-present, HLTB withheld) → Task 1. ✓
- Verdict fields (session_length + confidence + reason; only session_length persisted) →
  Tasks 2/4/6. ✓
- Pilot → Tasks 2–3. ✓  Flag list (long + abstain + low-conf) → Tasks 4–5. ✓
- Catalog shape (nulls omitted) → Task 6. ✓
- Backup → apply (migrate auto-applies; dry-run wart noted) → Task 7. ✓
- UI verify (start/stop app) → Task 8. ✓
- Subagents never touch DB/app/push; controller does live writes → header + Tasks 2/4. ✓
- Tests (3 requirements) already covered → Task 0. ✓
- Genericity / manual-lock respect → inherited from `apply_traits_catalog` (Task 0). ✓

**Placeholder scan:** No TBD/TODO; all code/commands shown literally. ✓
**Type consistency:** verdict shape `{normalized_title, session_length, confidence, reason}`
used identically in Tasks 2/4/6; catalog shape `{nt: {session_length}}` consistent. ✓
