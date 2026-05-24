# Bundle Curation Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **CRITICAL (memory `subagent-impl-never-touch-live-db`):** Implementation/review subagents verify ONLY with `python -m pytest` (temp-DB fixtures in `tests/conftest.py`) and `ruff check .`. NEVER start the dev server, NEVER POST to the running app, NEVER call any mutating function against the real `games.db`. The live dry-run + apply (Task 6) is done by the ORCHESTRATOR with the user, not a subagent.

**Goal:** Make `import_scraped.cleanup_bundles` migrate a curated bundle phantom's curation (status + series, fill-only) onto its constituents and then delete the phantom, gated behind a new `--include-curated` flag.

**Architecture:** Add two helpers in `import_scraped.py` next to `_is_curated`/`_DEFAULT_CURATION`: `_resolve_constituent_ids` (title → game_id via the existing normalized-title key) and `_migrate_bundle_curation` (fill-only copy of status/series, ambiguous-field guard). `cleanup_bundles` gains an `include_curated` param that routes curated phantoms through migration → delete (or keeps the ambiguous ones). Default behavior is unchanged. The CLI gains `--include-curated`.

**Tech Stack:** Python 3, stdlib `sqlite3`, pytest (temp-DB fixtures), `ruff`. Spec: `docs/superpowers/specs/2026-05-24-bundle-curation-migration-design.md`.

---

## File Structure

- Modify: `import_scraped.py` — add `_resolve_constituent_ids`, `_migrate_bundle_curation`, `_AMBIGUOUS_FIELDS`; add `include_curated` param to `cleanup_bundles`; add `--include-curated` CLI flag + report line.
- Test: `tests/test_bundles.py` — append migration tests (reuse `temp_db` fixture, `models.get_db()`).

No new files. (`import_scraped.py` is ~511 lines; this adds ~50. The `library_filters.py` extraction is a separate follow-up, out of scope here per the spec.)

Reference facts (verified in the current code):
- `match_key(title)` == `models.normalize_title(models.clean_title(title))`, which is exactly what `_create_game` stores in `games.normalized_title` (`import_scraped.py:72-74`, `:194-200`). So a constituent resolves via `SELECT id FROM games WHERE normalized_title = match_key(title)`.
- `models.get_db()` sets `row_factory = sqlite3.Row`, so `row["col"]` works (relied on by `_is_curated`, `import_scraped.py:370-378`).
- `_DEFAULT_CURATION` (`import_scraped.py:362-367`) holds per-field default tuples: `status: ("backlog", "", None)`, `rating: (None,)`, `notes: ("", None)`, `series_id: (None,)`, `started_at: (None,)`, `completed_at: (None,)`, `sort_order: (None,)`, `hours_played: (0, 0.0, None)`, `priority: (5, None)`.
- Series append pattern (`app.py:654-667`): `order = MAX(series_order) for series_id or 0`; for each new member `order += 1` and upsert `series_id`, `series_order`.
- `user_ratings` upsert uses `ON CONFLICT(game_id) DO UPDATE SET ...` (valid; `game_id` is unique — see `app.py:1125`, `models.py:542-548`).

---

## Task 1: `_resolve_constituent_ids` helper

**Files:**
- Modify: `import_scraped.py` (add helper after `_is_curated`, ~line 379)
- Test: `tests/test_bundles.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_bundles.py`:

```python
def test_resolve_constituent_ids_finds_existing_skips_missing(temp_db):
    conn = models.get_db()
    _insert(conn, "Pikmin 1")
    _insert(conn, "Pikmin 2")
    conn.commit()
    ids = imp._resolve_constituent_ids(conn, ("Pikmin 1", "Pikmin 2", "Not Imported Yet"))
    rows = {r[0]: r[1] for r in conn.execute("SELECT title, id FROM games")}
    assert ids == [rows["Pikmin 1"], rows["Pikmin 2"]]  # missing title omitted
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_bundles.py::test_resolve_constituent_ids_finds_existing_skips_missing -v`
Expected: FAIL — `AttributeError: module 'import_scraped' has no attribute '_resolve_constituent_ids'`

