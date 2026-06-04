# Bundle-authoritative apply — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the matching pipeline from discarding the IGDB id it computes — persist the full identity + lock on authoritative matches, and make the audit apply (self-heal) bundle matches instead of flagging them.

**Architecture:** Two pipeline edits. (1) `fetch_covers.search_game` returns the full resolved identity (id, cover, source, authoritative flag) instead of a bare cover URL, and `fetch_covers_generator` writes `igdb_id` + `cover_url` + `igdb_locked=1` when the match is authoritative. (2) `igdb_match.audit_igdb_matches` applies + locks bundle-source matches (clearing the review flag) and returns `{"applied": [...], "flagged": [...]}`. A small committed runner `run_igdb_audit.py` makes the heal repeatable with a `--dry-run`.

**Tech Stack:** Python 3, sqlite3, pytest. Run tests with `uv run python -m pytest`; lint with `ruff check`.

**Conventions (from project memory):**
- Tests use the `temp_db` fixture (a `pathlib.Path`); open a connection with `conn = models.get_db()` and `conn.close()` — never `conn = temp_db`.
- Subagents run pytest against the temp DB only — never touch the live `games.db` or the running app.
- Lint gate is `ruff check` only — never `ruff format`.
- Commit directly to `main`.

---

### Task 1: `search_game` returns the full identity to persist

**Files:**
- Modify: `fetch_covers.py:154-174` (`search_game`)
- Test: `tests/test_fetch_covers.py:167-192`

- [ ] **Step 1: Update the two existing `search_game` tests to expect an identity dict**

Replace `tests/test_fetch_covers.py:167-192` with:

```python
def test_search_game_returns_authoritative_identity(monkeypatch):
    import fetch_covers
    import igdb_match
    monkeypatch.setattr(igdb_match, "resolve_identity",
                        lambda *a, **k: {"igdb_id": 1, "name": "Mega Man 2",
                                         "cover_url": "https://x/t_cover_big/2.jpg",
                                         "source": "bundle"})
    got = fetch_covers.search_game("Mega Man 2", "c", "t",
                                   platform_ids={130}, collection_name="Mega Man Legacy Collection 2")
    assert got["cover_url"] == "https://x/t_cover_big/2.jpg"
    assert got["igdb_id"] == 1
    assert got["authoritative"] is True


def test_search_game_exact_title_is_authoritative_even_when_search_source(monkeypatch):
    import fetch_covers
    import igdb_match
    monkeypatch.setattr(igdb_match, "resolve_identity",
                        lambda *a, **k: {"igdb_id": 5, "name": "Celeste",
                                         "cover_url": "https://x/t_cover_big/c.jpg",
                                         "source": "search"})
    got = fetch_covers.search_game("Celeste", "c", "t")
    assert got["authoritative"] is True
    assert got["igdb_id"] == 5


def test_search_game_strict_rejects_loose_match(monkeypatch):
    import fetch_covers
    import igdb_match
    loose_identity = {"source": "search", "name": "Some Other Game",
                      "cover_url": "https://x/t_cover_big/y.jpg", "igdb_id": 9}
    monkeypatch.setattr(igdb_match, "resolve_identity", lambda *a, **k: loose_identity)

    # strict=True: name mismatch and source != "bundle" -> reject entirely
    assert fetch_covers.search_game("Celeste", "c", "t", strict=True) is None

    # strict=False: loose match returns a non-authoritative identity (cover only)
    got = fetch_covers.search_game("Celeste", "c", "t", strict=False)
    assert got["cover_url"] == "https://x/t_cover_big/y.jpg"
    assert got["authoritative"] is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_fetch_covers.py -k search_game -v`
Expected: FAIL — current `search_game` returns a string/None, so `got["cover_url"]` raises `TypeError: string indices must be integers`.

- [ ] **Step 3: Rewrite `search_game` to return the identity dict**

Replace `fetch_covers.py:154-174` with:

