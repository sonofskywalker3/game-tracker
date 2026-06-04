# Conservative score-delta IGDB re-audit — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `audit_igdb_matches` so it flags a game only when a *different* IGDB candidate genuinely beats the currently-stored entry (by quality score, with bundle-source treated as authoritative), killing the `.webp`/`.jpg` and re-search-drift false positives that made the first live run flag 178/766 games.

**Architecture:** Compare match *quality*, not cover URLs. A new `_cover_stem` collapses cosmetic URL differences; a new `fetch_entry` lets the audit score the stored IGDB entry with the existing `score_candidates`; the rewritten audit flags on bundle authority, score-delta, or a strong unmatched candidate, and records a human reason string in a new `games.igdb_review_reason` column surfaced in the UI.

**Tech Stack:** Python 3.11, `requests` (IGDB v4 via `igdb_dlc._igdb_query`), Flask, sqlite3, `uv`, `pytest`, vanilla-JS/Jinja templates.

**Spec:** `docs/superpowers/specs/2026-06-03-igdb-audit-score-delta-design.md`

---

## File Structure

- **Modify:** `igdb_match.py` — add `_cover_stem`, `fetch_entry`, constants `_REVIEW_MARGIN`/`_STRONG_MATCH`, and rewrite `audit_igdb_matches` (+ helpers `_score_entry`, `_flag_reason`).
- **Modify:** `models.py` — add `migrate_igdb_review_reason`, register in `migrate_db`.
- **Modify:** `tests/conftest.py` — mirror the new migration.
- **Modify:** `app.py` — clear `igdb_review_reason` on `igdb-pick` + `igdb-pin`; surface it in the `/api/games` row.
- **Modify:** `templates/base.html` — render a reason chip in `gameCardHtml` for flagged games.
- **Tests:** `tests/test_igdb_match.py` (stem, fetch_entry, migration, rewritten audit suite — **replaces** the old `test_audit_flags_disagreement_skips_locked_and_agreeing`), `tests/test_api_games.py` (reason cleared on pick + surfaced in list).

## Conventions (owner rules — non-negotiable)

- Tests: `uv run python -m pytest` (plain `uv run pytest` fails: ModuleNotFoundError: models).
- Lint: `uv run ruff check .` ONLY — never `ruff format`.
- Subagents (Tasks 1–6 code): pytest temp DB + monkeypatched IGDB only. **Never** run the app, touch the live `games.db`, make a live IGDB call, or `git push`.
- Work directly on `main` (no branch). End every commit message with the trailer:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- The live re-audit over the real library is a **controller** operation (done after this plan lands), not a subagent task.

## Reference: existing `igdb_match.py` symbols to reuse (do not reimplement)

- `score_candidates(candidates, *, game_platform_ids, title=None)` → ranked list; each result dict carries `_score` (int) and `_mobile_only` (bool). Drops entries that don't title-match when `title` is given.
- `candidates_for(title, game_platform_ids, collection_name, client_id, token)` → ranked identity candidates, **bundle-first**. Each: `{igdb_id, name, cover_url, platforms, source ("bundle"|"search"), score?}`.
- `platform_ids_for(short_names)` → `set[int]`.
- Constants already present: `_TITLE_EXACT=100`, `_PLATFORM_OVERLAP=50`, `_MOBILE_PENALTY=-80`, `_HAS_COVER=10`, `MOBILE_PLATFORM_IDS`.
- IGDB access: `igdb_dlc._igdb_query(query, client_id, token)` (monkeypatch this in tests).

---

## Task 1: `_cover_stem` — collapse cosmetic cover-URL differences

**Files:**
- Modify: `igdb_match.py`
- Test: `tests/test_igdb_match.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_igdb_match.py`)

