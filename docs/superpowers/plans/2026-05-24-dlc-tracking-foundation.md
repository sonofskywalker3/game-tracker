# DLC Tracking (Foundation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **CRITICAL (memory `subagent-impl-never-touch-live-db`):** Implementation/review subagents verify ONLY with `python -m pytest` (temp-DB fixtures in `tests/conftest.py`) and `ruff check .`. NEVER start the dev server, NEVER POST to the running app, NEVER call mutating/network functions against the real `games.db` or live IGDB. All IGDB calls in tests are monkeypatched. Live enrichment (Task 11) is run by the ORCHESTRATOR with the user.

**Goal:** Give each game a DLC tab (in its modal) listing IGDB-sourced DLC/expansions with per-item ownership checkboxes, auto-populated by IGDB enrichment wired into import, plus an IGDB-URL "pin identity" path that fixes a game's cover + DLC.

**Architecture:** A new `dlc` child table + `games.igdb_id` column. A new `igdb_dlc.py` module holds pure parse/merge/slug helpers plus an isolated IGDB fetch (reusing `fetch_covers`'s Twitch auth). `import_scraped` calls incremental enrichment after import. Flask endpoints expose the DLC list (folded into `GET /api/games/<id>`), ownership toggle, manual add/delete, per-game refresh, and identity-pin. The modal gets a `Details | DLC` tab.

**Tech Stack:** Python 3, Flask, stdlib `sqlite3`, `requests`, pytest (temp-DB + `client` fixtures, monkeypatch), `ruff`. Spec: `docs/superpowers/specs/2026-05-24-dlc-tracking-design.md`.

---

## File Structure

- Create: `igdb_dlc.py` — pure: `parse_dlc_payload`, `merge_dlc`, `slug_from_igdb_url`, `format_cover_url`; network-isolated: `_igdb_query`, `fetch_game_by_id/slug/title`; orchestration: `enrich_game`, `enrich_missing`.
- Create: `tests/test_igdb_dlc.py`.
- Modify: `models.py` — `dlc` table in `init_db` schema; new `migrate_dlc(conn)` helper called from `migrate_db`.
- Modify: `app.py` — fold `dlc` into `GET /api/games/<id>`; add `POST /api/dlc/<id>/owned`, `POST /api/games/<id>/dlc`, `DELETE /api/dlc/<id>`, `POST /api/games/<id>/dlc/refresh`, `POST /api/games/<id>/igdb`. Ensure `import requests` present.
- Modify: `import_scraped.py` — `run_dlc_enrichment(conn)` helper + `--no-dlc` flag + post-import call.
- Modify: `tests/test_api_games.py`, `tests/test_import_scraped.py` — new tests.
- Modify: `templates/base.html` — DLC tab UI + JS; cover-field smart-routing.

Verified reference facts:
- `config.get_twitch_credentials()` returns `(client_id, client_secret)` or `(None, None)`.
- `fetch_covers.get_access_token(client_id, client_secret, force_refresh=False)` returns a token (cached in `.igdb_token.json`); `fetch_covers.IGDB_API_URL == "https://api.igdb.com/v4"`.
- IGDB cover URL format rule (`app.py:1086-1088`): `raw.replace("t_thumb","t_cover_big")`; if not `startswith("http")`, prefix `"https:"`.
- `migrate_db` (`models.py:406-458`) calls a series of `migrate_*(conn)` helpers; follow that pattern.
- `GET /api/games/<id>` is `api_game` (`app.py:235-284`); it builds `result = dict(game)` then adds `platforms`/`tags`. `PUT /api/games/<id>` is `api_update_game` (`app.py:287-305`) and already handles `cover_url`.
- `models.get_db()` sets `row_factory = sqlite3.Row`.

---

## Task 1: Schema + migration (`dlc` table, `games.igdb_id`)

**Files:**
- Modify: `models.py` (add `dlc` table to `init_db` schema block; add `migrate_dlc(conn)`; call it in `migrate_db`)
- Test: `tests/test_models.py` (create if absent)

- [ ] **Step 1: Write the failing test**

Create/append `tests/test_models.py`:

```python
import sqlite3

import models


def test_init_creates_dlc_schema(temp_db):
    conn = models.get_db()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "dlc" in tables
    cols = {c[1] for c in conn.execute("PRAGMA table_info(games)")}
    assert "igdb_id" in cols
    conn.close()


def test_migrate_dlc_adds_to_legacy_db():
    # A bare DB with a games table missing igdb_id and no dlc table.
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE games (id INTEGER PRIMARY KEY, title TEXT)")
    models.migrate_dlc(conn)
    cols = {c[1] for c in conn.execute("PRAGMA table_info(games)")}
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "igdb_id" in cols and "dlc" in tables
    # idempotent: a second run does not error
    models.migrate_dlc(conn)
    conn.close()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_models.py -v`
Expected: FAIL — `dlc` not in tables / `models` has no attribute `migrate_dlc`.

- [ ] **Step 3: Implement**

In `models.py`, inside the big `init_db` `CREATE TABLE` script, add the `dlc` table next to the other tables (before the closing `"""` of that executescript). Also add `igdb_id` to the `games` table definition if you can locate the `CREATE TABLE ... games` block; if the games CREATE is built elsewhere, rely on the migration to add it. Add this table SQL:

```sql
        -- DLC / expansions for a game (child rows; checkbox ownership)
        CREATE TABLE IF NOT EXISTS dlc (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id    INTEGER NOT NULL,
            name       TEXT    NOT NULL,
            igdb_id    INTEGER,
            kind       TEXT    DEFAULT 'dlc',
            owned      INTEGER DEFAULT 0,
            source     TEXT    DEFAULT 'igdb',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (game_id, name),
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_dlc_game ON dlc(game_id);
```

Add the migration helper (near the other `migrate_*` helpers):

```python
def migrate_dlc(conn):
    """Add the dlc child table and games.igdb_id column if missing (idempotent)."""
    cols = [c[1] for c in conn.execute("PRAGMA table_info(games)")]
    if "igdb_id" not in cols:
        conn.execute("ALTER TABLE games ADD COLUMN igdb_id INTEGER")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dlc (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id    INTEGER NOT NULL,
            name       TEXT    NOT NULL,
            igdb_id    INTEGER,
            kind       TEXT    DEFAULT 'dlc',
            owned      INTEGER DEFAULT 0,
            source     TEXT    DEFAULT 'igdb',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (game_id, name),
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dlc_game ON dlc(game_id)")
    conn.commit()
```

In `migrate_db`, add a call alongside the other helpers (e.g. after `migrate_not_duplicates(conn)`):

```python
    # Add the dlc table + games.igdb_id (DLC tracking)
    migrate_dlc(conn)
```

To guarantee `init_db` also yields the column on a brand-new DB even if the `games` CREATE wasn't edited, call `migrate_dlc(conn)` at the end of `init_db` before `conn.commit()` — it's idempotent and additive. (If you successfully added `igdb_id` to the games CREATE and the `dlc` CREATE to the script, this call is a harmless no-op.)

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_models.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add models.py tests/test_models.py
git commit -m "feat: dlc table + games.igdb_id schema and migration"
```

---

## Task 2: `parse_dlc_payload` (pure)

**Files:**
- Create: `igdb_dlc.py`
- Test: `tests/test_igdb_dlc.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_igdb_dlc.py`:

```python
import igdb_dlc


def test_parse_flattens_dlcs_and_expansions_with_kind():
    payload = {
        "id": 1, "name": "Base Game",
        "dlcs": [{"id": 11, "name": "Pack A"}, {"id": 12, "name": "Pack B"}],
        "expansions": [{"id": 21, "name": "Big Expansion"}],
        "standalone_expansions": [{"id": 31, "name": "Standalone Ex"}],
    }
    out = igdb_dlc.parse_dlc_payload(payload)
    by_name = {d["name"]: d for d in out}
    assert by_name["Pack A"]["kind"] == "dlc" and by_name["Pack A"]["igdb_id"] == 11
    assert by_name["Big Expansion"]["kind"] == "expansion"
    assert by_name["Standalone Ex"]["kind"] == "expansion"
    assert len(out) == 4


def test_parse_drops_blanks_and_dedupes_by_name():
    payload = {
        "dlcs": [{"id": 1, "name": "Pack"}, {"id": 2, "name": "  "}, {"id": 3, "name": "Pack"}],
    }
    out = igdb_dlc.parse_dlc_payload(payload)
    assert [d["name"] for d in out] == ["Pack"]


def test_parse_empty_payload():
    assert igdb_dlc.parse_dlc_payload({"id": 1, "name": "x"}) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_igdb_dlc.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'igdb_dlc'`.

- [ ] **Step 3: Implement**

Create `igdb_dlc.py`:

```python
"""IGDB-sourced DLC/expansion enrichment for games.

Pure helpers (parse/merge/slug/cover formatting) are unit-tested; the network
fetch (`_igdb_query` and the `fetch_game_by_*` wrappers) is isolated so tests
monkeypatch `_igdb_query`. Auth is reused from fetch_covers (no duplicated token
logic). DLC is a child of a game, never a games row.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time

import requests

from fetch_covers import IGDB_API_URL, get_access_token  # noqa: F401 (get_access_token re-exported for callers)

logger = logging.getLogger(__name__)

# IGDB relations that count as DLC, with the kind we store.
_DLC_RELATIONS = (("dlcs", "dlc"), ("expansions", "expansion"),
                  ("standalone_expansions", "expansion"))


def parse_dlc_payload(igdb_game: dict) -> list[dict]:
    """Flatten an IGDB game's dlcs/expansions into {name, igdb_id, kind} dicts.

    Blanks are dropped; names are de-duped within the payload (case-insensitive).
    """
    out: list[dict] = []
    seen: set[str] = set()
    for key, kind in _DLC_RELATIONS:
        for item in igdb_game.get(key) or []:
            name = (item.get("name") or "").strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            out.append({"name": name, "igdb_id": item.get("id"), "kind": kind})
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_igdb_dlc.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add igdb_dlc.py tests/test_igdb_dlc.py
git commit -m "feat: igdb_dlc.parse_dlc_payload"
```

---

## Task 3: `slug_from_igdb_url` + `format_cover_url` (pure)

**Files:**
- Modify: `igdb_dlc.py`
- Test: `tests/test_igdb_dlc.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_igdb_dlc.py`:

```python
def test_slug_from_igdb_url():
    assert igdb_dlc.slug_from_igdb_url("https://www.igdb.com/games/elden-ring") == "elden-ring"
    assert igdb_dlc.slug_from_igdb_url("http://igdb.com/games/the-witcher-3") == "the-witcher-3"
    assert igdb_dlc.slug_from_igdb_url("https://www.igdb.com/games/elden-ring/dlc") == "elden-ring"


def test_slug_from_non_igdb_url_is_none():
    assert igdb_dlc.slug_from_igdb_url("https://images.igdb.com/igdb/co1.jpg") is None
    assert igdb_dlc.slug_from_igdb_url("https://example.com/cover.png") is None
    assert igdb_dlc.slug_from_igdb_url("") is None
    assert igdb_dlc.slug_from_igdb_url(None) is None


def test_format_cover_url():
    assert igdb_dlc.format_cover_url("//images.igdb.com/igdb/image/upload/t_thumb/co1.jpg") == \
        "https://images.igdb.com/igdb/image/upload/t_cover_big/co1.jpg"
    assert igdb_dlc.format_cover_url("https://x/t_thumb/co.jpg") == "https://x/t_cover_big/co.jpg"
    assert igdb_dlc.format_cover_url(None) is None
    assert igdb_dlc.format_cover_url("") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_igdb_dlc.py -k "slug or format_cover" -v`
Expected: FAIL — `AttributeError: ... has no attribute 'slug_from_igdb_url'`.

- [ ] **Step 3: Implement**

Append to `igdb_dlc.py`:

```python
_IGDB_GAME_URL = re.compile(r"https?://(?:www\.)?igdb\.com/games/([a-z0-9][a-z0-9\-]*)",
                            re.IGNORECASE)


def slug_from_igdb_url(url: str | None) -> str | None:
    """Extract the game slug from an igdb.com/games/<slug> URL, else None."""
    if not url:
        return None
    m = _IGDB_GAME_URL.search(url.strip())
    return m.group(1).lower() if m else None


def format_cover_url(raw: str | None) -> str | None:
    """Upgrade an IGDB cover URL to t_cover_big and ensure an https scheme."""
    if not raw:
        return None
    url = raw.replace("t_thumb", "t_cover_big")
    if not url.startswith("http"):
        url = "https:" + url
    return url
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_igdb_dlc.py -k "slug or format_cover" -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add igdb_dlc.py tests/test_igdb_dlc.py
git commit -m "feat: igdb_dlc slug + cover-url helpers"
```

---

## Task 4: `merge_dlc` (idempotent, insert-only)

**Files:**
- Modify: `igdb_dlc.py`
- Test: `tests/test_igdb_dlc.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_igdb_dlc.py`:

```python
import models


def _game(conn, title="Base"):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    return conn.execute("SELECT id FROM games WHERE title=?", (title,)).fetchone()[0]


def test_merge_inserts_then_is_idempotent_and_preserves_owned(temp_db):
    conn = models.get_db()
    gid = _game(conn)
    parsed = [{"name": "Pack A", "igdb_id": 11, "kind": "dlc"},
              {"name": "Expo", "igdb_id": 21, "kind": "expansion"}]
    r1 = igdb_dlc.merge_dlc(conn, gid, parsed)
    conn.commit()
    assert r1 == {"added": 2, "existing": 0}
    # user owns Pack A and adds a manual row
    conn.execute("UPDATE dlc SET owned=1 WHERE game_id=? AND name='Pack A'", (gid,))
    conn.execute("INSERT INTO dlc (game_id, name, source) VALUES (?, 'My Manual', 'manual')", (gid,))
    conn.commit()
    # re-merge same payload + a new pack
    r2 = igdb_dlc.merge_dlc(conn, gid, parsed + [{"name": "Pack B", "igdb_id": 12, "kind": "dlc"}])
    conn.commit()
    assert r2 == {"added": 1, "existing": 2}
    rows = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT name, owned, source FROM dlc WHERE game_id=?", (gid,))}
    assert rows["Pack A"][0] == 1            # ownership preserved
    assert "My Manual" in rows               # manual row preserved
    assert "Pack B" in rows                  # new appended
    conn.close()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_igdb_dlc.py -k merge -v`
Expected: FAIL — `AttributeError: ... has no attribute 'merge_dlc'`.

- [ ] **Step 3: Implement**

Append to `igdb_dlc.py`:

```python
def merge_dlc(conn: sqlite3.Connection, game_id: int, parsed: list[dict]) -> dict:
    """Insert any parsed DLC not already present for the game (by name).

    Never updates existing rows, so `owned` and manual entries are preserved.
    Returns {"added", "existing"}.
    """
    added = existing = 0
    for d in parsed:
        present = conn.execute(
            "SELECT 1 FROM dlc WHERE game_id = ? AND name = ?", (game_id, d["name"])).fetchone()
        if present:
            existing += 1
            continue
        conn.execute(
            "INSERT OR IGNORE INTO dlc (game_id, name, igdb_id, kind, source) "
            "VALUES (?, ?, ?, ?, 'igdb')",
            (game_id, d["name"], d.get("igdb_id"), d.get("kind", "dlc")))
        added += 1
    return {"added": added, "existing": existing}
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_igdb_dlc.py -k merge -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add igdb_dlc.py tests/test_igdb_dlc.py
git commit -m "feat: igdb_dlc.merge_dlc insert-only idempotent merge"
```

---

## Task 5: Fetch + `enrich_game` + `enrich_missing` (network isolated)

**Files:**
- Modify: `igdb_dlc.py`
- Test: `tests/test_igdb_dlc.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_igdb_dlc.py`:

```python
def test_enrich_game_by_title_sets_igdb_id_cover_and_dlc(temp_db, monkeypatch):
    conn = models.get_db()
    gid = _game(conn, "Base")
    conn.commit()
    payload = {"id": 999, "name": "Base", "slug": "base",
               "cover": {"url": "//img/t_thumb/co9.jpg"},
               "dlcs": [{"id": 11, "name": "Pack A"}]}
    monkeypatch.setattr(igdb_dlc, "_igdb_query", lambda q, c, t: [payload])
    rep = igdb_dlc.enrich_game(conn, gid, "cid", "tok")
    conn.commit()
    assert rep["matched"] and rep["added"] == 1
    g = conn.execute("SELECT igdb_id, cover_url FROM games WHERE id=?", (gid,)).fetchone()
    assert g["igdb_id"] == 999
    assert g["cover_url"] == "https://img/t_cover_big/co9.jpg"
    assert conn.execute("SELECT COUNT(*) FROM dlc WHERE game_id=?", (gid,)).fetchone()[0] == 1
    conn.close()


def test_enrich_game_no_match_returns_unmatched(temp_db, monkeypatch):
    conn = models.get_db()
    gid = _game(conn, "Nope")
    conn.commit()
    monkeypatch.setattr(igdb_dlc, "_igdb_query", lambda q, c, t: [])
    rep = igdb_dlc.enrich_game(conn, gid, "cid", "tok")
    assert rep["matched"] is False and rep["added"] == 0
    conn.close()


def test_enrich_missing_is_incremental(temp_db, monkeypatch):
    conn = models.get_db()
    g1 = _game(conn, "One")
    g2 = _game(conn, "Two")
    conn.execute("UPDATE games SET igdb_id = 5 WHERE id = ?", (g2,))  # already enriched
    conn.commit()
    calls = []
    def fake_query(q, c, t):
        calls.append(q)
        return [{"id": 42, "name": "One", "dlcs": [{"id": 1, "name": "X"}]}]
    monkeypatch.setattr(igdb_dlc, "_igdb_query", fake_query)
    totals = igdb_dlc.enrich_missing(conn, client_id="cid", token="tok")
    assert totals["games"] == 1 and totals["matched"] == 1  # only g1 (igdb_id NULL)
    assert conn.execute("SELECT igdb_id FROM games WHERE id=?", (g1,)).fetchone()[0] == 42
    conn.close()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_igdb_dlc.py -k "enrich" -v`
Expected: FAIL — `AttributeError: ... has no attribute 'enrich_game'`.

- [ ] **Step 3: Implement**

Append to `igdb_dlc.py`:

```python
_DLC_FIELDS = ("name, slug, cover.url, dlcs.name, expansions.name, "
               "standalone_expansions.name")


def _igdb_query(query: str, client_id: str, access_token: str) -> list[dict]:
    """POST an apicalypse query to IGDB /games; retry once on 429."""
    headers = {"Client-ID": client_id, "Authorization": f"Bearer {access_token}",
               "Content-Type": "text/plain"}
    response = requests.post(f"{IGDB_API_URL}/games", headers=headers, data=query)
    if response.status_code == 429:
        time.sleep(1)
        return _igdb_query(query, client_id, access_token)
    response.raise_for_status()
    return response.json()


def fetch_game_by_id(igdb_id: int, client_id: str, token: str) -> dict | None:
    res = _igdb_query(f"fields {_DLC_FIELDS}; where id = {int(igdb_id)};", client_id, token)
    return res[0] if res else None


def fetch_game_by_slug(slug: str, client_id: str, token: str) -> dict | None:
    safe = slug.replace('"', "")
    res = _igdb_query(f'fields {_DLC_FIELDS}; where slug = "{safe}";', client_id, token)
    return res[0] if res else None


def fetch_game_by_title(title: str, client_id: str, token: str) -> dict | None:
    from fetch_covers import clean_search_title
    safe = clean_search_title(title).replace('"', "")
    res = _igdb_query(f'search "{safe}"; fields {_DLC_FIELDS}; limit 1;', client_id, token)
    return res[0] if res else None


def enrich_game(conn: sqlite3.Connection, game_id: int, client_id: str, token: str,
                *, slug: str | None = None) -> dict:
    """Resolve a game on IGDB (by slug, stored id, or title), store igdb_id +
    cover, and merge its DLC. Returns {matched, cover_set, added, existing}.

    Cover is overwritten when pinning by slug; on auto-resolution it is only set
    when the game has no cover (never clobbers a user/IGDB cover).
    """
    row = conn.execute("SELECT title, igdb_id, cover_url FROM games WHERE id = ?",
                       (game_id,)).fetchone()
    if not row:
        return {"matched": False, "cover_set": False, "added": 0, "existing": 0}
    if slug:
        game = fetch_game_by_slug(slug, client_id, token)
    elif row["igdb_id"]:
        game = fetch_game_by_id(row["igdb_id"], client_id, token)
    else:
        game = fetch_game_by_title(row["title"], client_id, token)
    if not game:
        return {"matched": False, "cover_set": False, "added": 0, "existing": 0}
    conn.execute("UPDATE games SET igdb_id = ? WHERE id = ?", (game.get("id"), game_id))
    cover = format_cover_url((game.get("cover") or {}).get("url"))
    cover_set = False
    if cover and (slug or not row["cover_url"]):
        conn.execute("UPDATE games SET cover_url = ? WHERE id = ?", (cover, game_id))
        cover_set = True
    counts = merge_dlc(conn, game_id, parse_dlc_payload(game))
    return {"matched": True, "cover_set": cover_set, **counts}


def enrich_missing(conn: sqlite3.Connection, *, client_id: str, token: str) -> dict:
    """Enrich every never-enriched game (games.igdb_id IS NULL). Commits per game;
    a per-game network error is logged and skipped (never aborts the run)."""
    ids = [r[0] for r in conn.execute("SELECT id FROM games WHERE igdb_id IS NULL")]
    totals = {"games": 0, "matched": 0, "added": 0, "errors": 0}
    for gid in ids:
        try:
            rep = enrich_game(conn, gid, client_id, token)
            conn.commit()
        except requests.RequestException as exc:
            conn.rollback()
            totals["errors"] += 1
            logger.warning("DLC enrich failed for game %s: %s", gid, exc)
            continue
        totals["games"] += 1
        totals["matched"] += int(rep["matched"])
        totals["added"] += rep["added"]
    return totals
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_igdb_dlc.py -v`
Expected: PASS (all igdb_dlc tests)

- [ ] **Step 5: Commit**

```bash
git add igdb_dlc.py tests/test_igdb_dlc.py
git commit -m "feat: igdb_dlc fetch + enrich_game + incremental enrich_missing"
```

---

## Task 6: Fold `dlc` into `GET /api/games/<id>`

**Files:**
- Modify: `app.py` (`api_game`, ~`app.py:281` after `result['tags']`)
- Test: `tests/test_api_games.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api_games.py`:

```python
def test_get_game_includes_dlc(client, temp_db):
    import models
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('G', 'g')")
    gid = conn.execute("SELECT id FROM games WHERE title='G'").fetchone()[0]
    conn.execute("INSERT INTO dlc (game_id, name, kind, owned, source) "
                 "VALUES (?, 'Pack A', 'dlc', 1, 'igdb')", (gid,))
    conn.commit()
    conn.close()
    resp = client.get(f"/api/games/{gid}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["dlc"] == [{"id": data["dlc"][0]["id"], "name": "Pack A",
                            "kind": "dlc", "owned": True, "source": "igdb"}]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_api_games.py::test_get_game_includes_dlc -v`
Expected: FAIL — `KeyError: 'dlc'`.

- [ ] **Step 3: Implement**

In `app.py` `api_game`, immediately after `result['tags'] = [dict(t) for t in tags]` and before `conn.close()`:

```python
    # DLC list (folded into the game detail for the modal)
    dlc = conn.execute(
        "SELECT id, name, kind, owned, source FROM dlc WHERE game_id = ? ORDER BY kind, name",
        (game_id,)).fetchall()
    result['dlc'] = [{**dict(d), 'owned': bool(d['owned'])} for d in dlc]
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_api_games.py::test_get_game_includes_dlc -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_api_games.py
git commit -m "feat: include dlc list in GET /api/games/<id>"
```

---

## Task 7: Owned toggle + manual add + delete endpoints

**Files:**
- Modify: `app.py` (add three routes near `api_game`)
- Test: `tests/test_api_games.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api_games.py`:

```python
def _make_game_with_dlc(name="Pack A"):
    import models
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('G', 'g')")
    gid = conn.execute("SELECT id FROM games WHERE title='G'").fetchone()[0]
    conn.execute("INSERT INTO dlc (game_id, name, source) VALUES (?, ?, 'igdb')", (gid, name))
    did = conn.execute("SELECT id FROM dlc WHERE game_id=?", (gid,)).fetchone()[0]
    conn.commit()
    conn.close()
    return gid, did


def test_toggle_dlc_owned(client, temp_db):
    gid, did = _make_game_with_dlc()
    resp = client.post(f"/api/dlc/{did}/owned", json={"owned": True})
    assert resp.status_code == 200 and resp.get_json()["owned"] is True
    import models
    conn = models.get_db()
    assert conn.execute("SELECT owned FROM dlc WHERE id=?", (did,)).fetchone()[0] == 1
    conn.close()
    assert client.post("/api/dlc/99999/owned", json={"owned": True}).status_code == 404


def test_add_manual_dlc_and_duplicate_noop(client, temp_db):
    gid, _ = _make_game_with_dlc()
    resp = client.post(f"/api/games/{gid}/dlc", json={"name": "Manual X"})
    assert resp.status_code == 201
    row = resp.get_json()
    assert row["name"] == "Manual X" and row["source"] == "manual"
    # duplicate name returns the existing row (no second insert)
    resp2 = client.post(f"/api/games/{gid}/dlc", json={"name": "Manual X"})
    assert resp2.status_code == 200 and resp2.get_json()["id"] == row["id"]
    assert client.post(f"/api/games/{gid}/dlc", json={"name": "  "}).status_code == 400


def test_delete_dlc(client, temp_db):
    gid, did = _make_game_with_dlc()
    assert client.delete(f"/api/dlc/{did}").status_code == 200
    import models
    conn = models.get_db()
    assert conn.execute("SELECT COUNT(*) FROM dlc WHERE id=?", (did,)).fetchone()[0] == 0
    conn.close()
    assert client.delete(f"/api/dlc/{did}").status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_api_games.py -k "toggle_dlc or manual_dlc or delete_dlc" -v`
Expected: FAIL — 404/405 (routes not defined).

- [ ] **Step 3: Implement**

In `app.py`, add after `api_game` (before `api_update_game`):

```python
@app.route('/api/dlc/<int:dlc_id>/owned', methods=['POST'])
def api_set_dlc_owned(dlc_id):
    """Toggle ownership of a DLC entry."""
    data = request.get_json(silent=True) or {}
    owned = 1 if data.get('owned') else 0
    conn = get_db()
    cur = conn.execute("UPDATE dlc SET owned = ? WHERE id = ?", (owned, dlc_id))
    conn.commit()
    found = cur.rowcount > 0
    conn.close()
    if not found:
        return jsonify({'error': 'DLC not found'}), 404
    return jsonify({'ok': True, 'owned': bool(owned)})


@app.route('/api/games/<int:game_id>/dlc', methods=['POST'])
def api_add_dlc(game_id):
    """Add a manual DLC entry; returns the existing row if the name is a dup."""
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    kind = data.get('kind') or 'dlc'
    if not name:
        return jsonify({'error': 'name required'}), 400
    conn = get_db()
    if not conn.execute("SELECT 1 FROM games WHERE id = ?", (game_id,)).fetchone():
        conn.close()
        return jsonify({'error': 'Game not found'}), 404
    existing = conn.execute(
        "SELECT id, name, kind, owned, source FROM dlc WHERE game_id = ? AND name = ?",
        (game_id, name)).fetchone()
    if existing:
        conn.close()
        return jsonify({**dict(existing), 'owned': bool(existing['owned'])})
    cur = conn.execute(
        "INSERT INTO dlc (game_id, name, kind, source) VALUES (?, ?, ?, 'manual')",
        (game_id, name, kind))
    conn.commit()
    new = conn.execute(
        "SELECT id, name, kind, owned, source FROM dlc WHERE id = ?", (cur.lastrowid,)).fetchone()
    conn.close()
    return jsonify({**dict(new), 'owned': bool(new['owned'])}), 201


@app.route('/api/dlc/<int:dlc_id>', methods=['DELETE'])
def api_delete_dlc(dlc_id):
    """Delete a DLC entry (manual or IGDB-sourced)."""
    conn = get_db()
    cur = conn.execute("DELETE FROM dlc WHERE id = ?", (dlc_id,))
    conn.commit()
    found = cur.rowcount > 0
    conn.close()
    if not found:
        return jsonify({'error': 'DLC not found'}), 404
    return jsonify({'ok': True})
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_api_games.py -k "toggle_dlc or manual_dlc or delete_dlc" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_api_games.py
git commit -m "feat: DLC owned-toggle, manual-add, delete endpoints"
```

---

## Task 8: Refresh + IGDB-pin endpoints

**Files:**
- Modify: `app.py` (add two routes; ensure `import requests` at top)
- Test: `tests/test_api_games.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api_games.py`:

```python
def test_refresh_dlc_from_igdb(client, temp_db, monkeypatch):
    import config, igdb_dlc, fetch_covers, models
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('G', 'g')")
    gid = conn.execute("SELECT id FROM games WHERE title='G'").fetchone()[0]
    conn.commit(); conn.close()
    monkeypatch.setattr(config, "get_twitch_credentials", lambda: ("cid", "sec"))
    monkeypatch.setattr(fetch_covers, "get_access_token", lambda *a, **k: "tok")
    monkeypatch.setattr(igdb_dlc, "_igdb_query",
                        lambda q, c, t: [{"id": 7, "name": "G", "dlcs": [{"id": 1, "name": "P"}]}])
    resp = client.post(f"/api/games/{gid}/dlc/refresh")
    assert resp.status_code == 200
    names = [d["name"] for d in resp.get_json()["dlc"]]
    assert names == ["P"]


def test_refresh_dlc_without_credentials_400(client, temp_db, monkeypatch):
    import config, models
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('G', 'g')")
    gid = conn.execute("SELECT id FROM games WHERE title='G'").fetchone()[0]
    conn.commit(); conn.close()
    monkeypatch.setattr(config, "get_twitch_credentials", lambda: (None, None))
    assert client.post(f"/api/games/{gid}/dlc/refresh").status_code == 400


def test_pin_igdb_identity_sets_cover_and_dlc(client, temp_db, monkeypatch):
    import config, igdb_dlc, fetch_covers, models
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('G', 'g')")
    gid = conn.execute("SELECT id FROM games WHERE title='G'").fetchone()[0]
    conn.commit(); conn.close()
    monkeypatch.setattr(config, "get_twitch_credentials", lambda: ("cid", "sec"))
    monkeypatch.setattr(fetch_covers, "get_access_token", lambda *a, **k: "tok")
    monkeypatch.setattr(igdb_dlc, "_igdb_query",
                        lambda q, c, t: [{"id": 50, "name": "G", "slug": "g",
                                          "cover": {"url": "//img/t_thumb/co.jpg"},
                                          "expansions": [{"id": 2, "name": "Exp"}]}])
    resp = client.post(f"/api/games/{gid}/igdb",
                       json={"url": "https://www.igdb.com/games/g"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["game"]["igdb_id"] == 50
    assert data["game"]["cover_url"] == "https://img/t_cover_big/co.jpg"
    assert [d["name"] for d in data["dlc"]] == ["Exp"]


def test_pin_igdb_rejects_non_igdb_url(client, temp_db):
    import models
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('G', 'g')")
    gid = conn.execute("SELECT id FROM games WHERE title='G'").fetchone()[0]
    conn.commit(); conn.close()
    assert client.post(f"/api/games/{gid}/igdb",
                       json={"url": "https://example.com/x.png"}).status_code == 400
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_api_games.py -k "refresh_dlc or pin_igdb" -v`
Expected: FAIL — routes not defined (404/405).

- [ ] **Step 3: Implement**

Ensure `app.py` imports `requests` at the top (add `import requests` if missing). Add after `api_delete_dlc`:

```python
@app.route('/api/games/<int:game_id>/dlc/refresh', methods=['POST'])
def api_refresh_dlc(game_id):
    """Re-fetch a game's DLC from IGDB (by stored id, or by title if unset)."""
    import config
    import igdb_dlc
    conn = get_db()
    if not conn.execute("SELECT 1 FROM games WHERE id = ?", (game_id,)).fetchone():
        conn.close()
        return jsonify({'error': 'Game not found'}), 404
    client_id, secret = config.get_twitch_credentials()
    if not client_id:
        conn.close()
        return jsonify({'error': 'IGDB credentials not configured'}), 400
    try:
        token = igdb_dlc.get_access_token(client_id, secret)
        report = igdb_dlc.enrich_game(conn, game_id, client_id, token)
        conn.commit()
    except requests.RequestException as exc:
        conn.rollback()
        conn.close()
        return jsonify({'error': f'IGDB request failed: {exc}'}), 502
    dlc = conn.execute(
        "SELECT id, name, kind, owned, source FROM dlc WHERE game_id = ? ORDER BY kind, name",
        (game_id,)).fetchall()
    conn.close()
    return jsonify({'dlc': [{**dict(d), 'owned': bool(d['owned'])} for d in dlc],
                    'report': report})


@app.route('/api/games/<int:game_id>/igdb', methods=['POST'])
def api_pin_igdb(game_id):
    """Pin a game's IGDB identity from an igdb.com/games/<slug> URL: sets igdb_id,
    refreshes the cover, and re-fetches DLC."""
    import config
    import igdb_dlc
    data = request.get_json(silent=True) or {}
    slug = igdb_dlc.slug_from_igdb_url((data.get('url') or '').strip())
    if not slug:
        return jsonify({'error': 'Not an IGDB game URL'}), 400
    conn = get_db()
    if not conn.execute("SELECT 1 FROM games WHERE id = ?", (game_id,)).fetchone():
        conn.close()
        return jsonify({'error': 'Game not found'}), 404
    client_id, secret = config.get_twitch_credentials()
    if not client_id:
        conn.close()
        return jsonify({'error': 'IGDB credentials not configured'}), 400
    try:
        token = igdb_dlc.get_access_token(client_id, secret)
        report = igdb_dlc.enrich_game(conn, game_id, client_id, token, slug=slug)
        conn.commit()
    except requests.RequestException as exc:
        conn.rollback()
        conn.close()
        return jsonify({'error': f'IGDB request failed: {exc}'}), 502
    if not report['matched']:
        conn.close()
        return jsonify({'error': 'No IGDB game found for that URL'}), 404
    game = conn.execute(
        "SELECT id, title, cover_url, igdb_id FROM games WHERE id = ?", (game_id,)).fetchone()
    dlc = conn.execute(
        "SELECT id, name, kind, owned, source FROM dlc WHERE game_id = ? ORDER BY kind, name",
        (game_id,)).fetchall()
    conn.close()
    return jsonify({'game': dict(game),
                    'dlc': [{**dict(d), 'owned': bool(d['owned'])} for d in dlc],
                    'report': report})
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_api_games.py -k "refresh_dlc or pin_igdb" -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_api_games.py
git commit -m "feat: DLC refresh + IGDB identity-pin endpoints"
```

---

## Task 9: Import-pipeline enrichment hook + `--no-dlc`

**Files:**
- Modify: `import_scraped.py` (`run_dlc_enrichment` helper, `--no-dlc` arg, call in `main`)
- Test: `tests/test_import_scraped.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_import_scraped.py`:

```python
def test_run_dlc_enrichment_skips_without_credentials(temp_db, monkeypatch):
    import config
    import models
    import import_scraped as imp
    monkeypatch.setattr(config, "get_twitch_credentials", lambda: (None, None))
    conn = models.get_db()
    assert imp.run_dlc_enrichment(conn) is None
    conn.close()


def test_run_dlc_enrichment_populates_dlc(temp_db, monkeypatch):
    import config
    import fetch_covers
    import igdb_dlc
    import models
    import import_scraped as imp
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('G', 'g')")
    conn.commit()
    monkeypatch.setattr(config, "get_twitch_credentials", lambda: ("cid", "sec"))
    monkeypatch.setattr(fetch_covers, "get_access_token", lambda *a, **k: "tok")
    monkeypatch.setattr(igdb_dlc, "_igdb_query",
                        lambda q, c, t: [{"id": 3, "name": "G", "dlcs": [{"id": 1, "name": "P"}]}])
    totals = imp.run_dlc_enrichment(conn)
    assert totals["matched"] == 1 and totals["added"] == 1
    assert conn.execute("SELECT COUNT(*) FROM dlc").fetchone()[0] == 1
    conn.close()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_import_scraped.py -k "dlc_enrichment" -v`
Expected: FAIL — `AttributeError: ... has no attribute 'run_dlc_enrichment'`.

- [ ] **Step 3: Implement**

In `import_scraped.py`, add the helper (near `cleanup_bundles`):

```python
def run_dlc_enrichment(conn: sqlite3.Connection) -> Optional[dict]:
    """Enrich never-enriched games with IGDB DLC. Returns totals, or None if no
    Twitch credentials are configured (enrichment skipped)."""
    import config
    import igdb_dlc
    client_id, secret = config.get_twitch_credentials()
    if not client_id:
        logger.info("DLC enrich skipped (no Twitch credentials in config.json)")
        return None
    token = igdb_dlc.get_access_token(client_id, secret)
    totals = igdb_dlc.enrich_missing(conn, client_id=client_id, token=token)
    logger.info("DLC enrich: %d games, %d matched, +%d dlc, %d errors",
                totals["games"], totals["matched"], totals["added"], totals["errors"])
    return totals
```

In `main`, add the flag next to `--keep-non-games`:

```python
    parser.add_argument("--no-dlc", action="store_true",
                        help="skip IGDB DLC enrichment after import")
```

And after the import loop completes — i.e. after `if args.dry_run: ... else: conn.commit()` and `_log_summary(...)`, but before `conn.close()` — add:

```python
    if not args.dry_run and not args.no_dlc:
        run_dlc_enrichment(conn)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_import_scraped.py -k "dlc_enrichment" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add import_scraped.py tests/test_import_scraped.py
git commit -m "feat: import_scraped DLC enrichment hook + --no-dlc flag"
```

---

## Task 10: DLC tab UI + cover-field smart-routing (`base.html`)

**Files:**
- Modify: `templates/base.html` (`loadGameModal` render + JS handlers; `setCoverUrl`)

> UI work has no unit tests in this project (the user verifies "feel" in the running app). Implement, then leave verification to Task 11's orchestrator/user step. Do NOT start the dev server yourself.

- [ ] **Step 1: Add a tab strip + DLC panel to the modal render**

In `loadGameModal` (`templates/base.html`), the modal body currently renders the details section. Wrap the existing details content and add a DLC panel + tab strip. Inside the `content.innerHTML = \`...\`` template, immediately after the cover/header block and before the details fields, insert a tab strip:

```html
                    <div class="mt-4 flex gap-2 border-b border-gray-700" id="modal-tabs-${game.id}">
                        <button type="button" data-tab="details"
                            class="modal-tab px-3 py-2 text-sm border-b-2 border-accent text-white"
                            onclick="switchGameTab(${game.id}, 'details')">Details</button>
                        <button type="button" data-tab="dlc"
                            class="modal-tab px-3 py-2 text-sm border-b-2 border-transparent text-gray-400"
                            onclick="switchGameTab(${game.id}, 'dlc')">
                            DLC (${(game.dlc || []).filter(d => d.owned).length}/${(game.dlc || []).length})
                        </button>
                    </div>
```

Wrap the existing details fields block in `<div id="tab-details-${game.id}" class="game-tab-panel">...existing fields...</div>` and add the DLC panel after it:

```html
                    <div id="tab-dlc-${game.id}" class="game-tab-panel hidden">
                        ${renderDlcPanel(game)}
                    </div>
```

- [ ] **Step 2: Add the render + tab-switch + DLC action JS**

Add these functions in the `<script>` block near `loadGameModal`:

```javascript
        function switchGameTab(gameId, tab) {
            document.querySelectorAll(`#modal-tabs-${gameId} .modal-tab`).forEach(b => {
                const on = b.dataset.tab === tab;
                b.classList.toggle('border-accent', on);
                b.classList.toggle('text-white', on);
                b.classList.toggle('border-transparent', !on);
                b.classList.toggle('text-gray-400', !on);
            });
            document.getElementById(`tab-details-${gameId}`).classList.toggle('hidden', tab !== 'details');
            document.getElementById(`tab-dlc-${gameId}`).classList.toggle('hidden', tab !== 'dlc');
        }

        function renderDlcPanel(game) {
            const dlc = game.dlc || [];
            const rows = dlc.map(d => `
                <label class="flex items-center gap-2 py-1 text-sm">
                    <input type="checkbox" ${d.owned ? 'checked' : ''}
                        onchange="toggleDlcOwned(${game.id}, ${d.id}, this.checked)">
                    <span class="flex-1 text-white">${d.name.replace(/</g, '&lt;')}</span>
                    <span class="status-badge bg-surface-lighter text-gray-300">${d.kind}</span>
                    ${d.source === 'manual'
                        ? `<button onclick="deleteDlc(${game.id}, ${d.id})" class="text-gray-500 hover:text-red-400">&times;</button>`
                        : ''}
                </label>`).join('');
            return `
                <div class="mt-3 space-y-2">
                    ${dlc.length ? rows : '<p class="text-sm text-gray-500">No DLC found — Refresh from IGDB or add manually.</p>'}
                    <div class="flex gap-2 pt-2">
                        <input id="dlc-add-${game.id}" type="text" placeholder="Add DLC name…"
                            class="flex-1 bg-surface rounded-lg border border-gray-600 px-3 py-2 text-white text-sm">
                        <button onclick="addDlc(${game.id}, document.getElementById('dlc-add-${game.id}').value)"
                            class="px-3 py-2 bg-surface-lighter hover:bg-gray-600 rounded-lg text-white text-sm">Add</button>
                        <button onclick="refreshDlc(${game.id})"
                            class="px-3 py-2 bg-accent hover:bg-accent-hover rounded-lg text-white text-sm">Refresh from IGDB</button>
                    </div>
                </div>`;
        }

        async function toggleDlcOwned(gameId, dlcId, owned) {
            await api.post(`/api/dlc/${dlcId}/owned`, { owned });
            loadGameModal(gameId);
        }
        async function addDlc(gameId, name) {
            if (!name.trim()) return;
            await api.post(`/api/games/${gameId}/dlc`, { name: name.trim() });
            loadGameModal(gameId);
        }
        async function deleteDlc(gameId, dlcId) {
            await api.del(`/api/dlc/${dlcId}`);
            loadGameModal(gameId);
        }
        async function refreshDlc(gameId) {
            await api.post(`/api/games/${gameId}/dlc/refresh`, {});
            loadGameModal(gameId);
        }
