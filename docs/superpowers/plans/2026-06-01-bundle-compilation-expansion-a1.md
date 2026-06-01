# Bundle / compilation expansion (A1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Break multi-game products (launcher compilations + entitlement bundles) out into individual game rows from a committed, owner-curated catalog, deleting the redundant parent tile and marking launcher-compilation constituents with a `collection_name` that drives a tile badge + a detail-view "Part of …" launch cue — so the trait seed later classifies real individual titles.

**Architecture:** A new `models.load_bundle_catalog()` (mirrors `load_game_traits`/`load_series_patterns`) loads a `normalized_title → {type, constituents}` catalog. A new `games.collection_name` column (idempotent migration) carries the launcher name on constituents. A gated `import_scraped.apply_bundle_catalog(conn, dry_run=…)` reuses the existing bundle machinery (`import_games`, `_constituent_game`, `_resolve_constituent_ids`, `_migrate_bundle_curation`, `_is_curated`) to expand each catalogued parent by type, migrate its curation fill-only, and delete it. The library tile (`gameCardHtml`) and game modal surface `collection_name`. Anthologies are a no-op in A1 (deferred A2). A Claude-Code Workflow drafts the catalog for the owner's PR (controller-run runbook, not a TDD task).

**Tech Stack:** Python 3 + sqlite3, Flask, vanilla JS / Jinja / Tailwind, `uv`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-06-01-bundle-compilation-expansion-design.md`

---

## Conventions (read once, apply to every task)

- **Run tests:** `uv run python -m pytest` (plain `uv run pytest` fails: `ModuleNotFoundError: models`). Single: `uv run python -m pytest tests/test_x.py::test_name -v`.
- **Lint gate:** `ruff check <files>` ONLY. **NEVER** `ruff format` (hand-aligned codebase).
- **Subagents:** never run `app.py`, never touch live `games.db`, never `git push`. Use the `temp_db`/`client` pytest fixtures only. The controller restarts the app + verifies UI live.
- **Commit footer (every commit):**
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```
- **Process hygiene (controller only):** app runs `use_reloader=False`; STOP it (PowerShell `Stop-Process`) before editing `.py`, relaunch to verify.

## File Structure

**New files**
- `bundle_catalog.default.json` — committed catalog (ships `{}`; owner fills via the AI draft + PR).
- `tests/test_bundle_catalog.py` — `load_bundle_catalog` + `migrate_collection_name`.
- `tests/test_apply_bundle_catalog.py` — the expansion pipeline.
- `tests/test_api_games_collection.py` — API payload carries `collection_name`; tile/modal render markers.

**Modified files**
- `models.py` — `BUNDLE_CATALOG_PATH`/`_DEFAULT_PATH`, `load_bundle_catalog`, `migrate_collection_name`, register the migration in `migrate_db`.
- `tests/conftest.py` — mirror `migrate_collection_name` in `temp_db`.
- `import_scraped.py` — `apply_bundle_catalog(conn, *, dry_run=False)` (alongside `cleanup_bundles`) + a `--apply-bundle-catalog` CLI flag.
- `.gitignore` — add `bundle_catalog.json`.
- `app.py` — add `g.collection_name` to the `/api/games` SELECT and the `/api/games/search` SELECT + payload.
- `templates/base.html` — `gameCardHtml` tile badge + the modal "Part of …" line.

---

## Task 1: Committed catalog file + `load_bundle_catalog`

Mirrors `models.load_game_traits` (Spec C / traits work) and `load_series_patterns`.

**Files:** Create `bundle_catalog.default.json`, `tests/test_bundle_catalog.py`; Modify `models.py`, `.gitignore`.

- [ ] **Step 1: Create the committed (empty) catalog** — `bundle_catalog.default.json`:
```json
{}
```
(End the file with a trailing newline.)

- [ ] **Step 2: Add `bundle_catalog.json` to `.gitignore`** — append if not present:
```
bundle_catalog.json
```