```python
def search_game(title, client_id, access_token, strict=False,
                platform_ids=None, collection_name=None):
    """Return the best IGDB identity to persist for a game, or None.

    Returns a dict ``{igdb_id, name, cover_url, source, authoritative}``.
    ``authoritative`` is True when the match is trustworthy enough to own the
    game's identity: the source is ``"bundle"`` (a bundle constituent matched on
    exact title) or the resolved name normalises equal to the searched title.

    When ``strict=True``, a non-authoritative match returns None so a loose search
    guess never overwrites an existing correct cover. A match with no cover URL is
    treated as no match.
    """
    import igdb_match
    from models import normalize_title
    identity = igdb_match.resolve_identity(
        title, set(platform_ids or ()), collection_name, client_id, access_token)
    if not identity or not identity.get("cover_url"):
        return None
    authoritative = (identity.get("source") == "bundle"
                     or normalize_title(identity.get("name") or "") == normalize_title(title))
    if strict and not authoritative:
        return None
    return {**identity, "authoritative": authoritative}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_fetch_covers.py -k search_game -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add fetch_covers.py tests/test_fetch_covers.py
git commit -m "refactor(covers): search_game returns full identity + authoritative flag

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: cover loop persists `igdb_id` + locks on authoritative matches

**Files:**
- Modify: `fetch_covers.py:287-318` (the body of the `for` loop in `fetch_covers_generator`)
- Test: `tests/test_fetch_covers.py` (append new tests at end of file)

- [ ] **Step 1: Write failing tests for the persistence behavior**

Append to `tests/test_fetch_covers.py`:

```python
def _run_cover_gen(monkeypatch, identity):
    """Drive fetch_covers_generator over a single inserted game, with auth and
    search_game stubbed. Returns the games row dict after the run."""
    import fetch_covers
    import models
    monkeypatch.setattr(fetch_covers, "get_access_token", lambda *a, **k: "tok")
    monkeypatch.setattr(fetch_covers, "search_game", lambda *a, **k: identity)
    list(fetch_covers.fetch_covers_generator("c", "s", skip_existing=False))
    conn = models.get_db()
    row = conn.execute(
        "SELECT igdb_id, cover_url, COALESCE(igdb_locked,0) AS locked, "
        "COALESCE(needs_igdb_review,0) AS review FROM games WHERE id=1").fetchone()
    conn.close()
    return row


def test_cover_gen_authoritative_match_persists_id_and_locks(temp_db, monkeypatch):
    import models
    conn = models.get_db()
    conn.execute("INSERT INTO games (id,title,normalized_title,cover_url,needs_igdb_review,igdb_review_reason) "
                 "VALUES (1,'Castlevania: Aria of Sorrow','castlevania aria of sorrow',"
                 "'https://images.igdb.com/igdb/image/upload/t_cover_big/co687k.jpg',1,'bundle')")
    conn.commit()
    conn.close()
    row = _run_cover_gen(monkeypatch, {
        "igdb_id": 222412, "name": "Castlevania: Aria of Sorrow",
        "cover_url": "https://images.igdb.com/igdb/image/upload/t_cover_big/cob949.jpg",
        "source": "bundle", "authoritative": True})
    assert row["igdb_id"] == 222412
    assert row["cover_url"].endswith("cob949.jpg")
    assert row["locked"] == 1
    assert row["review"] == 0  # applying the authoritative match clears the flag