```

If `api` has no `post`/`del` helpers, use the same shapes already used elsewhere (the file uses `api.get`, `api.put`; add `api.post`/`api.del` mirroring `api.put`, or call the matching existing helper names — verify against the `api` object definition in `base.html` and match its method names exactly).

- [ ] **Step 3: Smart-route the Cover Art URL field to identity-pin**

Replace `setCoverUrl` (`templates/base.html:946-950`) with:

```javascript
        async function setCoverUrl(gameId, url) {
            const v = url.trim();
            const isIgdb = /https?:\/\/(www\.)?igdb\.com\/games\//i.test(v);
            if (isIgdb) {
                const res = await api.post(`/api/games/${gameId}/igdb`, { url: v });
                if (res && res.error) { alert(res.error); }
            } else {
                await api.put(`/api/games/${gameId}`, { cover_url: v });
            }
            loadGameModal(gameId);
            if (typeof refreshGameList === 'function') refreshGameList();
        }
```

Update the Cover Art URL field placeholder/help text (`templates/base.html:786-789`) to mention IGDB: change the placeholder to `"https://… or paste an IGDB game URL"` and add a small helper line under it: `<p class="text-xs text-gray-500 mt-1">Paste an IGDB game URL to set the right cover + DLC.</p>`.

- [ ] **Step 4: Sanity-check the template compiles (no server start)**

Run: `python -c "import jinja2, pathlib; jinja2.Environment().parse(pathlib.Path('templates/base.html').read_text(encoding='utf-8')); print('template parses')"`
Expected: `template parses` (catches gross Jinja/HTML-in-template syntax errors; it does not execute JS).

- [ ] **Step 5: Commit**

```bash
git add templates/base.html
git commit -m "feat: DLC tab UI + IGDB-URL cover-field smart-routing"
```

---

## Task 11: Full verification + live enrichment (ORCHESTRATOR ONLY for live steps)

> Steps 1-2 are safe for any agent. **Steps 3+ touch the real `games.db` and live IGDB and MUST be performed by the orchestrator with the user** (memory `subagent-impl-never-touch-live-db`).

- [ ] **Step 1: Full test suite**

Run: `python -m pytest`
Expected: PASS — prior suite + the new DLC tests. If any pre-existing test fails, STOP and investigate.

- [ ] **Step 2: Lint**

Run: `ruff check .`
Expected: clean.

- [ ] **Step 3 (orchestrator): Back up the live DB**

```bash
Copy-Item "games.db" "games.db.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
```

- [ ] **Step 4 (orchestrator): Migrate + smoke-test the UI with the user.** Start the app (`python app.py`), open a game modal, confirm the `Details | DLC` tab appears and the empty state shows. Confirm `config.json` has Twitch creds.

- [ ] **Step 5 (orchestrator): Live enrich a few games.** Either click "Refresh from IGDB" on a handful of games, or run an import (`python import_scraped.py <path>`) and watch the `DLC enrich` log line. Verify DLC rows appear and ownership toggles persist. Try pasting an IGDB game URL into a mis-matched game's Cover Art field and confirm the cover + DLC correct.

- [ ] **Step 6 (orchestrator): Optional full backfill.** If the user wants the whole library populated, run import (or a one-off `enrich_missing`) — incremental, so it only processes never-enriched games. Watch for the error count.

- [ ] **Step 7: Update memory** with the DLC feature landed + that pieces 3/4 and the GUI scrape button remain.

---

## Self-Review

**Spec coverage:**
- `dlc` table + `games.igdb_id` → Task 1. ✓
- IGDB enrichment module (fetch/parse/merge, reuse auth) → Tasks 2-5. ✓
- Idempotent insert-only merge preserving owned/manual → Task 4. ✓
- Incremental enrichment in import + `--no-dlc` → Task 9. ✓
- `dlc` in `GET /api/games/<id>` → Task 6. ✓
- owned toggle / manual add (dup → existing) / delete → Task 7. ✓
- refresh + identity-pin endpoints (slug resolve, cover + DLC) → Task 8. ✓
- "Pinning the IGDB identity" via cover field → Task 8 (endpoint) + Task 10 (smart-routing). ✓
- DLC tab UI (Details | DLC, checkboxes, refresh, add, empty state) → Task 10. ✓
- Tests: parse, merge idempotency, API, import integration, slug/identity → Tasks 2-9. ✓
- Out of scope (edition auto-check, scrape ownership, GUI scrape button) → not built. ✓

**Placeholder scan:** none — every code/test step shows full content. (Task 10 Step 2 notes verifying `api` helper method names against the file; that's a real instruction, not a placeholder — the `api` object exists in `base.html`.)

**Type consistency:** `parse_dlc_payload(dict)->list[dict]` (Task 2) consumed by `merge_dlc(conn, game_id, parsed)->{"added","existing"}` (Task 4) and `enrich_game` (Task 5). `enrich_game(conn, game_id, client_id, token, *, slug=None)->{"matched","cover_set","added","existing"}` consumed by endpoints (Task 8) and `enrich_missing` (Task 5). `slug_from_igdb_url`/`format_cover_url` (Task 3) used in Task 5/8. `run_dlc_enrichment(conn)->dict|None` (Task 9). `get_access_token` re-exported from `igdb_dlc` (Task 5) and used in Task 8/9. All consistent.