- [ ] **Step 3: Write the failing test** — `tests/test_bundle_catalog.py`:
```python
import json

import models


def test_load_bundle_catalog_reads_default(monkeypatch, tmp_path):
    default = tmp_path / "bundle_catalog.default.json"
    default.write_text(json.dumps({"mega man legacy collection":
                                   {"type": "compilation", "constituents": ["Mega Man"]}}),
                       encoding="utf-8")
    monkeypatch.setattr(models, "BUNDLE_CATALOG_PATH", tmp_path / "bundle_catalog.json")
    monkeypatch.setattr(models, "BUNDLE_CATALOG_DEFAULT_PATH", default)
    assert models.load_bundle_catalog() == {
        "mega man legacy collection": {"type": "compilation", "constituents": ["Mega Man"]}}


def test_load_bundle_catalog_prefers_per_user(monkeypatch, tmp_path):
    (tmp_path / "bundle_catalog.default.json").write_text("{}", encoding="utf-8")
    per_user = tmp_path / "bundle_catalog.json"
    per_user.write_text(json.dumps({"x": {"type": "entitlement", "constituents": []}}),
                        encoding="utf-8")
    monkeypatch.setattr(models, "BUNDLE_CATALOG_PATH", per_user)
    monkeypatch.setattr(models, "BUNDLE_CATALOG_DEFAULT_PATH", tmp_path / "bundle_catalog.default.json")
    assert models.load_bundle_catalog() == {"x": {"type": "entitlement", "constituents": []}}


def test_load_bundle_catalog_missing_is_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(models, "BUNDLE_CATALOG_PATH", tmp_path / "nope.json")
    monkeypatch.setattr(models, "BUNDLE_CATALOG_DEFAULT_PATH", tmp_path / "also-nope.json")
    assert models.load_bundle_catalog() == {}


def test_load_bundle_catalog_malformed_is_empty(monkeypatch, tmp_path):
    bad = tmp_path / "bundle_catalog.default.json"
    bad.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(models, "BUNDLE_CATALOG_PATH", tmp_path / "bundle_catalog.json")
    monkeypatch.setattr(models, "BUNDLE_CATALOG_DEFAULT_PATH", bad)
    assert models.load_bundle_catalog() == {}
```

- [ ] **Step 4: Run to verify it fails** — `uv run python -m pytest tests/test_bundle_catalog.py -v` — expect FAIL (`AttributeError: ... BUNDLE_CATALOG_PATH` / `load_bundle_catalog`).

- [ ] **Step 5: Implement paths + loader.** In `models.py`, directly below the `GAME_TRAITS_*` path constants (added by the traits work) add:
```python
BUNDLE_CATALOG_PATH = Path(__file__).parent / "bundle_catalog.json"                 # per-user (gitignored)
BUNDLE_CATALOG_DEFAULT_PATH = Path(__file__).parent / "bundle_catalog.default.json"  # committed seed
```
Directly below `load_game_traits` add:
```python
def load_bundle_catalog() -> dict:
    """Load the normalized_title->bundle-entry catalog (per-user file, else committed seed)."""
    path = BUNDLE_CATALOG_PATH if BUNDLE_CATALOG_PATH.exists() else BUNDLE_CATALOG_DEFAULT_PATH
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
```

- [ ] **Step 6: Run to verify pass** — `uv run python -m pytest tests/test_bundle_catalog.py -v` — expect PASS (4).

- [ ] **Step 7: Lint** — `ruff check models.py tests/test_bundle_catalog.py` — expect no errors.

- [ ] **Step 8: Commit**
```bash
git add bundle_catalog.default.json .gitignore models.py tests/test_bundle_catalog.py
git commit -m "feat(bundles): committed bundle_catalog + load_bundle_catalog loader

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `games.collection_name` column

Mirrors `migrate_game_traits` (idempotent guarded `ALTER TABLE`). Safe/additive → runs in `migrate_db`.

**Files:** Modify `models.py` (new migration + register in `migrate_db`), `tests/conftest.py`; add tests to `tests/test_bundle_catalog.py`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_bundle_catalog.py`:
```python
def test_migrate_collection_name_adds_column(temp_db):
    conn = models.get_db()
    cols = {c[1] for c in conn.execute("PRAGMA table_info(games)").fetchall()}
    assert "collection_name" in cols
    conn.close()


def test_migrate_collection_name_idempotent(temp_db):
    conn = models.get_db()
    models.migrate_collection_name(conn)
    models.migrate_collection_name(conn)  # must not raise
    cols = {c[1] for c in conn.execute("PRAGMA table_info(games)").fetchall()}
    assert "collection_name" in cols
    conn.close()
```

- [ ] **Step 2: Run to verify it fails** — `uv run python -m pytest tests/test_bundle_catalog.py::test_migrate_collection_name_adds_column -v` — expect FAIL (column absent / function undefined).

- [ ] **Step 3: Implement `migrate_collection_name`.** In `models.py`, directly after `migrate_game_traits` add:
```python
def migrate_collection_name(conn: sqlite3.Connection) -> None:
    """Add games.collection_name (the launcher compilation a broken-out game belongs
    to). Idempotent. Non-null drives the tile 'collection' badge + the detail-view
    'Part of <name>' launch cue; null is a normal standalone game.
    """
    cols = [c[1] for c in conn.execute("PRAGMA table_info(games)").fetchall()]
    if "collection_name" not in cols:
        conn.execute("ALTER TABLE games ADD COLUMN collection_name TEXT")
    conn.commit()
```

