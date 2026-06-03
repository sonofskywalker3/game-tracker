# SP-B — Popular-Games session_length Seed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Grow `game_traits.default.json` from 735 to ~3,000 entries by pulling the most-rated games from IGDB and classifying each game's `session_length` (short/long) with a controller-run Claude Code Workflow.

**Architecture:** A new `popular_seed.py` module provides three pure-ish functions — `fetch_top_games` (IGDB pagination), `select_unseeded` (dedup vs the committed catalog), `merge_classifications` (minimal-diff write) — plus a thin CLI. Classification itself is a controller-run Workflow (free on the owner's subscription), not code. The code never touches the live `games.db`; it touches IGDB and the committed `game_traits.default.json` only.

**Tech Stack:** Python 3.11, `requests` (via existing `igdb_dlc`), `uv`, `pytest`, IGDB v4 Apicalypse, Claude Code Workflow.

**Spec:** `docs/superpowers/specs/2026-06-03-popular-games-session-length-seed-design.md`

---

## File Structure

- **Create:** `popular_seed.py` — IGDB top-N fetch, catalog dedup, minimal-diff merge, CLI. One responsibility: turn "the top N popular games" into "new `game_traits.default.json` rows," with the AI classification step done externally.
- **Create:** `tests/test_popular_seed.py` — unit tests (IGDB `_igdb_query` monkeypatched; catalog ops run against a tmp file).
- **Reuses (no edits):** `models.normalize_title` (models.py:862), `models.GAME_TRAITS_DEFAULT_PATH`, `igdb_dlc._igdb_query` / `igdb_dlc.get_access_token`, `config.get_twitch_credentials`.

## Conventions (owner rules — non-negotiable)

- Tests: `uv run python -m pytest` (plain `uv run pytest` fails: ModuleNotFoundError: models).
- Lint: `uv run ruff check .` ONLY — never `ruff format`.
- Subagents implementing Phase 1 tasks: pytest temp files + static review only. **Never** run the app, touch the live `games.db`, run a live IGDB fetch, run the Workflow, or `git push`.
- Phase 2 is **controller-only** and gated on owner review at the marked points.

---

## Phase 0 — IGDB field spike (CONTROLLER, ~5 min, no code committed)

The spec assumes `category = 0` selects main games. IGDB is migrating `category` → `game_type`. Verify the live field before the code hardcodes it.

- [ ] **Step 1: Run a read-only probe** (controller; needs Twitch creds in `config.json`)

```bash
uv run python -c "
import config, igdb_dlc
cid, sec = config.get_twitch_credentials()
tok = igdb_dlc.get_access_token(cid, sec)
for clause in ('category = 0', 'game_type = 0'):
    q = f'fields name,total_rating_count; where {clause} & total_rating_count != null; sort total_rating_count desc; limit 3;'
    try:
        print(clause, '->', [(r.get(\"name\"), r.get(\"total_rating_count\")) for r in igdb_dlc._igdb_query(q, cid, tok)])
    except Exception as e:
        print(clause, 'ERROR', e)
"
```

Expected: at least one clause returns 3 well-known high-rating games (e.g. Witcher 3, Zelda BotW, GTA V).

- [ ] **Step 2: Record the working clause** in `popular_seed.py`'s `_MAIN_GAME_FILTER` constant (Task 1). If `category = 0` works, use it; else use whichever clause returned data. Note the result in the commit message for Task 1.

---

## Phase 1 — Code (subagent-driven TDD)

### Task 1: `fetch_top_games`

**Files:**
- Create: `popular_seed.py`
- Test: `tests/test_popular_seed.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_popular_seed.py
import popular_seed


def test_fetch_top_games_parses_paginates_and_dedupes(monkeypatch):
    page0 = [
        {"id": 1, "name": "Alpha Quest", "genres": [{"name": "RPG"}],
         "summary": "An RPG.", "first_release_date": 1262304000, "total_rating_count": 900},
        {"id": 2, "name": "Alpha Quest", "total_rating_count": 800},  # dup normalized -> dropped
    ]
    page1 = [
        {"id": 3, "name": "Beta Blast", "genres": [{"name": "Shooter"}],
         "summary": None, "first_release_date": None, "total_rating_count": 700},
    ]
    pages = [page0, page1, []]
    calls = {"i": 0}

    def fake_query(q, c, t):
        i = calls["i"]; calls["i"] += 1
        assert "sort total_rating_count desc" in q
        return pages[i] if i < len(pages) else []

    monkeypatch.setattr(popular_seed.igdb_dlc, "_igdb_query", fake_query)
    out = popular_seed.fetch_top_games(10, client_id="c", token="t", page_size=2)

    assert [g["normalized_title"] for g in out] == ["alpha quest", "beta blast"]
    assert out[0]["genres"] == ["RPG"]
    assert out[0]["year"] == 2010
    assert out[1]["year"] is None
    assert out[0]["name"] == "Alpha Quest"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_popular_seed.py::test_fetch_top_games_parses_paginates_and_dedupes -v`
Expected: FAIL (ModuleNotFoundError / AttributeError: no `fetch_top_games`).

- [ ] **Step 3: Write minimal implementation**

```python
# popular_seed.py
"""SP-B: seed game_traits.default.json from IGDB's most-rated games.

Classification (session_length) is done by an external controller-run Claude
Code Workflow; this module only (a) fetches the candidate game list from IGDB,
(b) drops games already in the catalog, and (c) merges approved verdicts back
into game_traits.default.json with a minimal diff. It never touches games.db.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import igdb_dlc
from models import GAME_TRAITS_DEFAULT_PATH, normalize_title

_MAIN_GAME_FILTER = "category = 0"  # verified live in Phase 0; main_game only
_PAGE_SIZE = 500
_REQ_PAUSE_SECONDS = 0.25  # IGDB courtesy (4 req/s)


def fetch_top_games(n: int, *, client_id: str, token: str,
                    page_size: int = _PAGE_SIZE) -> list[dict]:
    """Return up to ``n`` most-rated IGDB main games, de-collided by
    normalized_title (the most-rated of a colliding pair wins, since results are
    sorted by rating count descending). Each item:
    ``{igdb_id, name, normalized_title, genres: list[str], summary: str|None, year: int|None}``.
    """
    out: list[dict] = []
    seen: set[str] = set()
    offset = 0
    while len(out) < n:
        query = (
            "fields name, genres.name, summary, first_release_date, total_rating_count; "
            f"where {_MAIN_GAME_FILTER} & total_rating_count != null; "
            f"sort total_rating_count desc; limit {page_size}; offset {offset};"
        )
        rows = igdb_dlc._igdb_query(query, client_id, token)
        if not rows:
            break
        for r in rows:
            name = r.get("name")
            if not name:
                continue
            nt = normalize_title(name)
            if nt in seen:
                continue
            seen.add(nt)
            ts = r.get("first_release_date")
            out.append({
                "igdb_id": r.get("id"),
                "name": name,
                "normalized_title": nt,
                "genres": [g["name"] for g in (r.get("genres") or []) if g.get("name")],
                "summary": r.get("summary"),
                "year": time.gmtime(ts).tm_year if ts else None,
            })
            if len(out) >= n:
                break
        offset += page_size
        time.sleep(_REQ_PAUSE_SECONDS)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_popular_seed.py -v`
Expected: PASS.

- [ ] **Step 5: Run ruff**

Run: `uv run ruff check popular_seed.py tests/test_popular_seed.py`
Expected: All checks passed.

- [ ] **Step 6: Commit**

```bash
git add popular_seed.py tests/test_popular_seed.py
git commit -m "feat(seed): fetch_top_games pulls most-rated IGDB main games

Phase 0 spike result: <category=0 | game_type=0> selects main games.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `select_unseeded`

**Files:**
- Modify: `popular_seed.py`
- Test: `tests/test_popular_seed.py`

- [ ] **Step 1: Write the failing test**

```python
def test_select_unseeded_drops_existing_and_intra_dups(tmp_path):
    catalog = tmp_path / "game_traits.default.json"
    catalog.write_text(json.dumps({"zelda": {"session_length": "long"}}, indent=2) + "\n",
                       encoding="utf-8")
    games = [
        {"normalized_title": "zelda", "name": "Zelda"},        # already in catalog -> drop
        {"normalized_title": "celeste", "name": "Celeste"},    # keep
        {"normalized_title": "celeste", "name": "Celeste DX"}, # intra-list dup -> drop
    ]
    out = popular_seed.select_unseeded(games, catalog_path=catalog)
    assert [g["normalized_title"] for g in out] == ["celeste"]
```

(Add `import json` at the top of the test file if not present.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_popular_seed.py::test_select_unseeded_drops_existing_and_intra_dups -v`
Expected: FAIL (no `select_unseeded`).

- [ ] **Step 3: Write minimal implementation** (append to `popular_seed.py`)

```python
def select_unseeded(games: list[dict], *,
                    catalog_path: Path = GAME_TRAITS_DEFAULT_PATH) -> list[dict]:
    """Drop games whose normalized_title already has a catalog entry, and
    intra-list normalized_title duplicates (first occurrence wins)."""
    existing = set(json.loads(catalog_path.read_text(encoding="utf-8")))
    out: list[dict] = []
    seen: set[str] = set()
    for g in games:
        nt = g["normalized_title"]
        if nt in existing or nt in seen:
            continue
        seen.add(nt)
        out.append(g)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_popular_seed.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add popular_seed.py tests/test_popular_seed.py
git commit -m "feat(seed): select_unseeded drops cataloged + duplicate titles

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `merge_classifications`

**Files:**
- Modify: `popular_seed.py`
- Test: `tests/test_popular_seed.py`

- [ ] **Step 1: Write the failing test**

```python
def test_merge_classifications_minimal_diff_skip_existing_and_unknown(tmp_path):
    catalog = tmp_path / "game_traits.default.json"
    original = json.dumps({"abzu": {"session_length": "short"}},
                          sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    catalog.write_text(original, encoding="utf-8")

    verdicts = [
        {"normalized_title": "celeste", "session_length": "short"},   # add
        {"normalized_title": "skyrim", "session_length": "long"},     # add
        {"normalized_title": "abzu", "session_length": "long"},       # existing -> skip (no overwrite)
        {"normalized_title": "mystery", "session_length": "unknown"}, # abstain -> skip
        {"normalized_title": "", "session_length": "short"},          # bad -> skip
    ]
    added, skipped = popular_seed.merge_classifications(verdicts, catalog_path=catalog)
    assert (added, skipped) == (2, 3)

    result = json.loads(catalog.read_text(encoding="utf-8"))
    assert result["abzu"] == {"session_length": "short"}  # untouched
    assert result["celeste"] == {"session_length": "short"}
    assert result["skyrim"] == {"session_length": "long"}
    assert "mystery" not in result

    # round-trips: sorted, 2-space indent, trailing newline preserved
    expected = json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    assert catalog.read_text(encoding="utf-8") == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_popular_seed.py::test_merge_classifications_minimal_diff_skip_existing_and_unknown -v`
Expected: FAIL (no `merge_classifications`).

- [ ] **Step 3: Write minimal implementation** (append to `popular_seed.py`)

```python
_VALID_SESSION_LENGTHS = frozenset({"short", "long"})


def merge_classifications(verdicts: list[dict], *,
                          catalog_path: Path = GAME_TRAITS_DEFAULT_PATH) -> tuple[int, int]:
    """Add ``{session_length}`` entries for new normalized_titles. Never overwrite
    an existing entry; skip anything not in {short, long} (unknown/abstentions)
    and rows missing a normalized_title. Write sorted + 2-space indent + preserved
    trailing newline (minimal diff). Returns ``(added, skipped)``."""
    raw = catalog_path.read_text(encoding="utf-8")
    catalog = json.loads(raw)
    added = skipped = 0
    for v in verdicts:
        nt = v.get("normalized_title")
        sl = v.get("session_length")
        if not nt or sl not in _VALID_SESSION_LENGTHS or nt in catalog:
            skipped += 1
            continue
        catalog[nt] = {"session_length": sl}
        added += 1
    out = json.dumps(catalog, sort_keys=True, indent=2, ensure_ascii=False)
    if raw.endswith("\n"):
        out += "\n"
    catalog_path.write_text(out, encoding="utf-8", newline="\n")
    return added, skipped
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_popular_seed.py -v`
Expected: PASS.

- [ ] **Step 5: Run ruff**

Run: `uv run ruff check popular_seed.py tests/test_popular_seed.py`
Expected: All checks passed.

- [ ] **Step 6: Commit**

```bash
git add popular_seed.py tests/test_popular_seed.py
git commit -m "feat(seed): merge_classifications minimal-diff into trait catalog

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: CLI (`--fetch` / `--merge`)

**Files:**
- Modify: `popular_seed.py`
- Test: `tests/test_popular_seed.py`

The controller uses `--fetch N --out input.json` to produce the classification
input list (which is then embedded as a const literal in the Workflow script),
and `--merge verdicts.json` to apply approved verdicts.

- [ ] **Step 1: Write the failing test**

```python
def test_cli_merge_applies_verdicts(tmp_path, monkeypatch, capsys):
    catalog = tmp_path / "game_traits.default.json"
    catalog.write_text("{}\n", encoding="utf-8")
    verdicts = tmp_path / "verdicts.json"
    verdicts.write_text(json.dumps([{"normalized_title": "celeste", "session_length": "short"}]),
                        encoding="utf-8")
    monkeypatch.setattr(popular_seed, "GAME_TRAITS_DEFAULT_PATH", catalog)

    popular_seed.main(["--merge", str(verdicts)])

    assert json.loads(catalog.read_text(encoding="utf-8")) == {"celeste": {"session_length": "short"}}
    assert "added 1" in capsys.readouterr().out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_popular_seed.py::test_cli_merge_applies_verdicts -v`
Expected: FAIL (no `main`).

- [ ] **Step 3: Write minimal implementation** (append to `popular_seed.py`)

```python
import argparse
import logging

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="SP-B popular-games session_length seed")
    parser.add_argument("--fetch", type=int, metavar="N",
                        help="fetch top-N popular games from IGDB to --out (JSON)")
    parser.add_argument("--out", type=str, help="output path for --fetch")
    parser.add_argument("--merge", type=str, metavar="VERDICTS_JSON",
                        help="merge approved {normalized_title, session_length} verdicts")
    args = parser.parse_args(argv)

    if args.fetch:
        import config
        cid, sec = config.get_twitch_credentials()
        token = igdb_dlc.get_access_token(cid, sec)
        games = select_unseeded(fetch_top_games(args.fetch, client_id=cid, token=token))
        out_path = Path(args.out or "popular_seed_input.json")
        out_path.write_text(json.dumps(games, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"fetched {len(games)} unseeded games -> {out_path}")
        return

    if args.merge:
        verdicts = json.loads(Path(args.merge).read_text(encoding="utf-8"))
        added, skipped = merge_classifications(verdicts, catalog_path=GAME_TRAITS_DEFAULT_PATH)
        print(f"merged: added {added}, skipped {skipped}")
        return

    parser.error("nothing to do: pass --fetch N or --merge PATH")


if __name__ == "__main__":
    main()
```

Note: the `--merge` path reads the module-level `GAME_TRAITS_DEFAULT_PATH` so the
test's `monkeypatch.setattr` is honored; do not import the name into a local.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_popular_seed.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Run full suite + ruff**

Run: `uv run python -m pytest -q && uv run ruff check .`
Expected: all green, All checks passed.

- [ ] **Step 6: Commit**

```bash
git add popular_seed.py tests/test_popular_seed.py
git commit -m "feat(seed): popular_seed CLI (--fetch / --merge)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 2 — Controller-run seed operation (NOT subagent tasks; gated on owner)

These steps are executed by the controller (the main session), not by implementation
subagents. They make live IGDB calls and run a Claude Code Workflow. No `games.db`
writes occur at any point.

### Task 5: Fetch the candidate list (controller)

- [ ] Run `uv run python popular_seed.py --fetch 3000 --out popular_seed_input.json`.
- [ ] Sanity-check: file has ~3,000 rows (minus titles already in the 735-entry
      catalog), each with `name`, `normalized_title`, `genres`, `summary`, `year`.
      Spot-check that rank-1 entries are recognizable AAA titles.
- [ ] `popular_seed_input.json` is a scratch artifact — do NOT commit it
      (add to `.gitignore` if it lands in `git status`).

### Task 6: PILOT Workflow — 25 genre-spread games (controller + OWNER GATE)

- [ ] From `popular_seed_input.json`, hand-pick ~25 games spanning genres (VN, RPG,
      roguelike, puzzle, action, turn-based tactics, sim, metroidvania, sports).
- [ ] Author a Claude Code Workflow whose script **embeds the 25-game list as a
      const literal** (name + genres + summary + year + normalized_title) — NOT via
      Workflow `args`. Guard `Array.isArray`. Each agent classifies ~20 games →
      `{normalized_title, session_length: short|long|unknown, confidence, reason}`.
      Embed the classification rule from the spec (session STRUCTURE not length;
      Phoenix Wright = short).
- [ ] Run the pilot. Present the 25 verdicts to the owner.
- [ ] **OWNER GATE:** owner reviews calibration. If miscalibrated, adjust the
      embedded instructions and re-run the pilot. Proceed only on owner approval.

### Task 7: FULL Workflow — remaining games (controller)

- [ ] Author the full Workflow the same way (const-embedded list, ~20/agent,
      ~150 batches). Use the `parallel`/`pipeline` accumulation pattern; match by
      `normalized_title` (never the returned id).
- [ ] Run it. Collect all verdicts into `popular_seed_verdicts.json` (scratch, not
      committed). Verify 0 missing / 0 duplicate / 0 hallucinated normalized_titles
      against `popular_seed_input.json`.

### Task 8: Owner reviews the flag list (OWNER GATE)

- [ ] Build the flag list = every `long` + every low-confidence verdict. Present to
      owner (grouped: high-long / low-long / low-short / unknown). High-confidence
      `short` rides free. `unknown` will be omitted.
- [ ] **OWNER GATE:** owner approves / flips entries. Apply the flips to
      `popular_seed_verdicts.json`.

### Task 9: Merge + verify + commit (controller)

- [ ] Run `uv run python popular_seed.py --merge popular_seed_verdicts.json`.
- [ ] `git diff --stat game_traits.default.json` — expect ~+2,000–2,250 lines,
      the 735 existing entries unchanged, format identical (sorted, 2-space).
- [ ] `uv run python -m pytest -q` → green. `uv run ruff check .` → clean.
- [ ] Commit `game_traits.default.json` (and the new `.gitignore` line if added).
      Push to origin/main.

```bash
git add game_traits.default.json .gitignore
git commit -m "data(traits): seed session_length for ~N popular games (SP-B)

IGDB top-3000 by rating count, classified by Claude Code (session structure,
not play-time), pilot-calibrated + flag-list reviewed by owner. 735 existing
entries preserved. <X> added, <Y> abstained/skipped.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Notes for the implementer

- **Phase 1 only** is subagent-implementable (pure code + monkeypatched tests).
  Phase 2 is controller-run and owner-gated — do not attempt it as a code task.
- `normalize_title` lives in `models.py`; import it, do not reimplement (one
  normalization function in the project).
- The minimal-diff writer mirrors the validated round-trip for these catalog
  files: `json.dumps(d, sort_keys=True, indent=2, ensure_ascii=False)` + the
  original trailing newline, written with `newline="\n"`.
- Scratch artifacts (`popular_seed_input.json`, `popular_seed_verdicts.json`) are
  never committed.