- [ ] **Step 3: Write minimal implementation**

In `import_scraped.py`, immediately after `_is_curated` (after line 378):

```python
def _resolve_constituent_ids(conn: sqlite3.Connection,
                             constituents: tuple[str, ...]) -> list[int]:
    """game_ids for constituents that exist (matched by normalized title).

    Missing titles are omitted; in a real cleanup run import_games has already
    created them, in a dry run a not-yet-created constituent simply has nothing to
    migrate onto.
    """
    ids: list[int] = []
    for title in constituents:
        row = conn.execute("SELECT id FROM games WHERE normalized_title = ?",
                           (match_key(title),)).fetchone()
        if row:
            ids.append(row[0])
    return ids
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_bundles.py::test_resolve_constituent_ids_finds_existing_skips_missing -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add import_scraped.py tests/test_bundles.py
git commit -m "feat: _resolve_constituent_ids helper for bundle curation migration"
```

---

## Task 2: `_migrate_bundle_curation` — status fill-only + ambiguous guard

**Files:**
- Modify: `import_scraped.py` (add `_AMBIGUOUS_FIELDS` + `_migrate_bundle_curation` after `_resolve_constituent_ids`)
- Test: `tests/test_bundles.py` (append, with a richer phantom helper)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_bundles.py`:

```python
def _curated_phantom(conn, *, status="backlog", rating=None, notes=None,
                     series_id=None, hours_played=0, started_at=None,
                     completed_at=None):
    """Insert a Pikmin 1+2 phantom with chosen curation; return its game_id."""
    bid = _insert_phantom(conn, "Pikmin 1+2 Bundle", "nintendo", "70070000018036")
    conn.execute(
        "UPDATE user_ratings SET status=?, rating=?, notes=?, series_id=?, "
        "hours_played=?, started_at=?, completed_at=? WHERE game_id=?",
        (status, rating, notes, series_id, hours_played, started_at, completed_at, bid))
    conn.commit()
    return bid


def test_migrate_status_fills_default_constituents_only(temp_db):
    conn = models.get_db()
    _insert(conn, "Pikmin 1")
    _insert(conn, "Pikmin 2")
    # Pikmin 2 already curated by the user -> must NOT be overwritten (fill-only).
    p2 = conn.execute("SELECT id FROM games WHERE title='Pikmin 2'").fetchone()[0]
    conn.execute("INSERT INTO user_ratings (game_id, status) VALUES (?, 'playing')", (p2,))
    conn.commit()
    bid = _curated_phantom(conn, status="completed")
    ids = imp._resolve_constituent_ids(conn, ("Pikmin 1", "Pikmin 2"))
    report = imp._migrate_bundle_curation(conn, bid, ids, dry_run=False)
    conn.commit()
    statuses = {r[0]: r[1] for r in conn.execute(
        "SELECT g.title, ur.status FROM games g JOIN user_ratings ur ON ur.game_id=g.id "
        "WHERE g.title IN ('Pikmin 1','Pikmin 2')")}
    assert statuses["Pikmin 1"] == "completed"   # was default -> filled
    assert statuses["Pikmin 2"] == "playing"     # user value preserved
    assert report["ambiguous"] is False and report["status"] == "completed"
    conn.close()


def test_migrate_ambiguous_rating_migrates_nothing(temp_db):
    conn = models.get_db()
    _insert(conn, "Pikmin 1")
    _insert(conn, "Pikmin 2")
    conn.commit()
    bid = _curated_phantom(conn, status="completed", rating=5)
    ids = imp._resolve_constituent_ids(conn, ("Pikmin 1", "Pikmin 2"))
    report = imp._migrate_bundle_curation(conn, bid, ids, dry_run=False)
    conn.commit()
    statuses = [r[0] for r in conn.execute(
        "SELECT ur.status FROM games g JOIN user_ratings ur ON ur.game_id=g.id "
        "WHERE g.title IN ('Pikmin 1','Pikmin 2')")]
    assert report["ambiguous"] is True
    assert all(s == "backlog" for s in statuses)  # nothing migrated
    conn.close()