- [ ] **Step 4: Register in `migrate_db`.** In `models.py` `migrate_db`, directly after the `migrate_game_traits(conn)` line, add:
```python
    migrate_collection_name(conn)
```

- [ ] **Step 5: Mirror in the fixture.** In `tests/conftest.py`, in `temp_db`, directly after `models.migrate_game_traits(conn)` add:
```python
    models.migrate_collection_name(conn)
```

- [ ] **Step 6: Run to verify pass** — `uv run python -m pytest tests/test_bundle_catalog.py -v` — expect PASS (all).

- [ ] **Step 7: Lint** — `ruff check models.py tests/conftest.py tests/test_bundle_catalog.py` — expect no errors.

- [ ] **Step 8: Commit**
```bash
git add models.py tests/conftest.py tests/test_bundle_catalog.py
git commit -m "feat(bundles): games.collection_name column (idempotent migration)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `apply_bundle_catalog` — the expansion pipeline

The core. Reuses `import_scraped`'s existing bundle helpers. Gated controller op (NOT in `migrate_db`). Idempotent, dry-run-able, returns a report.

**Files:** Modify `import_scraped.py`; Create `tests/test_apply_bundle_catalog.py`.

- [ ] **Step 1: Write the failing tests** — `tests/test_apply_bundle_catalog.py`:
```python
import models
import import_scraped as imp


def _platform_id(conn, sn):
    return conn.execute("SELECT id FROM platforms WHERE short_name=?", (sn,)).fetchone()[0]


def _add_parent(conn, title, platform="Switch", *, status="backlog", rating=None):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(models.clean_title(title))))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id) VALUES (?, ?)",
                 (gid, _platform_id(conn, platform)))
    conn.execute("INSERT INTO user_ratings (game_id, status, rating) VALUES (?, ?, ?)",
                 (gid, status, rating))
    conn.commit()
    return gid


def _titles(conn):
    return {r[0] for r in conn.execute("SELECT title FROM games")}


def _collection_of(conn, title):
    r = conn.execute("SELECT collection_name FROM games WHERE title=?", (title,)).fetchone()
    return r[0] if r else None


def test_compilation_breaks_out_sets_collection_and_deletes_parent(monkeypatch, temp_db):
    conn = models.get_db()
    _add_parent(conn, "Mega Man Legacy Collection")
    monkeypatch.setattr(models, "load_bundle_catalog", lambda: {
        "mega man legacy collection": {"type": "compilation",
                                       "constituents": ["Mega Man", "Mega Man 2"]}})
    imp.apply_bundle_catalog(conn)
    titles = _titles(conn)
    assert "Mega Man Legacy Collection" not in titles      # parent deleted
    assert "Mega Man" in titles and "Mega Man 2" in titles  # constituents created
    assert _collection_of(conn, "Mega Man") == "Mega Man Legacy Collection"
    assert _collection_of(conn, "Mega Man 2") == "Mega Man Legacy Collection"
    conn.close()


def test_entitlement_breaks_out_without_collection_name(monkeypatch, temp_db):
    conn = models.get_db()
    _add_parent(conn, "Pikmin 1plus2")
    monkeypatch.setattr(models, "load_bundle_catalog", lambda: {
        models.normalize_title(models.clean_title("Pikmin 1plus2")):
            {"type": "entitlement", "constituents": ["Pikmin 1", "Pikmin 2"]}})
    imp.apply_bundle_catalog(conn)
    titles = _titles(conn)
    assert "Pikmin 1plus2" not in titles
    assert "Pikmin 1" in titles and "Pikmin 2" in titles
    assert _collection_of(conn, "Pikmin 1") is None     # no badge for entitlement bundles
    assert _collection_of(conn, "Pikmin 2") is None
    conn.close()


def test_anthology_is_noop(monkeypatch, temp_db):
    conn = models.get_db()
    _add_parent(conn, "Atari 50")
    monkeypatch.setattr(models, "load_bundle_catalog", lambda: {
        "atari 50": {"type": "anthology", "constituents": []}})
    report = imp.apply_bundle_catalog(conn)
    assert "Atari 50" in _titles(conn)                  # parent kept
    assert any(r["type"] == "anthology" and r["action"] == "kept" for r in report)
    conn.close()