def test_cover_gen_nonauthoritative_match_writes_cover_only(temp_db, monkeypatch):
    import models
    conn = models.get_db()
    conn.execute("INSERT INTO games (id,title,normalized_title,cover_url) "
                 "VALUES (1,'Some Game','some game',NULL)")
    conn.commit()
    conn.close()
    row = _run_cover_gen(monkeypatch, {
        "igdb_id": 9, "name": "A Loosely Related Game",
        "cover_url": "https://x/t_cover_big/loose.jpg",
        "source": "search", "authoritative": False})
    assert row["cover_url"].endswith("loose.jpg")
    assert row["igdb_id"] is None  # no id persisted on a loose cover fill
    assert row["locked"] == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_fetch_covers.py -k cover_gen -v`
Expected: FAIL — current loop treats `search_game`'s return as a URL string and writes `cover_url` only; `row["igdb_id"]` is None in the authoritative test.

- [ ] **Step 3: Rewrite the loop body to persist by authority**

Replace `fetch_covers.py:294-318` (from `cover_url = search_game(` through the `else:` `status = 'not_found'` block) with:

```python
            match = search_game(
                title, client_id, access_token, strict=upgrade_non_igdb,
                platform_ids=igdb_match.platform_ids_for(plat_short),
                collection_name=game["collection_name"])

            if match and match.get("authoritative"):
                # The resolver owns this game's identity: persist id + cover, lock
                # it, and retire any review flag. This is the self-correcting path.
                conn.execute(
                    "UPDATE games SET igdb_id = ?, cover_url = ?, igdb_locked = 1, "
                    "needs_igdb_review = 0, igdb_review_reason = NULL, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (match["igdb_id"], match["cover_url"], game['id'])
                )
                conn.commit()
                found += 1
                status = 'locked'
            elif match:
                # Non-authoritative fill (blank/non-IGDB cover, non-strict): cover only.
                conn.execute(
                    "UPDATE games SET cover_url = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (match["cover_url"], game['id'])
                )
                conn.commit()
                found += 1
                status = 'found'
            elif should_null_on_miss(game['cover_url']):
                # IGDB has no match and the existing art is wrong-shape: drop it.
                conn.execute(
                    "UPDATE games SET cover_url = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (game['id'],)
                )
                conn.commit()
                not_found_list.append(title)
                status = 'nulled'
            else:
                not_found_list.append(title)
                status = 'not_found'
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_fetch_covers.py -k cover_gen -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full fetch_covers test file to confirm no regressions**

Run: `uv run python -m pytest tests/test_fetch_covers.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add fetch_covers.py tests/test_fetch_covers.py
git commit -m "feat(covers): persist igdb_id + lock on authoritative matches

Stop discarding the resolved IGDB id; an authoritative (bundle or
exact-title) match now writes igdb_id + cover_url, locks the game, and
clears any review flag. Loose fills stay cover-only.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: audit applies bundle matches and returns `{applied, flagged}`

**Files:**
- Modify: `igdb_match.py:257-308` (`audit_igdb_matches`)
- Test: `tests/test_igdb_match.py` (rewrite the bundle test at 264-280; update 8 return-shape assertions)

- [ ] **Step 1: Rewrite the bundle audit test to expect apply-not-flag, and update the return shape in all audit tests**

In `tests/test_igdb_match.py`, replace `test_audit_flags_bundle_authoritative` (lines 264-280) with:

```python
def test_audit_applies_bundle_authoritative_not_flag(temp_db, monkeypatch):
    conn = models.get_db()
    conn.execute("INSERT INTO games (id,title,normalized_title,cover_url,collection_name,igdb_id) "
                 "VALUES (4,'Mega Man X','mega man x',?,'MM X LC',NULL)",
                 (_BASE + "t_cover_big/wrong.jpg",))
    conn.commit()
    _add_platform(conn, 4, "Switch")
    monkeypatch.setattr(igdb_match, "candidates_for", lambda *a, **k: [
        {"igdb_id": 1741, "name": "Mega Man X", "cover_url": _BASE + "t_cover_big/right.jpg",
         "platforms": [18], "source": "bundle"}])
    monkeypatch.setattr(igdb_match, "fetch_entry", lambda *a, **k: None)
    result = igdb_match.audit_igdb_matches(conn, client_id="c", token="t")
    assert result["applied"] == [4]
    assert result["flagged"] == []
    row = conn.execute("SELECT igdb_id, cover_url, COALESCE(igdb_locked,0), "
                       "COALESCE(needs_igdb_review,0), igdb_review_reason "
                       "FROM games WHERE id=4").fetchone()
    assert row[0] == 1741                      # id applied
    assert row[1].endswith("right.jpg")        # cover applied
    assert row[2] == 1                          # locked
    assert row[3] == 0 and row[4] is None       # review cleared
    conn.close()
```

Then update the 8 other audit return-value assertions in this file to the dict shape:

- Line 224 → `assert igdb_match.audit_igdb_matches(conn, client_id="c", token="t")["flagged"] == []`
- Line 240 → `flagged = igdb_match.audit_igdb_matches(conn, client_id="c", token="t")["flagged"]`
- Line 260 → `assert igdb_match.audit_igdb_matches(conn, client_id="c", token="t")["flagged"] == []`
- Line 294 → `flagged = igdb_match.audit_igdb_matches(conn, client_id="c", token="t")["flagged"]`
- Line 310 → `assert igdb_match.audit_igdb_matches(conn, client_id="c", token="t")["flagged"] == []`
- Line 321 → `assert igdb_match.audit_igdb_matches(conn, client_id="c", token="t")["flagged"] == []`
- Line 339 → `flagged = igdb_match.audit_igdb_matches(conn, client_id="c", token="t")["flagged"]`
- Line 360 → `flagged = igdb_match.audit_igdb_matches(conn, client_id="c", token="t")["flagged"]`

(Line 277 is inside the rewritten test above — already handled.)

- [ ] **Step 2: Run the audit tests to verify they fail**

Run: `uv run python -m pytest tests/test_igdb_match.py -k audit -v`
Expected: FAIL — `audit_igdb_matches` returns a list, so `["flagged"]` raises `TypeError: list indices must be integers`; the bundle test fails because the current code flags instead of applying.

- [ ] **Step 3: Apply the audit change**

Replace `igdb_match.py:293-308` (from `if best.get("source") == "bundle":` through `return flagged`) with:

```python
        if best.get("source") == "bundle":
            # Bundle constituents are authoritative (exact-title match inside the
            # owned bundle). Apply + lock instead of flagging: the pipeline owns
            # this identity, so heal it in place and clear any prior review flag.
            conn.execute(
                "UPDATE games SET igdb_id = ?, cover_url = ?, igdb_locked = 1, "
                "needs_igdb_review = 0, igdb_review_reason = NULL, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (best["igdb_id"], best_cover, r["id"]))
            applied.append(r["id"])
            continue
        elif stored_scored is not None:
            should_flag = best_scored["_score"] - stored_scored["_score"] >= _REVIEW_MARGIN
        else:
            should_flag = (best_scored["_score"] >= _STRONG_MATCH
                           and not best_scored.get("_mobile_only"))

        if should_flag:
            reason = _flag_reason(best, best_scored, stored_entry, stored_scored, gpi)
            conn.execute(
                "UPDATE games SET needs_igdb_review = 1, igdb_review_reason = ? WHERE id = ?",
                (reason, r["id"]))
            flagged.append(r["id"])
    conn.commit()
    return {"applied": applied, "flagged": flagged}
```

- [ ] **Step 4: Add the `applied` accumulator and update the docstring/signature return**

In `igdb_match.py`, at the top of `audit_igdb_matches` (the `flagged: list[int] = []` line, ~267), add `applied`:

```python
    flagged: list[int] = []
    applied: list[int] = []
```

And update the function's return type annotation and docstring summary line:

```python
def audit_igdb_matches(conn, *, client_id: str, token: str) -> dict[str, list[int]]:
    """Reconcile every non-locked game against its best IGDB candidate.

    Bundle-source candidates are authoritative: apply id + cover + lock and clear
    review (self-heal). Search-source candidates flag for review on a positive
    quality score-delta; games with no scorable stored entry flag only on a strong
    candidate. Returns ``{"applied": [...], "flagged": [...]}`` of game ids."""
```

- [ ] **Step 5: Run the audit tests to verify they pass**

Run: `uv run python -m pytest tests/test_igdb_match.py -k audit -v`
Expected: PASS (all audit tests)

- [ ] **Step 6: Run the full igdb_match test file**

Run: `uv run python -m pytest tests/test_igdb_match.py -v`
Expected: PASS (all)

- [ ] **Step 7: Commit**

```bash
git add igdb_match.py tests/test_igdb_match.py
git commit -m "feat(igdb): audit applies + locks bundle matches (self-heal)

Bundle-source candidates are authoritative, so the audit now writes
igdb_id + cover_url, locks the game, and clears the review flag instead
of flagging it for manual confirmation. Returns {applied, flagged}.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: committed audit runner with `--dry-run`

**Files:**
- Create: `run_igdb_audit.py`
- Test: `tests/test_run_igdb_audit.py`

- [ ] **Step 1: Write a failing test for the runner's reporting + dry-run rollback**

Create `tests/test_run_igdb_audit.py`:

```python
import models
import run_igdb_audit


def test_run_audit_reports_applied_and_flagged(temp_db, monkeypatch):
    monkeypatch.setattr(run_igdb_audit.config, "get_twitch_credentials",
                        lambda: ("cid", "secret"))
    monkeypatch.setattr(run_igdb_audit.igdb_dlc, "get_access_token",
                        lambda *a, **k: "tok")
    monkeypatch.setattr(run_igdb_audit.igdb_match, "audit_igdb_matches",
                        lambda conn, **k: {"applied": [1, 2], "flagged": [3]})
    summary = run_igdb_audit.run(dry_run=False)
    assert summary == {"applied": 2, "flagged": 1}


def test_run_audit_dry_run_does_not_persist(temp_db, monkeypatch):
    # A fake audit that actually writes, to prove dry-run rolls the write back.
    conn0 = models.get_db()
    conn0.execute("INSERT INTO games (id,title,normalized_title) VALUES (1,'G','g')")
    conn0.commit()
    conn0.close()

    def fake_audit(conn, **k):
        conn.execute("UPDATE games SET igdb_locked=1 WHERE id=1")
        return {"applied": [1], "flagged": []}

    monkeypatch.setattr(run_igdb_audit.config, "get_twitch_credentials",
                        lambda: ("cid", "secret"))
    monkeypatch.setattr(run_igdb_audit.igdb_dlc, "get_access_token", lambda *a, **k: "tok")
    monkeypatch.setattr(run_igdb_audit.igdb_match, "audit_igdb_matches", fake_audit)

    run_igdb_audit.run(dry_run=True)
    conn = models.get_db()
    locked = conn.execute("SELECT COALESCE(igdb_locked,0) FROM games WHERE id=1").fetchone()[0]
    conn.close()
    assert locked == 0  # dry-run rolled back
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_run_igdb_audit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'run_igdb_audit'`

- [ ] **Step 3: Create the runner**

Create `run_igdb_audit.py`:

```python
"""Run the IGDB match audit: self-heal authoritative (bundle) matches and flag
ambiguous ones for review. Use --dry-run to report counts without persisting.

    uv run python run_igdb_audit.py --dry-run
    uv run python run_igdb_audit.py
"""
import argparse
import logging

import config
import igdb_dlc
import igdb_match
from models import get_db

logger = logging.getLogger(__name__)


def run(*, dry_run: bool) -> dict[str, int]:
    """Execute the audit. Returns {'applied': N, 'flagged': M}. When dry_run is
    True the transaction is rolled back so nothing is persisted."""
    client_id, secret = config.get_twitch_credentials()
    if not client_id:
        raise RuntimeError("IGDB/Twitch credentials are not configured")
    token = igdb_dlc.get_access_token(client_id, secret)
    conn = get_db()
    try:
        result = igdb_match.audit_igdb_matches(conn, client_id=client_id, token=token)
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    finally:
        conn.close()
    return {"applied": len(result["applied"]), "flagged": len(result["flagged"])}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report applied/flagged counts without persisting")
    args = parser.parse_args()
    summary = run(dry_run=args.dry_run)
    prefix = "[dry-run] " if args.dry_run else ""
    logger.info("%saudit complete: %d applied (locked), %d flagged for review",
                prefix, summary["applied"], summary["flagged"])


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run python -m pytest tests/test_run_igdb_audit.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add run_igdb_audit.py tests/test_run_igdb_audit.py
git commit -m "feat(igdb): run_igdb_audit runner with --dry-run

Repeatable entry point for the self-healing audit; --dry-run reports
applied/flagged counts and rolls back without persisting.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: full suite + lint gate

- [ ] **Step 1: Run the entire test suite**

Run: `uv run python -m pytest`
Expected: PASS (all; previously 561 + the new tests). Investigate any failure before proceeding.

- [ ] **Step 2: Lint**

Run: `ruff check fetch_covers.py igdb_match.py run_igdb_audit.py tests/test_fetch_covers.py tests/test_igdb_match.py tests/test_run_igdb_audit.py`
Expected: no errors. (Never run `ruff format`.)

- [ ] **Step 3: Commit any lint fixes (if needed)**

```bash
git add -A
git commit -m "style(igdb): ruff check fixes for bundle-authoritative apply

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Post-implementation validation (operator step — NOT a subagent task)

Run by the main session against the **live** environment, after all tasks pass:

1. Stop the running app (PowerShell `Stop-Process`), back up: `games.db.bak-<ts>`.
2. Copy `games.db` → a scratch copy; point a throwaway run at the copy and run
   `uv run python run_igdb_audit.py --dry-run`; confirm it reports ~22 applied
   (the bundle pile) and the remainder still flagged.
3. On the copy, spot-check that Castlevania: Aria of Sorrow (id 860) now has
   `igdb_id=222412` and cover `cob949.jpg`, and that locked games were untouched.
4. Show the owner the applied list for approval.
5. On approval, run `uv run python run_igdb_audit.py` against the live DB; restart
   the app; confirm the flagged count dropped by the applied count.
6. Update memory ([[igdb-audit-score-delta]]) with the outcome.

---

## Self-review

**Spec coverage:**
- Spec Change 1 (persist identity + lock; authoritative = bundle or exact-title; loose fill = cover-only; strict skip; null-on-miss preserved) → Tasks 1 & 2. ✓
- Spec Change 2 (bundle branch applies + locks + clears review; other reasons unchanged; return carries applied + flagged) → Task 3. ✓
- Spec "self-heals / runnable" + safety (dry-run before live) → Task 4 runner + post-impl validation. ✓
- Spec testing bullets (locked skipped, cosmetic stem skip, search-source still flags) → covered by existing tests kept green in Tasks 2/3 plus new tests. ✓

**Placeholder scan:** none — every code step has complete code.

**Type consistency:** `search_game` returns `dict | None` with keys `igdb_id/name/cover_url/source/authoritative` (Task 1), consumed by `match["authoritative"]`/`match["igdb_id"]`/`match["cover_url"]` in Task 2. `audit_igdb_matches` returns `{"applied", "flagged"}` (Task 3), consumed as `result["applied"]`/`result["flagged"]` in tests and `run()` (Task 4). Consistent. ✓