```python
def test_cover_stem_ignores_size_and_extension():
    base = "https://images.igdb.com/igdb/image/upload/"
    assert igdb_match._cover_stem(base + "t_thumb/co1zyu.jpg") == "co1zyu"
    assert igdb_match._cover_stem(base + "t_cover_big/co1zyu.webp") == "co1zyu"
    assert igdb_match._cover_stem("//x/t_cover_big/co1zyu.png") == "co1zyu"
    assert igdb_match._cover_stem(None) is None
    assert igdb_match._cover_stem("") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_igdb_match.py::test_cover_stem_ignores_size_and_extension -v`
Expected: FAIL (`AttributeError: module 'igdb_match' has no attribute '_cover_stem'`).

- [ ] **Step 3: Write minimal implementation** (append to `igdb_match.py`, after `cover_url_of`)

```python
def _cover_stem(url: str | None) -> str | None:
    """The IGDB image id from a cover URL, ignoring size token + extension, so
    cosmetic differences (.webp vs .jpg, t_thumb vs t_cover_big) collapse to the
    same value. '.../t_cover_big/co1zyu.jpg' -> 'co1zyu'. None if no usable id."""
    if not url:
        return None
    last = url.rsplit("/", 1)[-1]      # 'co1zyu.jpg'
    stem = last.rsplit(".", 1)[0]      # 'co1zyu'
    return stem or None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_igdb_match.py::test_cover_stem_ignores_size_and_extension -v`
Expected: PASS.

- [ ] **Step 5: Run ruff**

Run: `uv run ruff check igdb_match.py tests/test_igdb_match.py`
Expected: All checks passed.

- [ ] **Step 6: Commit**

```bash
git add igdb_match.py tests/test_igdb_match.py
git commit -m "feat(igdb): _cover_stem collapses cosmetic cover-url differences

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `fetch_entry` — fetch one IGDB entry by id for scoring

**Files:**
- Modify: `igdb_match.py`
- Test: `tests/test_igdb_match.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_igdb_match.py`)

```python
def test_fetch_entry_queries_by_id(monkeypatch):
    seen = {}
    def fake(query, cid, tok):
        seen["q"] = query
        return [{"id": 1711, "name": "Mega Man 2", "platforms": [18],
                 "cover": {"url": "//x/t_thumb/2.jpg"}, "total_rating_count": 80}]
    monkeypatch.setattr(igdb_match.igdb_dlc, "_igdb_query", fake)
    entry = igdb_match.fetch_entry(1711, "c", "t")
    assert "where id = (1711)" in seen["q"]
    assert "platforms" in seen["q"]
    assert entry["name"] == "Mega Man 2"


def test_fetch_entry_returns_none_when_missing(monkeypatch):
    monkeypatch.setattr(igdb_match.igdb_dlc, "_igdb_query", lambda *a, **k: [])
    assert igdb_match.fetch_entry(999, "c", "t") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_igdb_match.py::test_fetch_entry_queries_by_id tests/test_igdb_match.py::test_fetch_entry_returns_none_when_missing -v`
Expected: FAIL (`AttributeError: ... 'fetch_entry'`).

- [ ] **Step 3: Write minimal implementation** (append to `igdb_match.py`, after `fetch_candidates`)

```python
def fetch_entry(igdb_id: int, client_id: str, token: str) -> dict | None:
    """Fetch one IGDB entry by id with the fields the scorer needs. Returns the
    raw IGDB dict (name, cover.url, platforms, ...) or None if the id is gone."""
    query = (
        "fields name, cover.url, platforms, first_release_date, "
        "total_rating_count, game_type; "
        f"where id = ({int(igdb_id)}); limit 1;"
    )
    rows = igdb_dlc._igdb_query(query, client_id, token) or []
    return rows[0] if rows else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_igdb_match.py -v`
Expected: PASS.

- [ ] **Step 5: Run ruff**

Run: `uv run ruff check igdb_match.py tests/test_igdb_match.py`
Expected: All checks passed.

- [ ] **Step 6: Commit**

```bash
git add igdb_match.py tests/test_igdb_match.py
git commit -m "feat(igdb): fetch_entry (fetch one IGDB entry by id for scoring)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `migrate_igdb_review_reason` column