def test_missing_parent_is_skipped(monkeypatch, temp_db):
    conn = models.get_db()
    monkeypatch.setattr(models, "load_bundle_catalog", lambda: {
        "not owned collection": {"type": "compilation", "constituents": ["A", "B"]}})
    report = imp.apply_bundle_catalog(conn)
    assert report == []                                 # nothing owned -> nothing done
    assert _titles(conn) == set()
    conn.close()


def test_unknown_type_is_skipped(monkeypatch, temp_db):
    conn = models.get_db()
    _add_parent(conn, "Weird Pack")
    monkeypatch.setattr(models, "load_bundle_catalog", lambda: {
        "weird pack": {"type": "bogus", "constituents": ["A"]}})
    report = imp.apply_bundle_catalog(conn)
    assert report == []
    assert "Weird Pack" in _titles(conn)                # untouched
    conn.close()


def test_dry_run_writes_nothing(monkeypatch, temp_db):
    conn = models.get_db()
    _add_parent(conn, "Mega Man Legacy Collection")
    monkeypatch.setattr(models, "load_bundle_catalog", lambda: {
        "mega man legacy collection": {"type": "compilation",
                                       "constituents": ["Mega Man", "Mega Man 2"]}})
    imp.apply_bundle_catalog(conn, dry_run=True)
    assert "Mega Man Legacy Collection" in _titles(conn)  # nothing changed
    assert "Mega Man" not in _titles(conn)
    conn.close()


def test_idempotent_second_run_noop(monkeypatch, temp_db):
    conn = models.get_db()
    _add_parent(conn, "Mega Man Legacy Collection")
    monkeypatch.setattr(models, "load_bundle_catalog", lambda: {
        "mega man legacy collection": {"type": "compilation",
                                       "constituents": ["Mega Man", "Mega Man 2"]}})
    imp.apply_bundle_catalog(conn)
    before = _titles(conn)
    imp.apply_bundle_catalog(conn)  # parent already gone -> skipped
    assert _titles(conn) == before
    conn.close()


def test_ambiguous_curation_keeps_parent(monkeypatch, temp_db):
    conn = models.get_db()
    # A user rating on the parent is un-splittable -> parent kept (kept_ambiguous).
    _add_parent(conn, "Mega Man Legacy Collection", status="completed", rating=4)
    monkeypatch.setattr(models, "load_bundle_catalog", lambda: {
        "mega man legacy collection": {"type": "compilation",
                                       "constituents": ["Mega Man", "Mega Man 2"]}})
    report = imp.apply_bundle_catalog(conn)
    assert "Mega Man Legacy Collection" in _titles(conn)               # kept
    assert any(r["action"] == "kept_ambiguous" for r in report)
    assert "Mega Man" in _titles(conn)                                # constituents still made
    conn.close()
```

- [ ] **Step 2: Run to verify it fails** — `uv run python -m pytest tests/test_apply_bundle_catalog.py -v` — expect FAIL (`apply_bundle_catalog` undefined).

- [ ] **Step 3: Implement `apply_bundle_catalog`.** In `import_scraped.py`, directly after `cleanup_bundles` add:
```python
_VALID_BUNDLE_TYPES = ("compilation", "entitlement", "anthology")