def test_migrate_status_dry_run_writes_nothing(temp_db):
    conn = models.get_db()
    _insert(conn, "Pikmin 1")
    conn.commit()
    bid = _curated_phantom(conn, status="completed")
    ids = imp._resolve_constituent_ids(conn, ("Pikmin 1",))
    imp._migrate_bundle_curation(conn, bid, ids, dry_run=True)
    conn.commit()
    s = conn.execute("SELECT ur.status FROM games g JOIN user_ratings ur ON ur.game_id=g.id "
                     "WHERE g.title='Pikmin 1'").fetchone()[0]
    assert s == "backlog"  # dry run wrote nothing
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_bundles.py -k migrate -v`
Expected: FAIL — `AttributeError: module 'import_scraped' has no attribute '_migrate_bundle_curation'`

- [ ] **Step 3: Write minimal implementation**

In `import_scraped.py`, after `_resolve_constituent_ids`:

```python
# Phantom curation that cannot be meaningfully split across several constituents;
# its presence blocks auto-delete (the phantom is kept for manual handling).
_AMBIGUOUS_FIELDS = ("rating", "notes", "hours_played")


def _migrate_bundle_curation(conn: sqlite3.Connection, bundle_id: int,
                             constituent_ids: list[int], *, dry_run: bool) -> dict:
    """Fill-only migrate the phantom's curation onto its constituents.

    Copies status (+ started/completed) onto each constituent still at its default,
    never overwriting curation the user already set. If the phantom carries a
    non-default rating/notes/hours_played, migrates nothing and returns
    {"ambiguous": True} so the caller keeps the phantom. Writes nothing when
    dry_run; the returned report stays accurate for already-existing constituents.
    """
    row = conn.execute(
        "SELECT status, rating, notes, series_id, started_at, completed_at, hours_played "
        "FROM user_ratings WHERE game_id = ?", (bundle_id,)).fetchone()
    report: dict = {"ambiguous": False, "status": None, "series_to": []}
    if not row:
        return report
    if any(row[f] not in _DEFAULT_CURATION[f] for f in _AMBIGUOUS_FIELDS):
        return {"ambiguous": True, "status": None, "series_to": []}

    if row["status"] not in _DEFAULT_CURATION["status"]:
        for cid in constituent_ids:
            cur = conn.execute("SELECT status FROM user_ratings WHERE game_id = ?",
                               (cid,)).fetchone()
            if cur is None or cur["status"] in _DEFAULT_CURATION["status"]:
                report["status"] = row["status"]
                if not dry_run:
                    conn.execute(
                        "INSERT INTO user_ratings (game_id, status, started_at, completed_at) "
                        "VALUES (?, ?, ?, ?) ON CONFLICT(game_id) DO UPDATE SET "
                        "status = excluded.status, started_at = excluded.started_at, "
                        "completed_at = excluded.completed_at, updated_at = CURRENT_TIMESTAMP",
                        (cid, row["status"], row["started_at"], row["completed_at"]))
    return report
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_bundles.py -k migrate -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add import_scraped.py tests/test_bundles.py
git commit -m "feat: bundle curation migration - fill-only status + ambiguous guard"
```

---

## Task 3: `_migrate_bundle_curation` — series fill-only append

**Files:**
- Modify: `import_scraped.py` (extend `_migrate_bundle_curation` with the series branch)
- Test: `tests/test_bundles.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_bundles.py`:

```python
def _make_series(conn, name):
    conn.execute("INSERT INTO series (name) VALUES (?)", (name,))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_migrate_series_appends_default_constituents_with_next_order(temp_db):
    conn = models.get_db()
    sid = _make_series(conn, "Pikmin")
    # An existing member fixes the next series_order at MAX+1.
    _insert(conn, "Existing Member")
    em = conn.execute("SELECT id FROM games WHERE title='Existing Member'").fetchone()[0]
    conn.execute("INSERT INTO user_ratings (game_id, series_id, series_order) VALUES (?, ?, 7)",
                 (em, sid))
    _insert(conn, "Pikmin 1")
    _insert(conn, "Pikmin 2")
    conn.commit()
    bid = _curated_phantom(conn, series_id=sid)
    ids = imp._resolve_constituent_ids(conn, ("Pikmin 1", "Pikmin 2"))
    report = imp._migrate_bundle_curation(conn, bid, ids, dry_run=False)
    conn.commit()
    members = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT g.title, ur.series_id, ur.series_order FROM games g "
        "JOIN user_ratings ur ON ur.game_id=g.id WHERE ur.series_id=?", (sid,))}
    assert members["Pikmin 1"] == (sid, 8)
    assert members["Pikmin 2"] == (sid, 9)
    assert set(report["series_to"]) == set(ids)
    conn.close()