Must land **before** Task 4 so the `temp_db` fixture has the column the rewritten audit writes.

**Files:**
- Modify: `models.py` (new `migrate_igdb_review_reason` after `migrate_igdb_review` ~762; register in `migrate_db` after `migrate_igdb_review(conn)` ~859)
- Modify: `tests/conftest.py` (mirror after `models.migrate_igdb_review(conn)` ~25)
- Test: `tests/test_igdb_match.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_igdb_match.py`)

```python
def test_migrate_igdb_review_reason_adds_column(temp_db):
    cols = [c[1] for c in temp_db.execute("PRAGMA table_info(games)")]
    assert "igdb_review_reason" in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_igdb_match.py::test_migrate_igdb_review_reason_adds_column -v`
Expected: FAIL (column missing — conftest doesn't create it yet).

- [ ] **Step 3: Write minimal implementation**

Add to `models.py` immediately after `migrate_igdb_review` (the function ending ~line 761):

```python
def migrate_igdb_review_reason(conn: sqlite3.Connection) -> None:
    """Add games.igdb_review_reason (TEXT, nullable). Idempotent. Holds a short
    human reason the audit flagged a game (e.g. 'bundle', 'mobile->console'),
    surfaced in the Needs-review UI. Cleared whenever needs_igdb_review is cleared."""
    cols = [c[1] for c in conn.execute("PRAGMA table_info(games)").fetchall()]
    if "igdb_review_reason" not in cols:
        conn.execute("ALTER TABLE games ADD COLUMN igdb_review_reason TEXT")
    conn.commit()
```

Register it in `migrate_db` immediately after `migrate_igdb_review(conn)`:

```python
    migrate_igdb_review(conn)
    migrate_igdb_review_reason(conn)
```

Mirror in `tests/conftest.py` immediately after `models.migrate_igdb_review(conn)`:

```python
    models.migrate_igdb_review(conn)
    models.migrate_igdb_review_reason(conn)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_igdb_match.py::test_migrate_igdb_review_reason_adds_column -v`
Expected: PASS.

- [ ] **Step 5: Run full suite + ruff**

Run: `uv run python -m pytest -q && uv run ruff check .`
Expected: all green, All checks passed.

- [ ] **Step 6: Commit**

```bash
git add models.py tests/conftest.py tests/test_igdb_match.py
git commit -m "feat(db): migrate_igdb_review_reason (games.igdb_review_reason)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Rewrite `audit_igdb_matches` (score-delta + reasons)

**Files:**
- Modify: `igdb_match.py` (replace `audit_igdb_matches` ~206-227; add `_REVIEW_MARGIN`, `_STRONG_MATCH`, `_score_entry`, `_flag_reason`)
- Test: `tests/test_igdb_match.py` (**delete** `test_audit_flags_disagreement_skips_locked_and_agreeing`; add the new suite below)

This is the core task. The new audit, per non-locked game: gets the bundle-first best candidate via `candidates_for`, scores both it and the stored entry (`fetch_entry`) with `score_candidates`, and flags on bundle authority / score-delta / strong-unmatched. Flag-only — never writes `cover_url`/`igdb_id`.

- [ ] **Step 1: Delete the obsolete test**

Remove `test_audit_flags_disagreement_skips_locked_and_agreeing` from `tests/test_igdb_match.py` entirely (it monkeypatches `resolve_identity`, which the new audit no longer calls).

- [ ] **Step 2: Write the failing tests** (append to `tests/test_igdb_match.py`)

```python
# --- rewritten audit: score-delta + reasons --------------------------------

_BASE = "https://images.igdb.com/igdb/image/upload/"


def _add_platform(conn, game_id, short_name):
    """Link a game to a platform by short_name (platform_ids_for maps short_name ->
    IGDB id via igdb_match.IGDB_PLATFORM_IDS, so no IGDB id is stored here).
    platforms.name is NOT NULL UNIQUE, so supply it too."""
    conn.execute("INSERT OR IGNORE INTO platforms (name, short_name) VALUES (?, ?)",
                 (short_name, short_name))
    pid = conn.execute("SELECT id FROM platforms WHERE short_name=?", (short_name,)).fetchone()[0]
    conn.execute("INSERT OR IGNORE INTO game_platforms (game_id, platform_id) VALUES (?, ?)",
                 (game_id, pid))
    conn.commit()


def test_audit_skips_cosmetic_webp_jpg(temp_db, monkeypatch):
    conn = temp_db
    conn.execute("INSERT INTO games (id,title,normalized_title,cover_url,igdb_id) "
                 "VALUES (1,'Hades','hades',?,100)", (_BASE + "t_cover_big/co1zyu.webp",))
    conn.commit()
    monkeypatch.setattr(igdb_match, "candidates_for", lambda *a, **k: [
        {"igdb_id": 100, "name": "Hades", "cover_url": _BASE + "t_cover_big/co1zyu.jpg",
         "platforms": [6], "source": "search", "score": 110}])
    monkeypatch.setattr(igdb_match, "fetch_entry", lambda *a, **k: {
        "id": 100, "name": "Hades", "platforms": [6], "cover": {"url": _BASE + "t_thumb/co1zyu.jpg"}})
    assert igdb_match.audit_igdb_matches(conn, client_id="c", token="t") == []
    assert conn.execute("SELECT needs_igdb_review FROM games WHERE id=1").fetchone()[0] == 0


def test_audit_flags_mobile_to_console(temp_db, monkeypatch):
    conn = temp_db
    conn.execute("INSERT INTO games (id,title,normalized_title,cover_url,igdb_id) "
                 "VALUES (2,'Y','y',?,200)", (_BASE + "t_cover_big/mob.jpg",))
    conn.commit()
    monkeypatch.setattr(igdb_match, "candidates_for", lambda *a, **k: [
        {"igdb_id": 201, "name": "Y", "cover_url": _BASE + "t_cover_big/con.jpg",
         "platforms": [48], "source": "search"}])
    monkeypatch.setattr(igdb_match, "fetch_entry", lambda *a, **k: {
        "id": 200, "name": "Y", "platforms": [igdb_match.IOS_ID],
        "cover": {"url": _BASE + "t_thumb/mob.jpg"}})
    flagged = igdb_match.audit_igdb_matches(conn, client_id="c", token="t")
    assert flagged == [2]
    row = conn.execute("SELECT needs_igdb_review, igdb_review_reason FROM games WHERE id=2").fetchone()
    assert row[0] == 1 and row[1] == "mobile->console"
    # never mutates cover/igdb_id
    assert conn.execute("SELECT cover_url FROM games WHERE id=2").fetchone()[0].endswith("mob.jpg")


def test_audit_not_flagged_when_stored_scores_higher(temp_db, monkeypatch):
    conn = temp_db
    conn.execute("INSERT INTO games (id,title,normalized_title,cover_url,igdb_id) "
                 "VALUES (3,'Portal','portal',?,300)", (_BASE + "t_cover_big/switch.jpg",))
    conn.commit()
    _add_platform(conn, 3, "Switch")
    # search best is the PC original (no Switch overlap) -> scores lower than stored
    monkeypatch.setattr(igdb_match, "candidates_for", lambda *a, **k: [
        {"igdb_id": 71, "name": "Portal", "cover_url": _BASE + "t_cover_big/pcorig.jpg",
         "platforms": [6], "source": "search"}])
    monkeypatch.setattr(igdb_match, "fetch_entry", lambda *a, **k: {
        "id": 300, "name": "Portal", "platforms": [130],
        "cover": {"url": _BASE + "t_thumb/switch.jpg"}})
    assert igdb_match.audit_igdb_matches(conn, client_id="c", token="t") == []


def test_audit_flags_bundle_authoritative(temp_db, monkeypatch):
    conn = temp_db
    conn.execute("INSERT INTO games (id,title,normalized_title,cover_url,collection_name,igdb_id) "
                 "VALUES (4,'Mega Man X','mega man x',?,'MM X LC',400)",
                 (_BASE + "t_cover_big/wrong.jpg",))
    conn.commit()
    _add_platform(conn, 4, "Switch")
    # bundle constituent is NES (no Switch overlap) -> would TIE on score, but
    # bundle source is authoritative so a different stem must still flag.
    monkeypatch.setattr(igdb_match, "candidates_for", lambda *a, **k: [
        {"igdb_id": 1741, "name": "Mega Man X", "cover_url": _BASE + "t_cover_big/right.jpg",
         "platforms": [18], "source": "bundle"}])
    monkeypatch.setattr(igdb_match, "fetch_entry", lambda *a, **k: {
        "id": 400, "name": "Mega Man X", "platforms": [18],
        "cover": {"url": _BASE + "t_thumb/wrong.jpg"}})
    flagged = igdb_match.audit_igdb_matches(conn, client_id="c", token="t")
    assert flagged == [4]
    assert conn.execute("SELECT igdb_review_reason FROM games WHERE id=4").fetchone()[0] == "bundle"


def test_audit_unmatched_strong_candidate_flags(temp_db, monkeypatch):
    conn = temp_db
    conn.execute("INSERT INTO games (id,title,normalized_title,cover_url,igdb_id) "
                 "VALUES (5,'Celeste','celeste',?,NULL)", (_BASE + "t_cover_big/old.jpg",))
    conn.commit()
    _add_platform(conn, 5, "Steam")
    monkeypatch.setattr(igdb_match, "candidates_for", lambda *a, **k: [
        {"igdb_id": 26226, "name": "Celeste", "cover_url": _BASE + "t_cover_big/celeste.jpg",
         "platforms": [6], "source": "search"}])
    # no stored igdb_id -> fetch_entry never called; guard with an assert
    monkeypatch.setattr(igdb_match, "fetch_entry",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no stored id")))
    flagged = igdb_match.audit_igdb_matches(conn, client_id="c", token="t")
    assert flagged == [5]
    assert conn.execute("SELECT igdb_review_reason FROM games WHERE id=5").fetchone()[0] == "unmatched->match"


def test_audit_unmatched_weak_candidate_not_flagged(temp_db, monkeypatch):
    conn = temp_db
    conn.execute("INSERT INTO games (id,title,normalized_title,cover_url,igdb_id) "
                 "VALUES (6,'Celeste','celeste',?,NULL)", (_BASE + "t_cover_big/old.jpg",))
    conn.commit()
    _add_platform(conn, 6, "Switch")
    # candidate has no Switch overlap -> not a strong match -> not flagged
    monkeypatch.setattr(igdb_match, "candidates_for", lambda *a, **k: [
        {"igdb_id": 26226, "name": "Celeste", "cover_url": _BASE + "t_cover_big/celeste.jpg",
         "platforms": [6], "source": "search"}])
    monkeypatch.setattr(igdb_match, "fetch_entry", lambda *a, **k: None)
    assert igdb_match.audit_igdb_matches(conn, client_id="c", token="t") == []


def test_audit_skips_locked(temp_db, monkeypatch):
    conn = temp_db
    conn.execute("INSERT INTO games (id,title,normalized_title,cover_url,igdb_id,igdb_locked) "
                 "VALUES (7,'Locked','locked',?,700,1)", (_BASE + "t_cover_big/x.jpg",))
    conn.commit()
    monkeypatch.setattr(igdb_match, "candidates_for",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("locked must be skipped")))
    assert igdb_match.audit_igdb_matches(conn, client_id="c", token="t") == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_igdb_match.py -k audit -v`
Expected: FAIL (new audit signature/behavior not implemented; reason column writes missing).

- [ ] **Step 4: Write the implementation**

Add the constants near the other module constants in `igdb_match.py` (after `_HAS_COVER`/`_BUNDLE_GAME_TYPE`):

```python
_REVIEW_MARGIN = 1                              # flag when best beats stored by >= this
_STRONG_MATCH = _TITLE_EXACT + _PLATFORM_OVERLAP  # bar for flagging an unmatched game
```

Replace the entire existing `audit_igdb_matches` function (~206-227) with:

```python
def _score_entry(entry: dict | None, *, game_platform_ids: set[int],
                 title: str) -> dict | None:
    """Score one IGDB entry dict with score_candidates. Returns the scored dict
    (carrying _score / _mobile_only) or None if it is falsy or fails title match."""
    if not entry:
        return None
    scored = score_candidates([entry], game_platform_ids=game_platform_ids, title=title)
    return scored[0] if scored else None


def _flag_reason(best: dict, best_scored: dict, stored_entry: dict | None,
                 stored_scored: dict | None, game_platform_ids: set[int]) -> str:
    """Short human reason a game was flagged, by why the candidate won."""
    if best.get("source") == "bundle":
        return "bundle"
    if stored_scored is None:
        return "unmatched->match"
    if stored_scored.get("_mobile_only") and not best_scored.get("_mobile_only"):
        return "mobile->console"
    best_overlap = bool(set(best.get("platforms") or []) & game_platform_ids)
    stored_overlap = bool(set((stored_entry or {}).get("platforms") or []) & game_platform_ids)
    if best_overlap and not stored_overlap:
        return "better platform match"
    return "stronger match"


def audit_igdb_matches(conn, *, client_id: str, token: str) -> list[int]:
    """Flag (needs_igdb_review=1 + igdb_review_reason) every non-locked game whose
    best IGDB candidate genuinely beats the currently-stored entry. Bundle-source
    candidates are authoritative (flag on any cover-stem difference); search-source
    candidates flag on a positive quality score-delta; games with no scorable stored
    entry flag only on a strong candidate. Flag-only: never mutates cover_url/igdb_id.
    Returns the list of flagged game ids."""
    rows = conn.execute(
        "SELECT id, title, cover_url, collection_name, igdb_id FROM games "
        "WHERE COALESCE(igdb_locked, 0) = 0 ORDER BY title").fetchall()
    flagged: list[int] = []
    for r in rows:
        plat_short = [x[0] for x in conn.execute(
            "SELECT p.short_name FROM game_platforms gp JOIN platforms p "
            "ON p.id = gp.platform_id WHERE gp.game_id = ?", (r["id"],))]
        gpi = platform_ids_for(plat_short)
        cands = candidates_for(r["title"], gpi, r["collection_name"], client_id, token)
        if not cands:
            continue
        best = cands[0]
        best_cover = best.get("cover_url")
        if not best_cover:
            continue
        # cosmetic: same image (ignoring size/extension) -> never flag
        best_stem, stored_stem = _cover_stem(best_cover), _cover_stem(r["cover_url"])
        if best_stem and stored_stem and best_stem == stored_stem:
            continue

        best_min = {"name": best.get("name"), "platforms": best.get("platforms") or [],
                    "cover": {"url": best_cover}}
        best_scored = _score_entry(best_min, game_platform_ids=gpi, title=r["title"])
        if best_scored is None:                       # defensive; best title-matched upstream
            continue

        stored_entry = fetch_entry(r["igdb_id"], client_id, token) if r["igdb_id"] else None
        stored_scored = _score_entry(stored_entry, game_platform_ids=gpi, title=r["title"])

        if best.get("source") == "bundle":
            should_flag = True                        # reverse-bundle lookup is authoritative
        elif stored_scored is not None:
            should_flag = best_scored["_score"] - stored_scored["_score"] >= _REVIEW_MARGIN
        else:                                         # no scorable stored entry
            should_flag = (best_scored["_score"] >= _STRONG_MATCH
                           and not best_scored.get("_mobile_only"))

        if should_flag:
            reason = _flag_reason(best, best_scored, stored_entry, stored_scored, gpi)
            conn.execute(
                "UPDATE games SET needs_igdb_review = 1, igdb_review_reason = ? WHERE id = ?",
                (reason, r["id"]))
            flagged.append(r["id"])
    conn.commit()
    return flagged
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_igdb_match.py -v`
Expected: PASS (all audit tests + earlier tasks).

- [ ] **Step 6: Run full suite + ruff**

Run: `uv run python -m pytest -q && uv run ruff check .`
Expected: all green, All checks passed.

- [ ] **Step 7: Commit**

```bash
git add igdb_match.py tests/test_igdb_match.py
git commit -m "feat(igdb): score-delta audit with bundle authority + reasons

Replaces flag-on-cover-URL-difference. Scores the stored entry vs the
bundle-first best candidate; bundle source is authoritative on stem
difference, search source flags on score-delta, unmatched needs a strong
candidate. Records igdb_review_reason. Flag-only.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Clear reason on pick/pin; surface in `/api/games`

**Files:**
- Modify: `app.py` (pin clear ~599; pick clear ~654; list query ~89 + serialization ~179)
- Test: `tests/test_api_games.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_api_games.py`)