def apply_bundle_catalog(conn: sqlite3.Connection, *, dry_run: bool = False) -> list[dict]:
    """Expand catalogued multi-game products that exist in the DB. Catalog-keyed
    sibling of cleanup_bundles (which is keyed by vendor id).

    For each entry in models.load_bundle_catalog() whose parent (matched by
    normalized_title) is owned:
      - anthology  -> no-op (kept; A2 handles its contents list);
      - compilation/entitlement -> ensure constituents exist on the parent's
        platform(s) (reusing the importer), migrate the parent's curation fill-only,
        then delete the parent (uncurated -> 'deleted'; curated+splittable ->
        'migrated_deleted'; un-splittable rating/notes/hours -> 'kept_ambiguous').
      - compilation -> additionally stamp collection_name = parent title on each
        constituent (the badge + 'Part of X' cue). entitlement constituents stay plain.
    Idempotent (a removed parent is skipped next run). Honors dry_run. Returns a report.
    """
    catalog = models.load_bundle_catalog()
    results: list[dict] = []
    for norm_title, entry in catalog.items():
        ptype = entry.get("type")
        if ptype not in _VALID_BUNDLE_TYPES:
            continue  # unknown type: never guessed
        parent = conn.execute(
            "SELECT id, title FROM games WHERE normalized_title = ?", (norm_title,)).fetchone()
        if not parent:
            continue
        parent_id, parent_title = parent["id"], parent["title"]
        if ptype == "anthology":
            results.append({"title": parent_title, "type": "anthology", "action": "kept",
                            "constituents_created": 0, "migrated": {}})
            continue

        constituents = tuple(entry.get("constituents") or ())
        platforms = [r[0] for r in conn.execute(
            "SELECT p.short_name FROM game_platforms gp JOIN platforms p ON p.id = gp.platform_id "
            "WHERE gp.game_id = ?", (parent_id,))] or [None]
        synth = [_constituent_game({"platform": pf}, title, "catalog")
                 for title in constituents for pf in platforms]
        stats = import_games(conn, synth, "catalog", dry_run=dry_run, confirm_fn=_safe_auto_confirm)

        ids = _resolve_constituent_ids(conn, constituents)
        if ptype == "compilation" and not dry_run:
            for cid in ids:
                conn.execute("UPDATE games SET collection_name = ? WHERE id = ?",
                             (parent_title, cid))

        curated = _is_curated(conn, parent_id)
        migrated: dict = {}
        if not curated:
            action = "deleted"
            if not dry_run:
                conn.execute("DELETE FROM games WHERE id = ?", (parent_id,))
        else:
            migrated = _migrate_bundle_curation(conn, parent_id, ids, dry_run=dry_run)
            if migrated["ambiguous"]:
                action = "kept_ambiguous"
            else:
                action = "migrated_deleted"
                if not dry_run:
                    conn.execute("DELETE FROM games WHERE id = ?", (parent_id,))
        results.append({"title": parent_title, "type": ptype, "action": action,
                        "constituents_created": stats.new_games, "migrated": migrated})
    if not dry_run:
        conn.commit()
    return results
```

- [ ] **Step 4: Run to verify pass** — `uv run python -m pytest tests/test_apply_bundle_catalog.py -v` — expect PASS (8).

- [ ] **Step 5: Full suite** — `uv run python -m pytest -q` — expect all pass. If any UNEXPECTED test is red, STOP and report.

- [ ] **Step 6: Lint** — `ruff check import_scraped.py tests/test_apply_bundle_catalog.py` — expect no errors.

- [ ] **Step 7: Commit**
```bash
git add import_scraped.py tests/test_apply_bundle_catalog.py
git commit -m "feat(bundles): apply_bundle_catalog expands compilations/entitlements, sets collection_name

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `--apply-bundle-catalog` CLI flag (dry-run preview)

Gives the controller a dry-run-first preview + apply, mirroring `--cleanup-bundles`.

**Files:** Modify `import_scraped.py` (the `main` argparse + dispatch); add a test to `tests/test_apply_bundle_catalog.py`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_apply_bundle_catalog.py`:
```python
def test_cli_apply_bundle_catalog_dry_run(monkeypatch, temp_db, capsys):
    # Point the global DB at the temp DB so main() operates on it.
    import models as m
    conn = models.get_db()
    _add_parent(conn, "Mega Man Legacy Collection")
    conn.close()
    monkeypatch.setattr(m, "load_bundle_catalog", lambda: {
        "mega man legacy collection": {"type": "compilation",
                                       "constituents": ["Mega Man", "Mega Man 2"]}})
    imp.main(["--apply-bundle-catalog", "--dry-run"])
    conn = models.get_db()
    titles = {r[0] for r in conn.execute("SELECT title FROM games")}
    assert "Mega Man Legacy Collection" in titles   # dry run wrote nothing
    assert "Mega Man" not in titles
    conn.close()
```
(`temp_db` monkeypatches `models.DB_PATH`, and `main` calls `models.migrate_db()` + `models.get_db()`, so it operates on the temp DB.)

- [ ] **Step 2: Run to verify it fails** — `uv run python -m pytest tests/test_apply_bundle_catalog.py::test_cli_apply_bundle_catalog_dry_run -v` — expect FAIL (unrecognized argument `--apply-bundle-catalog`).

- [ ] **Step 3: Add the flag + dispatch.** In `import_scraped.py` `main`, directly after the `--cleanup-bundles` argument definition add:
```python
    parser.add_argument("--apply-bundle-catalog", action="store_true",
                        help="expand catalogued compilations/entitlement bundles "
                             "(bundle_catalog.json) already in the DB, then exit")
```
Then, directly after the `if args.cleanup_bundles:` block (after its `return`), add:
```python
    if args.apply_bundle_catalog:
        report = apply_bundle_catalog(conn, dry_run=args.dry_run)
        for r in report:
            logger.info("%s: %s (+%d constituents) [%s]", r["title"], r["action"],
                        r["constituents_created"], r["type"])
        logger.info("DRY RUN — no changes written." if args.dry_run
                    else "bundle-catalog entries processed: %d" % len(report))
        conn.close()
        return