def test_migrate_series_skips_constituent_already_in_a_series(temp_db):
    conn = models.get_db()
    sid = _make_series(conn, "Pikmin")
    other = _make_series(conn, "Other")
    _insert(conn, "Pikmin 1")
    _insert(conn, "Pikmin 2")
    p1 = conn.execute("SELECT id FROM games WHERE title='Pikmin 1'").fetchone()[0]
    conn.execute("INSERT INTO user_ratings (game_id, series_id, series_order) VALUES (?, ?, 1)",
                 (p1, other))  # already placed elsewhere
    conn.commit()
    bid = _curated_phantom(conn, series_id=sid)
    ids = imp._resolve_constituent_ids(conn, ("Pikmin 1", "Pikmin 2"))
    imp._migrate_bundle_curation(conn, bid, ids, dry_run=False)
    conn.commit()
    p1_series = conn.execute("SELECT series_id FROM user_ratings WHERE game_id=?", (p1,)).fetchone()[0]
    assert p1_series == other  # untouched
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_bundles.py -k "migrate_series" -v`
Expected: FAIL — `KeyError`/assertion: series not assigned (`series_to` empty, members missing).

- [ ] **Step 3: Write the implementation**

In `import_scraped.py`, add the series branch to `_migrate_bundle_curation` immediately before its final `return report` (i.e. after the status block):

```python
    if row["series_id"] is not None:
        order = conn.execute(
            "SELECT MAX(series_order) FROM user_ratings WHERE series_id = ?",
            (row["series_id"],)).fetchone()[0] or 0
        for cid in constituent_ids:
            cur = conn.execute("SELECT series_id FROM user_ratings WHERE game_id = ?",
                               (cid,)).fetchone()
            if cur is None or cur["series_id"] is None:
                order += 1
                report["series_to"].append(cid)
                if not dry_run:
                    conn.execute(
                        "INSERT INTO user_ratings (game_id, series_id, series_order) "
                        "VALUES (?, ?, ?) ON CONFLICT(game_id) DO UPDATE SET "
                        "series_id = excluded.series_id, series_order = excluded.series_order, "
                        "updated_at = CURRENT_TIMESTAMP",
                        (cid, row["series_id"], order))
    return report
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_bundles.py -k "migrate_series" -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add import_scraped.py tests/test_bundles.py
git commit -m "feat: bundle curation migration - fill-only series append"
```

---

## Task 4: Wire `include_curated` into `cleanup_bundles`

**Files:**
- Modify: `import_scraped.py:381-417` (`cleanup_bundles`)
- Test: `tests/test_bundles.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_bundles.py`:

```python
def test_cleanup_include_curated_migrates_and_deletes(temp_db):
    conn = models.get_db()
    _insert(conn, "Pikmin 1")
    _insert(conn, "Pikmin 2")
    conn.commit()
    bid = _curated_phantom(conn, status="completed")
    results = imp.cleanup_bundles(conn, include_curated=True)
    conn.commit()
    titles = {r[0] for r in conn.execute("SELECT title FROM games")}
    assert "Pikmin 1+2 Bundle" not in titles  # phantom deleted
    statuses = [r[0] for r in conn.execute(
        "SELECT ur.status FROM games g JOIN user_ratings ur ON ur.game_id=g.id "
        "WHERE g.title IN ('Pikmin 1','Pikmin 2')")]
    assert statuses == ["completed", "completed"]
    assert any(r["action"] == "migrated_deleted" for r in results)
    conn.close()