```python
def test_igdb_pick_clears_review_reason_and_list_surfaces_it(client, temp_db):
    # a flagged game with a reason
    temp_db.execute(
        "INSERT INTO games (id,title,normalized_title,needs_igdb_review,igdb_review_reason) "
        "VALUES (1,'Mega Man X','mega man x',1,'bundle')")
    temp_db.commit()

    # the list surfaces the reason
    r = client.get('/api/games')
    assert r.status_code == 200
    game = next(g for g in r.get_json() if g['id'] == 1)
    assert game['needs_igdb_review'] is True
    assert game['igdb_review_reason'] == 'bundle'

    # picking an identity clears flag + reason
    r = client.post('/api/games/1/igdb-pick',
                    json={'igdb_id': 1741, 'cover_url': 'https://x/t_cover_big/r.jpg'})
    assert r.status_code == 200
    row = temp_db.execute(
        "SELECT needs_igdb_review, igdb_review_reason FROM games WHERE id=1").fetchone()
    assert row['needs_igdb_review'] == 0 and row['igdb_review_reason'] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_api_games.py::test_igdb_pick_clears_review_reason_and_list_surfaces_it -v`
Expected: FAIL (`KeyError: 'igdb_review_reason'` in list; reason not cleared on pick).

- [ ] **Step 3: Write the implementation**