```

- [ ] **Step 4: Run to verify pass** — `uv run python -m pytest tests/test_apply_bundle_catalog.py::test_cli_apply_bundle_catalog_dry_run -v` — expect PASS.

- [ ] **Step 5: Lint** — `ruff check import_scraped.py tests/test_apply_bundle_catalog.py` — expect no errors.

- [ ] **Step 6: Commit**
```bash
git add import_scraped.py tests/test_apply_bundle_catalog.py
git commit -m "feat(bundles): --apply-bundle-catalog CLI flag (dry-run preview)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: API — surface `collection_name`

`GET /api/games/<id>` already returns it (`SELECT g.*`). The list + search endpoints select explicit columns and must add it so `gameCardHtml` can render the badge.

**Files:** Modify `app.py` (`api_games` SELECT ~line 79-94; `api_games_search` SELECT ~line 313 + payload ~line 328); Create `tests/test_api_games_collection.py`.

- [ ] **Step 1: Write the failing test** — `tests/test_api_games_collection.py`:
```python
import models


def _make_collection_game(client, title="Mega Man", collection="Mega Man Legacy Collection"):
    gid = client.post("/api/games", json={"title": title}).get_json()["game_id"]
    conn = models.get_db()
    conn.execute("UPDATE games SET collection_name = ? WHERE id = ?", (collection, gid))
    conn.commit()
    conn.close()
    return gid


def test_api_games_list_includes_collection_name(client):
    _make_collection_game(client)
    rows = client.get("/api/games").get_json()
    mm = next(r for r in rows if r["title"] == "Mega Man")
    assert mm["collection_name"] == "Mega Man Legacy Collection"


def test_api_games_search_includes_collection_name(client):
    _make_collection_game(client, title="Mega Man X")
    rows = client.get("/api/games/search?q=mega").get_json()
    mm = next(r for r in rows if r["title"] == "Mega Man X")
    assert mm["collection_name"] == "Mega Man Legacy Collection"


def test_api_game_detail_includes_collection_name(client):
    gid = _make_collection_game(client)
    g = client.get(f"/api/games/{gid}").get_json()
    assert g["collection_name"] == "Mega Man Legacy Collection"
```

- [ ] **Step 2: Run to verify it fails** — `uv run python -m pytest tests/test_api_games_collection.py -v` — expect FAIL on the list + search tests (`KeyError: 'collection_name'`); the detail test already passes (`SELECT g.*`).

- [ ] **Step 3: Add `collection_name` to the list SELECT.** In `app.py` `api_games`, in the big `SELECT DISTINCT` column list, add `g.collection_name,` directly after `g.cover_url,`:
```python
            g.id,
            g.title,
            g.cover_url,
            g.collection_name,
            g.metacritic_score,
```

- [ ] **Step 4: Add `collection_name` to the search SELECT + payload.** In `app.py` `api_games_search`, change the SELECT (currently `SELECT id, title, cover_url FROM games ...`) to include the column:
```python
    rows = conn.execute(
        "SELECT id, title, cover_url, collection_name FROM games "
        "WHERE title LIKE ? COLLATE NOCASE OR normalized_title LIKE ? COLLATE NOCASE "
        "ORDER BY title COLLATE NOCASE LIMIT 10",
        (like, like)).fetchall()
```
and add it to the returned dicts (the `jsonify([...])` comprehension):
```python
    return jsonify([
        {"id": r["id"], "title": r["title"], "cover_url": r["cover_url"],
         "collection_name": r["collection_name"],
         "platforms": plat_by_game.get(r["id"], [])}
        for r in rows
    ])
```

- [ ] **Step 5: Run to verify pass** — `uv run python -m pytest tests/test_api_games_collection.py -v` — expect PASS (3).

- [ ] **Step 6: Lint** — `ruff check app.py tests/test_api_games_collection.py` — expect no errors.

- [ ] **Step 7: Commit**
```bash
git add app.py tests/test_api_games_collection.py
git commit -m "feat(api): surface collection_name in games list + search

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: UI — tile badge + modal "Part of …" line

The stacked-cards badge on the tile (when `collection_name` set) + the detail-view launch cue. Render-smoke test + controller live-verify.

**Files:** Modify `templates/base.html` (`gameCardHtml` ~line 302-305; `loadGameModal` header ~line 879); add a render-smoke test to `tests/test_api_games_collection.py`.

- [ ] **Step 1: Write a render-smoke test** — append to `tests/test_api_games_collection.py`:
```python
def test_tile_and_modal_have_collection_markup():
    with open("templates/base.html", encoding="utf-8") as f:
        html = f.read()
    assert "collection-badge" in html          # tile badge
    assert "game.collection_name" in html       # gated on the field
    assert "Part of" in html                     # modal launch cue