def test_cleanup_include_curated_keeps_ambiguous(temp_db):
    conn = models.get_db()
    _insert(conn, "Pikmin 1")
    conn.commit()
    _curated_phantom(conn, status="completed", notes="my note")
    results = imp.cleanup_bundles(conn, include_curated=True)
    conn.commit()
    titles = {r[0] for r in conn.execute("SELECT title FROM games")}
    assert "Pikmin 1+2 Bundle" in titles  # kept (ambiguous)
    assert any(r["action"] == "kept_ambiguous" for r in results)
    conn.close()


def test_cleanup_default_still_keeps_curated(temp_db):
    conn = models.get_db()
    _insert(conn, "Pikmin 1")
    conn.commit()
    _curated_phantom(conn, status="completed")
    results = imp.cleanup_bundles(conn)  # no include_curated
    conn.commit()
    titles = {r[0] for r in conn.execute("SELECT title FROM games")}
    assert "Pikmin 1+2 Bundle" in titles
    assert any(r["action"] == "kept_curated" for r in results)
    conn.close()


def test_cleanup_include_curated_dry_run_writes_nothing(temp_db):
    conn = models.get_db()
    _insert(conn, "Pikmin 1")
    conn.commit()
    _curated_phantom(conn, status="completed")
    imp.cleanup_bundles(conn, include_curated=True, dry_run=True)
    conn.commit()
    assert "Pikmin 1+2 Bundle" in {r[0] for r in conn.execute("SELECT title FROM games")}
    assert conn.execute("SELECT ur.status FROM games g JOIN user_ratings ur ON ur.game_id=g.id "
                        "WHERE g.title='Pikmin 1'").fetchone()[0] == "backlog"
    conn.close()