In `app.py`, the `/api/games` SELECT (~line 89), add the column next to the existing `needs_igdb_review` line:

```python
            COALESCE(g.needs_igdb_review, 0) AS needs_igdb_review,
            g.igdb_review_reason,
```

In the row serialization (~line 179), right after the existing `needs_igdb_review` bool coercion:

```python
        game['needs_igdb_review'] = bool(game.get('needs_igdb_review'))
        game['igdb_review_reason'] = game.get('igdb_review_reason')
```

In `api_pin_igdb` (~line 599), extend the clear to the reason:

```python
    conn.execute("UPDATE games SET igdb_locked = 1, needs_igdb_review = 0, "
                 "igdb_review_reason = NULL WHERE id = ?", (game_id,))
```

In `api_igdb_pick` (~line 652-655), extend the UPDATE to clear the reason:

```python
    conn.execute(
        "UPDATE games SET igdb_id = ?, cover_url = COALESCE(?, cover_url), "
        "igdb_locked = 1, needs_igdb_review = 0, igdb_review_reason = NULL, "
        "updated_at = CURRENT_TIMESTAMP WHERE id = ?", (igdb_id, cover_url, game_id))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_api_games.py -v`
Expected: PASS.

- [ ] **Step 5: Run full suite + ruff**

Run: `uv run python -m pytest -q && uv run ruff check .`
Expected: all green, All checks passed.

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_api_games.py
git commit -m "feat(api): surface igdb_review_reason; clear it on pick/pin

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Reason chip in `gameCardHtml` (UI-only)