```

- [ ] **Step 2: Run to verify it fails** — `uv run python -m pytest tests/test_api_games_collection.py::test_tile_and_modal_have_collection_markup -v` — expect FAIL (markers absent).

- [ ] **Step 3: Add the tile badge to `gameCardHtml`.** In `templates/base.html`, change the cover line in `gameCardHtml` (currently `<div class="aspect-[3/4] relative overflow-hidden">${cover}</div>`) to overlay the badge when `collection_name` is set:
```javascript
            const collectionBadge = game.collection_name
                ? `<span class="collection-badge" title="Part of ${escapeHtml(game.collection_name)}"
                         style="position:absolute;top:6px;right:6px;display:flex;align-items:center;justify-content:center;width:26px;height:26px;background:rgba(10,15,24,.6);border-radius:7px;">
                     <svg width="18" height="18" viewBox="0 0 22 22" fill="none">
                       <rect x="2.5" y="6" width="10" height="13" rx="2" fill="#1b2230" stroke="#cdd9ec" stroke-width="1.3" transform="rotate(-11 7.5 12.5)" opacity="0.65"/>
                       <rect x="6" y="4.5" width="10" height="13" rx="2" fill="#1b2230" stroke="#cdd9ec" stroke-width="1.3"/>
                       <rect x="9.5" y="6" width="10" height="13" rx="2" fill="#2563eb" stroke="#7fb0ff" stroke-width="1.3" transform="rotate(11 14.5 12.5)"/>
                     </svg></span>`
                : '';
            return `<div class="game-card bg-surface-light rounded-lg overflow-hidden cursor-pointer" data-game-id="${game.id}" onclick="openModal(${game.id})">
                <div class="aspect-[3/4] relative overflow-hidden">${cover}${collectionBadge}</div>
                <div class="p-3"><h3 class="font-medium text-sm text-white leading-tight line-clamp-2 min-h-[2.5rem]" title="${escapeHtml(game.title)}">${escapeHtml(game.title)}</h3>
                    <div class="mt-2 flex flex-wrap items-center gap-1">${badges}</div></div></div>`;
```
(Define `collectionBadge` just above the existing `return` inside `gameCardHtml`, then add `${collectionBadge}` inside the `relative` cover div as shown. The cover div is already `position:relative`, so the absolutely-positioned badge anchors to it.)

- [ ] **Step 4: Add the modal "Part of …" line.** In `templates/base.html` `loadGameModal`, directly after the opening of the header detail column `<div class="flex-1 min-w-0">` (the line before `<div class="mt-2 flex flex-wrap gap-2">`), insert:
```javascript
                            ${game.collection_name ? `
                            <div class="text-xs text-accent mb-1 flex items-center gap-1.5">
                                <svg width="13" height="13" viewBox="0 0 22 22" fill="none">
                                  <rect x="2.5" y="6" width="10" height="13" rx="2" fill="none" stroke="currentColor" stroke-width="1.4" transform="rotate(-11 7.5 12.5)" opacity="0.6"/>
                                  <rect x="6" y="4.5" width="10" height="13" rx="2" fill="none" stroke="currentColor" stroke-width="1.4"/>
                                  <rect x="9.5" y="6" width="10" height="13" rx="2" fill="none" stroke="currentColor" stroke-width="1.4" transform="rotate(11 14.5 12.5)"/>
                                </svg>
                                Part of <span class="font-medium">${escapeHtml(game.collection_name)}</span>
                            </div>` : ''}
```

- [ ] **Step 5: Run the render test to verify it passes** — `uv run python -m pytest tests/test_api_games_collection.py::test_tile_and_modal_have_collection_markup -v` — expect PASS.

- [ ] **Step 6: Full suite** — `uv run python -m pytest -q` — expect all pass.

- [ ] **Step 7: Lint** — `ruff check tests/test_api_games_collection.py` — expect no errors.

- [ ] **Step 8: Commit**
```bash
git add templates/base.html tests/test_api_games_collection.py
git commit -m "feat(ui): collection badge on tiles + 'Part of <collection>' in the modal

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 9: CONTROLLER live-verify** — controller stops the app, relaunches, and (via a temporary `collection_name` set on one real game, reverted after) confirms in a browser: the stacked-cards badge renders top-right on that game's tile, and its modal shows the "Part of <collection>" line. Subagents do NOT do this.