def test_cleanup_include_curated_idempotent(temp_db):
    conn = models.get_db()
    _insert(conn, "Pikmin 1")
    _insert(conn, "Pikmin 2")
    conn.commit()
    _curated_phantom(conn, status="completed")
    imp.cleanup_bundles(conn, include_curated=True)
    conn.commit()
    # second run: phantom gone, constituents already filled -> no error, no change
    results = imp.cleanup_bundles(conn, include_curated=True)
    conn.commit()
    statuses = [r[0] for r in conn.execute(
        "SELECT ur.status FROM games g JOIN user_ratings ur ON ur.game_id=g.id "
        "WHERE g.title IN ('Pikmin 1','Pikmin 2')")]
    assert statuses == ["completed", "completed"]
    assert results == [] or all(r["action"] != "migrated_deleted" for r in results)
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_bundles.py -k "include_curated or default_still" -v`
Expected: FAIL — `TypeError: cleanup_bundles() got an unexpected keyword argument 'include_curated'`

- [ ] **Step 3: Write the implementation**

Replace the body of `cleanup_bundles` (`import_scraped.py:381-417`) with this full version (signature gains `include_curated`):

```python
def cleanup_bundles(conn: sqlite3.Connection, *, dry_run: bool = False,
                    include_curated: bool = False,
                    confirm_fn: Callable[[str, str, float], bool] = _safe_auto_confirm
                    ) -> list[dict]:
    """One-time pass: expand every known bundle that exists in the DB as a phantom.

    For each mapped (source, external_id) present: ensure its constituents exist
    and are owned on the bundle's platform(s), then delete the phantom row. An
    uncurated phantom is deleted outright. A curated phantom is kept (reported
    `kept_curated`) UNLESS include_curated is set, in which case its curation is
    migrated onto its constituents (fill-only) and the phantom deleted
    (`migrated_deleted`); a phantom carrying un-splittable curation
    (rating/notes/hours) is kept and reported `kept_ambiguous`. Honors dry_run.
    """
    results: list[dict] = []
    for (source, external_id), constituents in bundles.BUNDLE_CONTENTS.items():
        row = conn.execute(
            "SELECT game_id FROM game_external_ids WHERE source = ? AND external_id = ?",
            (source, external_id)).fetchone()
        if not row:
            continue
        bundle_id = row[0]
        bundle = conn.execute("SELECT title FROM games WHERE id = ?", (bundle_id,)).fetchone()
        if not bundle:
            continue
        platforms = [r[0] for r in conn.execute(
            "SELECT p.short_name FROM game_platforms gp JOIN platforms p ON p.id = gp.platform_id "
            "WHERE gp.game_id = ?", (bundle_id,))] or [None]
        synth = [_constituent_game({"platform": pf}, title, source)
                 for title in constituents for pf in platforms]
        stats = import_games(conn, synth, source, dry_run=dry_run, confirm_fn=confirm_fn)
        curated = _is_curated(conn, bundle_id)
        migrated: dict = {}
        if not curated:
            action = "deleted"
            if not dry_run:
                conn.execute("DELETE FROM games WHERE id = ?", (bundle_id,))
        elif include_curated:
            ids = _resolve_constituent_ids(conn, constituents)
            migrated = _migrate_bundle_curation(conn, bundle_id, ids, dry_run=dry_run)
            if migrated["ambiguous"]:
                action = "kept_ambiguous"
            else:
                action = "migrated_deleted"
                if not dry_run:
                    conn.execute("DELETE FROM games WHERE id = ?", (bundle_id,))
        else:
            action = "kept_curated"
        results.append({"bundle_id": bundle_id, "title": bundle[0],
                        "source": source, "external_id": external_id,
                        "action": action, "constituents_created": stats.new_games,
                        "migrated": migrated})
    if not dry_run:
        conn.commit()
    return results
```

Note on the idempotency test: on the second run the phantom no longer exists, so its `(source, external_id)` lookup returns no row and it is skipped — `results` contains no entry for it. The assertion allows either an empty list or no `migrated_deleted` action.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_bundles.py -k "include_curated or default_still" -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Run the full bundles file to confirm no regressions**

Run: `python -m pytest tests/test_bundles.py -v`
Expected: PASS (all, including the original cleanup/import tests)

- [ ] **Step 6: Commit**

```bash
git add import_scraped.py tests/test_bundles.py
git commit -m "feat: cleanup_bundles --include-curated migrates curation then deletes phantom"
```

---

## Task 5: CLI `--include-curated` flag + report line

**Files:**
- Modify: `import_scraped.py:459-478` (argparse + cleanup report printing)

- [ ] **Step 1: Add the flag and pass it through**

In `main`, after the `--cleanup-bundles` argument (`import_scraped.py:459-460`), add:

```python
    parser.add_argument("--include-curated", action="store_true",
                        help="with --cleanup-bundles: also migrate curated phantoms' "
                             "status/series onto constituents, then delete them")
```

- [ ] **Step 2: Use the flag and enrich the report line**

Replace the `if args.cleanup_bundles:` block (`import_scraped.py:472-480`) with:

```python
    if args.cleanup_bundles:
        report = cleanup_bundles(conn, dry_run=args.dry_run,
                                 include_curated=args.include_curated)
        for r in report:
            extra = ""
            mig = r.get("migrated") or {}
            if mig.get("status"):
                extra += f" status={mig['status']}"
            if mig.get("series_to"):
                extra += f" series+{len(mig['series_to'])}"
            logger.info("%s: %s (+%d constituents)%s [%s]", r["title"], r["action"],
                        r["constituents_created"], extra, f"{r['source']}/{r['external_id']}")
        logger.info("DRY RUN — no changes written." if args.dry_run
                    else "bundles processed: %d" % len(report))
        conn.close()
        return