**Files:**
- Modify: `templates/base.html` (`gameCardHtml` ~305-326)

No unit test (UI-only; the controller verifies live in Phase 5). The chip renders only for flagged games, so it is invisible on the normal grid and visible in the "Needs review" filter.

- [ ] **Step 1: Add the reason chip to the card body**

In `gameCardHtml`, replace the card-body return (the `<div class="p-3">…</div>` block, ~324-325) with a version that adds a reason chip when present:

```javascript
            const reviewChip = (game.needs_igdb_review && game.igdb_review_reason)
                ? `<div class="mt-1"><span class="text-[10px] px-1.5 py-0.5 rounded bg-yellow-500/20 text-yellow-300"
                        title="IGDB audit: ${escapeHtml(game.igdb_review_reason)}">⚠ ${escapeHtml(game.igdb_review_reason)}</span></div>`
                : '';
            return `<div class="game-card bg-surface-light rounded-lg overflow-hidden cursor-pointer" data-game-id="${game.id}" onclick="openModal(${game.id})">
                <div class="aspect-[3/4] relative overflow-hidden">${cover}${collectionBadge}</div>
                <div class="p-3"><h3 class="font-medium text-sm text-white leading-tight line-clamp-2 min-h-[2.5rem]" title="${escapeHtml(game.title)}">${escapeHtml(game.title)}</h3>
                    <div class="mt-2 flex flex-wrap items-center gap-1">${badges}</div>${reviewChip}</div></div>`;
```