---

## Task 7: Full-suite green + ruff gate (integration checkpoint)

**Files:** none (verification only).

- [ ] **Step 1: Whole suite** — `uv run python -m pytest` — expect PASS (prior count + the new A1 tests). Debug any red with superpowers:systematic-debugging before proceeding.
- [ ] **Step 2: Lint the tree** — `ruff check .` — expect no errors. (NEVER `ruff format`.)
- [ ] **Step 3: Controller confirms the live app** — relaunch; spot-check the library renders normally (no collection games yet, since the committed catalog is empty — the badge only appears once the catalog is populated + applied).

---

## The AI catalog-draft (CONTROLLER-run operation — NOT a subagent TDD task)

Run after Tasks 1-6 land. Produces `bundle_catalog.default.json` for the owner's review/PR; **does not auto-apply** anything destructive.

**Pre-flight (controller):** stop the app. (No DB write in the draft step.) Build the candidate work-list from the live DB (read-only): all games (the agent decides type), or a heuristic-filtered subset whose titles contain collection-ish tokens (`collection|compilation|trilogy|anthology|legacy|bundle|remaster|edition|\+|&|/`). Gather `{id, normalized_title, title, platforms}`.

**Workflow shape (~20 games/agent):** each agent receives a batch and returns, per product, `{normalized_title, type: compilation|entitlement|anthology|standalone, constituents: [titles], reason}` via a StructuredOutput schema. Tell agents: a `compilation` is one launcher app you open then pick a game (Mega Man/Castlevania Legacy/Anniversary, Ezio, BioShock Collection, .Hack G.U., Chrono Cross + Radical Dreamers); an `entitlement` grants separately-launched games (Pikmin 1+2, Portal Companion, Borderlands pack); an `anthology` is a museum of many micro-games (Atari 50, UFO 50, Rare Replay extras); a single game + DLC/edition naming (BioShock Infinite: Complete Edition, Bulletstorm: Full Clip Edition) is `standalone`. List each compilation/entitlement's constituent full-game titles. Do NOT touch any database.

**Controller post-processing (the only writer):**
1. Match each verdict to the authoritative game by **`normalized_title`** (NEVER trust a returned id — the trait pilot proved transposition happens).
2. Drop `standalone`; merge `compilation`/`entitlement`/`anthology` into `bundle_catalog.default.json` (sorted by key) as `{type, constituents}`.
3. Log counts + low-confidence items + any title whose constituents don't resolve to/near existing rows (so the owner can spot hallucinated constituents).
The owner reviews/edits the emitted catalog, then the controller runs `--apply-bundle-catalog --dry-run` (preview), backs up `games.db`, and runs `--apply-bundle-catalog` for real. Re-runnable (idempotent).

---

## Self-Review (completed by plan author)

**Spec coverage:**
- `bundle_catalog.default.json` + per-user override + `load_bundle_catalog` (mirrors traits/series) → Task 1. ✓
- Three product types with per-type handling (compilation breaks-out+collection_name+delete; entitlement breaks-out plain+delete; anthology no-op/kept) → Task 3. ✓
- `games.collection_name` identity column → Task 2. ✓
- Reuse existing `import_scraped` machinery (import_games, _constituent_game, _resolve_constituent_ids, _migrate_bundle_curation, _is_curated) + ambiguity-safe parent deletion → Task 3. ✓
- Gated, dry-run-first, idempotent, NOT in migrate_db → Task 3 (function) + Task 4 (CLI dry-run). ✓
- Surface collection_name in API (list/search add it; detail free via g.*) → Task 5. ✓
- Tile stacked-cards badge + modal "Part of …" cue, platform icons unchanged → Task 6. ✓
- AI draft Workflow (returns verdicts, controller writes catalog, match by normalized_title) → seed-op section. ✓
- Error handling (missing/malformed catalog → {}; missing parent skipped; unknown type skipped) → Tasks 1 + 3. ✓
- A2 (anthology contents-list + promote) explicitly deferred → not in this plan. ✓

**Placeholder scan:** none — every code step has complete code; the AI-draft section is a controller runbook (mirrors the accepted trait-seed section), not a TDD task with placeholders.

**Type/name consistency:** `load_bundle_catalog`, `BUNDLE_CATALOG_PATH/_DEFAULT_PATH`, `migrate_collection_name`, `games.collection_name`, `apply_bundle_catalog(conn, *, dry_run=False)`, type values `compilation|entitlement|anthology`, report keys `title/type/action/constituents_created/migrated`, actions `deleted|migrated_deleted|kept_ambiguous|kept` — all used consistently across tasks and tests.