```

- [ ] **Step 3: Verify the CLI parses and the suite is green**

Run: `python import_scraped.py --cleanup-bundles --include-curated --dry-run --help`
Expected: argparse help text prints `--include-curated` with no error. (This is `--help`; it touches no DB.)

Run: `python -m pytest tests/test_bundles.py -v`
Expected: PASS (all).

- [ ] **Step 4: Commit**

```bash
git add import_scraped.py
git commit -m "feat: --include-curated CLI flag + migration summary in cleanup report"
```

---

## Task 6: Full verification + live dry-run preview (ORCHESTRATOR ONLY)

> Steps 1-2 are safe for any agent. **Steps 3+ touch the real `games.db` and MUST be performed by the orchestrator with the user — NOT a subagent** (memory `subagent-impl-never-touch-live-db`).

- [ ] **Step 1: Run the full test suite**

Run: `python -m pytest`
Expected: PASS — previous 173 + the ~12 new bundle tests (≈185 passing). If any pre-existing test fails, STOP and investigate before proceeding.

- [ ] **Step 2: Lint**

Run: `ruff check .`
Expected: clean (no errors).

- [ ] **Step 3 (orchestrator): Back up the live DB**

```bash
Copy-Item "games.db" "games.db.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
```

- [ ] **Step 4 (orchestrator): Dry-run preview on the real data**

Run: `python import_scraped.py --cleanup-bundles --include-curated --dry-run`
Expected report (the 6 curated phantoms): 670 Pikmin (`migrated_deleted status=completed`), 570 Nobody Saves (`migrated_deleted status=playing`), 501 Batman: Return to Arkham (`migrated_deleted series+2`), 488 AC Chronicles (`migrated_deleted series+3`), 610 Watch Dogs + 576 Prototype (`migrated_deleted`, no extras). No `kept_ambiguous`. "DRY RUN — no changes written."

- [ ] **Step 5 (orchestrator): Show the user the dry-run output and get explicit OK.** Do not proceed without it.

- [ ] **Step 6 (orchestrator): Apply for real**

Run: `python import_scraped.py --cleanup-bundles --include-curated`

- [ ] **Step 7 (orchestrator): Spot-check the result (read-only)** — confirm the 6 phantom rows are gone, Pikmin 1/2 = completed, Nobody Saves the World = playing, Arkham Asylum/City in the Batman series, Chronicles China/India/Russia in the Assassin's Creed series.

- [ ] **Step 8: Update memory** `post-import-cleanup-workstreams.md` with the applied result + new backup filename.

---

## Self-Review

**Spec coverage:**
- Fill-only status migration → Task 2. ✓
- Fill-only series append (next `series_order`) → Task 3. ✓
- `sort_order`/`priority` dropped → implicit (helper never reads/writes them). ✓
- Ambiguous `rating`/`notes`/`hours_played` → `kept_ambiguous` → Tasks 2 + 4. ✓
- `--include-curated` flag, default unchanged → Tasks 4 + 5. ✓
- `--dry-run` writes nothing → Tasks 2, 4. ✓
- Idempotent → Task 4. ✓
- DLC: dropped, base-game constituents only → no new code needed (already in `BUNDLE_CONTENTS`); migration operates only on resolved constituents, never creates DLC. ✓
- Live apply with backup + dry-run + user OK → Task 6 (orchestrator). ✓

**Placeholder scan:** none — every code/test step shows full content.

**Type consistency:** `_resolve_constituent_ids(conn, constituents) -> list[int]` (Task 1) is called with `constituents` in Task 4. `_migrate_bundle_curation(conn, bundle_id, constituent_ids, *, dry_run) -> dict` returns `{"ambiguous", "status", "series_to"}` (Tasks 2-3), consumed in Task 4 (`migrated["ambiguous"]`) and Task 5 (`mig.get("status")`, `mig.get("series_to")`). `cleanup_bundles(..., include_curated=...)` keyword matches CLI call in Task 5. ✓