- [ ] **Step 2: Run full suite + ruff** (no behavior change, but keep the gate green)

Run: `uv run python -m pytest -q && uv run ruff check .`
Expected: all green, All checks passed.

- [ ] **Step 3: Commit**

```bash
git add templates/base.html
git commit -m "feat(ui): reason chip on Needs-review cards

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 5 (controller-run, after this plan lands — NOT a subagent task)

- [ ] Stop any running stale `app.py` (PowerShell Stop-Process filtered to `*app.py*`; never `servosity_restore`).
- [ ] Confirm live DB backup exists (`games.db.bak-20260603-pre-igdb-audit`); the prior reset already returned `needs_igdb_review` to 0 — `igdb_review_reason` is freshly added and NULL.
- [ ] Controller re-runs `audit_igdb_matches` over the live `games.db` (live IGDB), reports the new flagged count + a reason breakdown to the owner. Expect far fewer than 178.
- [ ] Controller relaunches the app; owner walks the "Needs review" list (now with reason chips), picks correct versions in the modal (each locks the game). Then stop the app.
- [ ] `uv run python -m pytest -q` green; `uv run ruff check .` clean; push.

---

## Notes for the implementer

- Reuse `score_candidates`, `candidates_for`, `platform_ids_for`, `_cover_stem`, `fetch_entry`, `igdb_dlc._igdb_query` — do not reimplement IGDB access or scoring.
- Never make a live IGDB call in a test; monkeypatch `igdb_match.candidates_for` / `igdb_match.fetch_entry` (or `igdb_match.igdb_dlc._igdb_query`).
- The audit is **set-only** (it never clears existing flags); the controller resets the baseline before each live run. Keep it that way.
- `_REVIEW_MARGIN`/`_STRONG_MATCH` are tunable constants — do not inline the numbers in conditions.
- Reason strings use ASCII `->` (e.g. `mobile->console`) to stay lint/encoding-safe; the UI displays them verbatim.
