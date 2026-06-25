# Physical/Digital Consolidation + Cartridge/Disc Media Icons Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `game_platforms.format` the single source of truth for physical/digital, heal the 48 legacy "Physical"-tagged games with a conservative one-time migration, retire the legacy Physical-tag control, and render a media-aware (cartridge vs disc) badge on the web card.

**Architecture:** The web API's `game['physical']` is re-derived from each game's per-platform `game_platforms.format` instead of the legacy `Physical` tag. A module-level `PLATFORM_MEDIA` lookup maps platform `short_name` → `cartridge`/`disc`; `api_games` exposes a per-game `physical_media[]` of the distinct media types owned in physical/both format. A guarded, idempotent migration flips only the tagged games' `digital`/`NULL` platform rows to `physical`. The web card renders one badge SVG per media type; the legacy Physical-tag checkbox is removed.

**Tech Stack:** Python 3 / Flask, SQLite (`models.py`), pytest, Jinja/vanilla-JS templates (`templates/base.html`, `templates/index.html`). Backend tests: `uv run python -m pytest`. Lint: `ruff check` only.

## Global Constraints

- **Source of truth = `game_platforms.format`** (`'physical'` | `'digital'` | `'both'` | `NULL`). A game is "physical" iff some owned platform's format is `'physical'` or `'both'`.
- **Work on `main`, commit + push directly** — no branches/PRs.
- **Backend tests:** `uv run python -m pytest` (plain `uv run pytest` fails: ModuleNotFoundError). **Lint gate:** `ruff check` ONLY — never `ruff format` (code is hand-aligned).
- **`git add` EXPLICIT file paths only** — never `git add -A`/`.` (DB backups `games.db.*` and `_live_server*.log`/`test_*.log` are gitignored but a blanket add still risks sweeping scratch).
- **Subagents never touch the live `games.db`, the running `:5000` server, or the device** — pytest temp-DB + static review only. The controller (you) handles any live-server restart (owner-gated; `use_reloader=False` means Python changes need a restart) and any browser/device verification.
- **Module-level immutability** (CLAUDE.md): lookup tables / constants at module scope (`frozenset`, tuples, named string constants — no magic strings in conditions).
- **Conservative migration:** only the games carrying the `Physical` tag flip to `physical`, and only on platform rows whose current format is `'digital'` or `NULL`. NEVER downgrade `'both'` or an existing `'physical'`. Idempotent (re-runnable to a no-op).
- **Mobile media-icon is OUT OF SCOPE** (deferred fast-follow). The Android scanner already reads `game_platforms.format` and displays correctly once the data is consolidated.

---

### Task 1: `PLATFORM_MEDIA` media-type lookup (app.py)

A module-scope lookup mapping platform `short_name` → physical media type, plus named constants. Pure data + its unit test; no behavior wired yet.

**Files:**
- Modify: `app.py` (add module-level constants near the other module constants, above `api_games`)
- Test: `tests/test_platform_media.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `app.MEDIA_CARTRIDGE: str = "cartridge"`
  - `app.MEDIA_DISC: str = "disc"`
  - `app.PHYSICAL_FORMATS: tuple[str, str] = ("physical", "both")`
  - `app.PLATFORM_MEDIA: dict[str, str]` — maps `short_name` → `MEDIA_CARTRIDGE`/`MEDIA_DISC`. Unmapped short_names are absent (`.get()` returns `None`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_platform_media.py`:

```python
"""PLATFORM_MEDIA maps platform short_name -> physical media type (cartridge/disc)."""
import app


def test_cartridge_platforms_map_to_cartridge():
    for short in ("Switch", "Switch2", "3DS", "NDS", "N64", "SNES", "NES",
                  "GB", "GBC", "GBA", "Genesis", "Vita"):
        assert app.PLATFORM_MEDIA.get(short) == app.MEDIA_CARTRIDGE, short


def test_disc_platforms_map_to_disc():
    for short in ("PS1", "PS2", "PS3", "PS4", "PS5", "OGXbox", "X360", "Xbox",
                  "GC", "Wii", "WiiU", "Dreamcast", "Saturn", "PSP", "PC"):
        assert app.PLATFORM_MEDIA.get(short) == app.MEDIA_DISC, short


def test_unmapped_platforms_have_no_media():
    # mobile / subscription / unknown carry no physical-media badge
    for short in ("iOS", "Android", "GamePass", "PSPlus", "NSO", "Nonsense"):
        assert app.PLATFORM_MEDIA.get(short) is None, short


def test_media_constants_are_distinct_strings():
    assert app.MEDIA_CARTRIDGE == "cartridge"
    assert app.MEDIA_DISC == "disc"
    assert app.PHYSICAL_FORMATS == ("physical", "both")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_platform_media.py -v`
Expected: FAIL with `AttributeError: module 'app' has no attribute 'PLATFORM_MEDIA'`.

- [ ] **Step 3: Add the lookup to `app.py`**

Add these module-level constants to `app.py`. Place them just above the `api_games` route (the `@app.route('/api/games', methods=['GET'])` handler that contains the `game['physical'] = ...` line ~196), so the lookup lives next to its only consumer:

```python
# --- Physical-media badge lookup -------------------------------------------
# Single source of truth for which physical medium a platform's games ship on.
# Module-scope lookup table (CLAUDE.md). Platforms not listed here
# (mobile / subscription / unknown) carry no physical-media badge.
MEDIA_CARTRIDGE = "cartridge"
MEDIA_DISC = "disc"

# A game counts as "physical" when it is owned physical OR both on some platform.
PHYSICAL_FORMATS = ("physical", "both")

_CARTRIDGE_PLATFORMS = frozenset({
    "Switch", "Switch2", "3DS", "NDS", "N64", "SNES", "NES",
    "GB", "GBC", "GBA", "Genesis", "Vita",
})
_DISC_PLATFORMS = frozenset({
    "PS1", "PS2", "PS3", "PS4", "PS5", "OGXbox", "X360", "Xbox",
    "GC", "Wii", "WiiU", "Dreamcast", "Saturn", "PSP", "PC",
})
PLATFORM_MEDIA: dict[str, str] = {
    **{short: MEDIA_CARTRIDGE for short in _CARTRIDGE_PLATFORMS},
    **{short: MEDIA_DISC for short in _DISC_PLATFORMS},
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_platform_media.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Lint**

Run: `ruff check app.py tests/test_platform_media.py`
Expected: no errors. (Do NOT run `ruff format`.)

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_platform_media.py
git commit -m "feat(web): PLATFORM_MEDIA cartridge/disc lookup + media constants"
```

---

### Task 2: Re-derive `physical` from format + add `physical_media` to `api_games` (app.py)

The games-list API stops reading the `Physical` tag for format and instead derives `physical` (and the new `physical_media[]`) from each game's per-platform `game_platforms.format`.

**Files:**
- Modify: `app.py` — the `api_games` GET handler (the per-game platform query ~179-186 and the `game['physical'] = ...` line ~196)
- Modify: `tests/test_api_games.py` — update `_insert_game` helper + `test_api_games_exposes_categories_and_physical` to the new format-based semantics
- Test: `tests/test_api_games.py` (add `physical_media` cases)

**Interfaces:**
- Consumes: `app.PLATFORM_MEDIA`, `app.PHYSICAL_FORMATS` (Task 1).
- Produces: each game dict in `GET /api/games` now carries `game['physical']: bool` (format-derived) and `game['physical_media']: list[str]` (sorted distinct media types of platforms owned `physical`/`both`; `[]` when none).

- [ ] **Step 1: Update the existing test fixture + assertions to format-based semantics**

In `tests/test_api_games.py`, replace the `_insert_game` helper so a `physical=True` game sets `game_platforms.format='physical'` (the new source of truth) instead of only adding the tag:

```python
def _insert_game(title, short_name, physical=False):
    conn = models.get_db()
    conn.execute(
        "INSERT INTO games (title, normalized_title) VALUES (?, ?)",
        (title, models.normalize_title(title)),
    )
    gid = conn.execute("SELECT id FROM games WHERE title = ?", (title,)).fetchone()[0]
    pid = conn.execute(
        "SELECT id FROM platforms WHERE short_name = ?", (short_name,)
    ).fetchone()[0]
    fmt = "physical" if physical else "digital"
    conn.execute(
        "INSERT INTO game_platforms (game_id, platform_id, format) VALUES (?, ?, ?)",
        (gid, pid, fmt),
    )
    conn.execute("INSERT INTO user_ratings (game_id, status) VALUES (?, 'backlog')", (gid,))
    conn.commit()
    conn.close()
    return gid
```

(The old `physical=True` branch that inserted a `Physical` tag is removed — format now carries it.)

- [ ] **Step 2: Add `physical_media` assertions to the existing list test**

In `tests/test_api_games.py`, extend `test_api_games_exposes_categories_and_physical` so it also asserts the new `physical_media` field. Replace the test body with:

```python
def test_api_games_exposes_categories_and_physical(client):
    _ensure_platform("PlayStation 4", "PS4", "modern_console")
    _ensure_platform("PlayStation 3", "PS3", "legacy_console")
    # "PC" is seeded by init_db as category 'pc'.
    _insert_game("Modern Disc Game", "PS4", physical=True)
    _insert_game("Retro Game", "PS3")
    _insert_game("Desktop Game", "PC")

    rows = client.get("/api/games").get_json()
    by_title = {g["title"]: g for g in rows}

    assert by_title["Modern Disc Game"]["categories"] == ["modern_console"]
    assert by_title["Modern Disc Game"]["physical"] is True
    assert by_title["Modern Disc Game"]["physical_media"] == ["disc"]   # PS4 -> disc
    assert by_title["Retro Game"]["categories"] == ["legacy_console"]
    assert by_title["Retro Game"]["physical"] is False
    assert by_title["Retro Game"]["physical_media"] == []              # digital -> no media
    assert by_title["Desktop Game"]["categories"] == ["pc"]
```

- [ ] **Step 3: Add a dedicated derivation + multi-media test**

Append to `tests/test_api_games.py`:

```python
def test_physical_media_derived_from_format_not_tag(client):
    """physical/physical_media come from game_platforms.format, never the tag."""
    _ensure_platform("Nintendo Switch", "Switch", "modern_console")
    _ensure_platform("PlayStation 5", "PS5", "modern_console")

    conn = models.get_db()
    # A) cartridge-only physical (Switch)
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (1,'Cart','cart')")
    sid = conn.execute("SELECT id FROM platforms WHERE short_name='Switch'").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) "
                 "VALUES (1, ?, 'physical')", (sid,))
    # B) owned on Switch (physical) AND PS5 (disc) -> both media, deduped + sorted
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (2,'Mixed','mixed')")
    pid = conn.execute("SELECT id FROM platforms WHERE short_name='PS5'").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) "
                 "VALUES (2, ?, 'both')", (sid,))   # Switch 'both' counts as physical
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) "
                 "VALUES (2, ?, 'physical')", (pid,))
    # C) has the legacy 'Physical' TAG but every platform is digital -> NOT physical
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (3,'Tagged','tagged')")
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) "
                 "VALUES (3, ?, 'digital')", (sid,))
    conn.execute("INSERT OR IGNORE INTO tags (name, category) VALUES ('Physical','custom')")
    tid = conn.execute("SELECT id FROM tags WHERE name='Physical'").fetchone()[0]
    conn.execute("INSERT INTO game_tags (game_id, tag_id) VALUES (3, ?)", (tid,))
    conn.commit()
    conn.close()

    by_id = {g["id"]: g for g in client.get("/api/games").get_json()}
    assert by_id[1]["physical"] is True
    assert by_id[1]["physical_media"] == ["cartridge"]
    assert by_id[2]["physical"] is True
    assert by_id[2]["physical_media"] == ["cartridge", "disc"]   # sorted, deduped
    assert by_id[3]["physical"] is False        # tag ignored; format is digital
    assert by_id[3]["physical_media"] == []
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_api_games.py::test_api_games_exposes_categories_and_physical tests/test_api_games.py::test_physical_media_derived_from_format_not_tag -v`
Expected: FAIL — `test_api_games_exposes...` fails on `physical is True` (the format-based derivation isn't implemented yet, and the old tag path is gone) / missing `physical_media` key; the new test fails on the missing `physical_media` key.

- [ ] **Step 5: Re-derive `physical` + emit `physical_media` in `api_games`**

In `app.py`, in the `api_games` GET handler, change the per-game platform query to also pull `gp.format`, then replace the tag-based `physical` line. Replace this block:

```python
        # Get platforms (+ era category) for this game
        platforms = conn.execute("""
            SELECT p.short_name, p.category
            FROM platforms p
            JOIN game_platforms gp ON gp.platform_id = p.id
            WHERE gp.game_id = ?
        """, (row['id'],)).fetchall()
        game['platforms'] = [p['short_name'] for p in platforms]
        game['categories'] = sorted({p['category'] for p in platforms})
```

with:

```python
        # Get platforms (+ era category + per-platform format) for this game
        platforms = conn.execute("""
            SELECT p.short_name, p.category, gp.format
            FROM platforms p
            JOIN game_platforms gp ON gp.platform_id = p.id
            WHERE gp.game_id = ?
        """, (row['id'],)).fetchall()
        game['platforms'] = [p['short_name'] for p in platforms]
        game['categories'] = sorted({p['category'] for p in platforms})
```

Then replace the tag-based physical line:

```python
        game['physical'] = any(t['name'] == 'Physical' for t in game['tags'])
```

with the format-based derivation + the new `physical_media` field:

```python
        # physical/digital is per-platform format now (single source of truth),
        # not the legacy 'Physical' tag. Physical iff owned physical/both somewhere.
        physical_rows = [p for p in platforms if p['format'] in PHYSICAL_FORMATS]
        game['physical'] = bool(physical_rows)
        game['physical_media'] = sorted({
            PLATFORM_MEDIA[p['short_name']]
            for p in physical_rows
            if p['short_name'] in PLATFORM_MEDIA
        })
```

(Leave the `game['tags'] = [...]` line directly above untouched — tags are still surfaced for other purposes; only the `physical` derivation moves off them.)

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_api_games.py -v`
Expected: PASS (all tests in the file, including the two updated/added).

- [ ] **Step 7: Lint**

Run: `ruff check app.py tests/test_api_games.py`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add app.py tests/test_api_games.py
git commit -m "feat(web): derive game.physical from format + add physical_media[] to api_games"
```

---

### Task 3: Conservative tagged→physical migration (models.py)

A guarded, idempotent migration that flips only the legacy `Physical`-tagged games to `format='physical'` on their `digital`/`NULL` platform rows, never downgrading `both`.

**Files:**
- Modify: `models.py` — add `migrate_tagged_games_to_physical`; register it in `migrate_db()` right after `migrate_game_platform_format(conn)`
- Test: `tests/test_platform_format.py` (add migration cases)

**Interfaces:**
- Consumes: existing `game_platforms.format` column (added by `migrate_game_platform_format`, which must run first).
- Produces: `models.migrate_tagged_games_to_physical(conn: sqlite3.Connection) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_platform_format.py`:

```python
def _tag_physical(conn, game_id):
    conn.execute("INSERT OR IGNORE INTO tags (name, category) VALUES ('Physical','custom')")
    tid = conn.execute("SELECT id FROM tags WHERE name='Physical'").fetchone()[0]
    conn.execute("INSERT INTO game_tags (game_id, tag_id) VALUES (?, ?)", (game_id, tid))


def test_tagged_games_to_physical_flips_digital_and_null_for_tagged_only(temp_db):
    conn = models.get_db()
    _add_platform(conn, "Nintendo Switch", "Switch", "modern_console")
    _add_platform(conn, "PlayStation 5", "PS5", "modern_console")
    sid = conn.execute("SELECT id FROM platforms WHERE short_name='Switch'").fetchone()[0]
    pid = conn.execute("SELECT id FROM platforms WHERE short_name='PS5'").fetchone()[0]

    # game 1: tagged Physical, owned digital on Switch + NULL on PS5 -> both flip
    conn.execute("INSERT INTO games (id,title,normalized_title) VALUES (1,'Kirby','kirby')")
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) VALUES (1, ?, 'digital')", (sid,))
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) VALUES (1, ?, NULL)", (pid,))
    _tag_physical(conn, 1)

    # game 2: tagged Physical, already 'both' on Switch -> must NOT downgrade
    conn.execute("INSERT INTO games (id,title,normalized_title) VALUES (2,'Both','both')")
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) VALUES (2, ?, 'both')", (sid,))
    _tag_physical(conn, 2)

    # game 3: NOT tagged, owned digital -> untouched (most of the library)
    conn.execute("INSERT INTO games (id,title,normalized_title) VALUES (3,'Digital','digital')")
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) VALUES (3, ?, 'digital')", (sid,))
    conn.commit()

    models.migrate_tagged_games_to_physical(conn)

    fmts = {(r[0], r[1]): r[2] for r in conn.execute(
        "SELECT gp.game_id, p.short_name, gp.format FROM game_platforms gp "
        "JOIN platforms p ON p.id = gp.platform_id").fetchall()}
    conn.close()
    assert fmts[(1, "Switch")] == "physical"   # digital -> physical
    assert fmts[(1, "PS5")] == "physical"       # NULL -> physical (all owned platforms)
    assert fmts[(2, "Switch")] == "both"        # never downgraded
    assert fmts[(3, "Switch")] == "digital"     # untagged, untouched


def test_tagged_games_to_physical_is_idempotent(temp_db):
    conn = models.get_db()
    _add_platform(conn, "Nintendo Switch", "Switch", "modern_console")
    sid = conn.execute("SELECT id FROM platforms WHERE short_name='Switch'").fetchone()[0]
    conn.execute("INSERT INTO games (id,title,normalized_title) VALUES (1,'K','k')")
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) VALUES (1, ?, 'digital')", (sid,))
    _tag_physical(conn, 1)
    conn.commit()

    models.migrate_tagged_games_to_physical(conn)
    models.migrate_tagged_games_to_physical(conn)  # second run = no-op
    fmt = conn.execute("SELECT format FROM game_platforms WHERE game_id=1").fetchone()[0]
    conn.close()
    assert fmt == "physical"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_platform_format.py::test_tagged_games_to_physical_flips_digital_and_null_for_tagged_only tests/test_platform_format.py::test_tagged_games_to_physical_is_idempotent -v`
Expected: FAIL with `AttributeError: module 'models' has no attribute 'migrate_tagged_games_to_physical'`.

- [ ] **Step 3: Add the migration function**

In `models.py`, add this function next to `migrate_game_platform_format` (just below it, ~line 1011):

```python
def migrate_tagged_games_to_physical(conn: sqlite3.Connection) -> None:
    """One-time conservative reconcile of the legacy 'Physical' tag into the
    per-platform format source of truth. For every game carrying the 'Physical'
    tag, set format='physical' on each owned platform whose current format is
    'digital' or NULL. Never downgrades 'both' or an existing 'physical', and
    never touches untagged games. Idempotent: only digital/NULL rows of tagged
    games move, so re-running is a no-op.

    Must run AFTER migrate_game_platform_format (which creates the column)."""
    conn.execute("""
        UPDATE game_platforms SET format = 'physical'
        WHERE (format IS NULL OR format = 'digital')
          AND EXISTS (
              SELECT 1 FROM game_tags gt JOIN tags t ON t.id = gt.tag_id
              WHERE gt.game_id = game_platforms.game_id AND t.name = 'Physical'
          )
    """)
    conn.commit()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_platform_format.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Register the migration in `migrate_db()`**

In `models.py`, in `migrate_db()`, find the line `migrate_game_platform_format(conn)` (~line 1183) and insert the new call immediately after it:

```python
    migrate_game_platform_format(conn)
    migrate_tagged_games_to_physical(conn)
```

- [ ] **Step 6: Verify the full suite still passes (no regression from registration)**

Run: `uv run python -m pytest -q`
Expected: PASS (whole suite; the new migration is conservative and registered after the column exists).

- [ ] **Step 7: Lint**

Run: `ruff check models.py tests/test_platform_format.py`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add models.py tests/test_platform_format.py
git commit -m "feat(db): conservative migrate_tagged_games_to_physical (idempotent, never downgrades both)"
```

---

### Task 4: Stop adding the legacy `Physical` tag on game create (app.py)

The create endpoint already sets `game_platforms.format='physical'` when the physical checkbox is ticked; remove the now-redundant `Physical` tag insert so format is the sole carrier.

**Files:**
- Modify: `app.py` — `api_create_game` (`POST /api/games`), remove the tag-add block (~lines 266-277)
- Modify: `tests/test_api_add_legacy_game.py` — update `test_create_game_marks_physical` to assert format, not the tag

**Interfaces:**
- Consumes: nothing new.
- Produces: `POST /api/games` with `physical: true` sets `game_platforms.format='physical'` on each provided platform and adds NO `Physical` tag.

- [ ] **Step 1: Update the create test to the new (format-based) contract**

In `tests/test_api_add_legacy_game.py`, replace `test_create_game_marks_physical` with a version that asserts the per-platform format and the absence of the tag:

```python
def test_create_game_marks_physical(client):
    resp = client.post(
        "/api/games",
        json={"title": "Fire Emblem Awakening", "platforms": ["3DS"], "physical": True},
    )
    assert resp.status_code == 201
    gid = resp.get_json()["game_id"]

    game = client.get(f"/api/games/{gid}").get_json()
    # Format is the source of truth now: 3DS is recorded physical...
    by_short = {p["short_name"]: p for p in game["platforms"]}
    assert by_short["3DS"]["format"] == "physical"
    # ...and no legacy 'Physical' tag is created.
    assert not any(t["name"] == "Physical" for t in game.get("tags", []))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_api_add_legacy_game.py::test_create_game_marks_physical -v`
Expected: FAIL on `assert not any(... "Physical" ...)` — the create path still inserts the tag.

- [ ] **Step 3: Remove the tag-add block from `api_create_game`**

In `app.py`, in `api_create_game`, delete this block (the `# Optional physical-copy flag...` comment through the tag insert, ~lines 266-277):

```python
    # Optional physical-copy flag, surfaced via the 'Physical' tag (see api_games).
    if data.get('physical'):
        conn.execute(
            "INSERT OR IGNORE INTO tags (name, category) VALUES ('Physical', 'custom')"
        )
        tag_id = conn.execute(
            "SELECT id FROM tags WHERE name = 'Physical'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO game_tags (game_id, tag_id) VALUES (?, ?)",
            (game_id, tag_id)
        )
```

Leave the platform-loop above it intact — `fmt = 'physical' if data.get('physical') else 'digital'` (~line 254) and the `INSERT INTO game_platforms (..., format)` already record the format. The block immediately following (`conn.commit()` / `apply_traits_catalog` ...) stays.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run python -m pytest tests/test_api_add_legacy_game.py -v`
Expected: PASS (all 3 tests — `test_create_game_not_physical_by_default` still passes; it never expected the tag).

- [ ] **Step 5: Lint**

Run: `ruff check app.py tests/test_api_add_legacy_game.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_api_add_legacy_game.py
git commit -m "refactor(web): stop adding the legacy 'Physical' tag on create (format is the source)"
```

---

### Task 5: Remove the Physical-tag checkbox from the edit UI (base.html)

Retire the standalone `Physical` tag control. The per-platform Physical/Digital toggle (already in the detail modal) becomes the only way to set format. No automated test (no JS harness) — controller verifies in Task 7.

**Files:**
- Modify: `templates/base.html` — the Tags row (~lines 1194-1208)

**Interfaces:**
- Consumes: nothing.
- Produces: the edit modal's Tags row no longer shows a "Physical" checkbox; tag chips + the `+ tag` input remain.

- [ ] **Step 1: Remove the checkbox + its leading separator**

In `templates/base.html`, inside the Tags `<div class="flex flex-wrap gap-2 items-center">`, delete the Physical `<label>` (the checkbox) and the `|` separator span that followed it. Change this:

```html
                            <div class="flex flex-wrap gap-2 items-center">
                                <label class="flex items-center gap-1 cursor-pointer text-sm text-gray-300">
                                    <input type="checkbox" ${(game.tags || []).some(t => t.name === 'Physical') ? 'checked' : ''}
                                           onchange="toggleTag(${game.id}, 'Physical', this.checked)"
                                           class="accent-accent w-4 h-4">
                                    Physical
                                </label>
                                <span class="text-gray-600">|</span>
                                ${(game.tags || []).filter(t => t.name !== 'Physical').map(t =>
                                    `<span class="px-2 py-0.5 bg-surface rounded text-sm group cursor-pointer" onclick="removeTag(${game.id}, '${t.name}')">${t.name} <span class="text-gray-500 group-hover:text-red-400">×</span></span>`
                                ).join('')}
                                <input type="text" placeholder="+ tag"
                                       class="bg-transparent border-b border-gray-600 px-1 py-0.5 text-white text-sm focus:border-accent focus:outline-none w-20"
                                       onkeypress="if(event.key==='Enter') addTag(${game.id}, this.value, this)">
                            </div>
```

to this (checkbox + separator removed; the `.filter(... !== 'Physical')` stays so any inert legacy Physical tag never shows as a chip):

```html
                            <div class="flex flex-wrap gap-2 items-center">
                                ${(game.tags || []).filter(t => t.name !== 'Physical').map(t =>
                                    `<span class="px-2 py-0.5 bg-surface rounded text-sm group cursor-pointer" onclick="removeTag(${game.id}, '${t.name}')">${t.name} <span class="text-gray-500 group-hover:text-red-400">×</span></span>`
                                ).join('')}
                                <input type="text" placeholder="+ tag"
                                       class="bg-transparent border-b border-gray-600 px-1 py-0.5 text-white text-sm focus:border-accent focus:outline-none w-20"
                                       onkeypress="if(event.key==='Enter') addTag(${game.id}, this.value, this)">
                            </div>
```

(Do NOT remove the `toggleTag` function itself — other tag interactions still use it.)

- [ ] **Step 2: Sanity-check the template still parses (no stray braces)**

Run: `uv run python -c "from jinja2 import Environment, FileSystemLoader; Environment(loader=FileSystemLoader('templates')).get_template('base.html'); print('base.html parses')"`
Expected: prints `base.html parses` (Jinja loads the template without a syntax error; the JS is template-literal text Jinja passes through).

- [ ] **Step 3: Commit**

```bash
git add templates/base.html
git commit -m "feat(web): retire the legacy 'Physical' tag checkbox (per-platform toggle is the editor)"
```

---

### Task 6: Media-aware card badge from `physical_media` (index.html)

Replace the single disc badge with one badge per `physical_media` entry — a cartridge SVG for `cartridge`, the existing disc SVG for `disc`. No automated test — controller browser-verifies in Task 7.

**Files:**
- Modify: `templates/index.html` — the `isPhysical` const (~line 325) and the "Physical disc icon" badge block (~lines 343-351)

**Interfaces:**
- Consumes: `game.physical_media` (Task 2). `[]` → no badge.
- Produces: media-aware badge stack on each card.

- [ ] **Step 1: Replace the `isPhysical` derivation with the media list**

In `templates/index.html`, change:

```javascript
            const isPhysical = (game.tags || []).some(t => t.name === 'Physical');
```

to:

```javascript
            const physicalMedia = game.physical_media || [];
```

- [ ] **Step 2: Replace the single-disc badge with a per-media badge stack**

In `templates/index.html`, replace the "Physical disc icon" block:

```html
                        <!-- Physical disc icon -->
                        ${isPhysical ? `
                            <div class="absolute top-2 right-2 bg-black/50 rounded-full w-6 h-6 flex items-center justify-center" title="Physical Copy">
                                <svg class="w-4 h-4 text-white/80" viewBox="0 0 24 24" fill="currentColor">
                                    <circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" stroke-width="1.5"/>
                                    <circle cx="12" cy="12" r="3" fill="currentColor"/>
                                </svg>
                            </div>
                        ` : ''}
```

with a media-aware stack (one chip per media type; cartridge gets a cartridge SVG, disc keeps the existing disc SVG):

```html
                        <!-- Physical media badges (cartridge / disc), one per media type -->
                        ${physicalMedia.length ? `
                            <div class="absolute top-2 right-2 flex flex-col gap-1">
                                ${physicalMedia.map(media => media === 'cartridge' ? `
                                    <div class="bg-black/50 rounded-full w-6 h-6 flex items-center justify-center" title="Cartridge">
                                        <svg class="w-4 h-4 text-white/80" viewBox="0 0 24 24" fill="currentColor">
                                            <path d="M6 3 H18 a1 1 0 0 1 1 1 V18 a1 1 0 0 1 -1 1 H16 V16 H8 V19 H6 a1 1 0 0 1 -1 -1 V4 a1 1 0 0 1 1 -1 Z"/>
                                            <rect x="8" y="6" width="8" height="5" rx="1" fill="#000" opacity="0.35"/>
                                        </svg>
                                    </div>
                                ` : `
                                    <div class="bg-black/50 rounded-full w-6 h-6 flex items-center justify-center" title="Disc">
                                        <svg class="w-4 h-4 text-white/80" viewBox="0 0 24 24" fill="currentColor">
                                            <circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" stroke-width="1.5"/>
                                            <circle cx="12" cy="12" r="3" fill="currentColor"/>
                                        </svg>
                                    </div>
                                `).join('')}
                            </div>
                        ` : ''}
```

- [ ] **Step 3: Sanity-check the template still parses**

Run: `uv run python -c "from jinja2 import Environment, FileSystemLoader; Environment(loader=FileSystemLoader('templates')).get_template('index.html'); print('index.html parses')"`
Expected: prints `index.html parses`.

- [ ] **Step 4: Commit**

```bash
git add templates/index.html
git commit -m "feat(web): media-aware card badge (cartridge/disc) from physical_media"
```

---

### Task 7: Final gate — full suite, lint, fresh-DB migrate, whole-branch review, push

Verify the whole feature end to end, confirm the migration runs cleanly on a fresh DB through the real runner, run a final whole-branch review, then push. Live `:5000` restart on the consolidated code is **owner-gated** (real-ownership migration) and handled separately — see the closing note.

**Files:** none (verification + push).

- [ ] **Step 1: Full backend suite**

Run: `uv run python -m pytest -q`
Expected: PASS (entire suite green — baseline was 854 passing at the effort start; this feature adds tests in `test_platform_media.py`, `test_api_games.py`, `test_platform_format.py` and updates two existing tests, with no removals).

- [ ] **Step 2: Lint gate**

Run: `ruff check .`
Expected: no errors. (Never `ruff format`.)

- [ ] **Step 3: Fresh-DB migration smoke (real runner, throwaway DB)**

Confirm `migrate_db()` runs the new migration cleanly on a brand-new DB and is idempotent:

```bash
uv run python -c "
import tempfile, os, pathlib
import models
d = pathlib.Path(tempfile.mkdtemp()) / 'fresh.db'
models.DB_PATH = d
models.init_db()
models.migrate_db()
models.migrate_db()  # second run must be a no-op (idempotent)
conn = models.get_db()
cols = [c[1] for c in conn.execute('PRAGMA table_info(game_platforms)').fetchall()]
assert 'format' in cols, cols
print('fresh migrate_db OK; game_platforms.format present; idempotent re-run clean')
conn.close()
"
```

Expected: prints the OK line, no traceback.

- [ ] **Step 4: Whole-branch review**

Dispatch a final review over the feature commit range (Task 1 base → HEAD) using the most capable model. Focus: the `physical`/`physical_media` derivation matches the spec rule (physical iff `format in ('physical','both')`; media deduped+sorted; unmapped platforms excluded); the migration is conservative + idempotent + never downgrades `both`; the create path no longer writes the tag; the two templates render correctly and the `physical_media` empty case shows no badge. Address any Critical/Important findings (with a fail-then-pass test for any code fix), re-run Steps 1-2, and commit fixes (explicit paths).

- [ ] **Step 5: Push**

Run: `git push origin main`
Expected: HEAD advances on `origin/main`.

- [ ] **Step 6: Controller manual verification (browser) — NOT a subagent step**

The controller (not a subagent) browser-verifies the badges against a COPY of `games.db` on an alternate port (per the verify-UI-changes memory): a cartridge-format game shows the cartridge badge, a disc-format game shows the disc badge, a multi-format game shows both, an all-digital game shows none; the edit modal's Tags row no longer has a Physical checkbox. Tear down the verify instance + scratch afterward.

**Closing note (owner-gated, do NOT do autonomously):** Deploying to live `:5000` runs `migrate_tagged_games_to_physical` against the **real** `games.db` (moves ownership data for the 48 tagged games). Before any live restart: take a fresh DB backup (`games.db.pre-consolidation-bak`), then the controller restarts the owner-gated server. The Android app needs no change (it already reads `game_platforms.format`); the mobile media-icon is a deferred fast-follow.

---

## Self-Review

**Spec coverage:**
- §2 single source of truth — Task 2 (re-derive `physical` from format), Task 4 (drop tag-add on create). ✓
- §3 retire the tag control — Task 5 (remove checkbox). ✓
- §4 one-time conservative migration — Task 3. ✓
- §5 `PLATFORM_MEDIA` + `physical_media` field — Task 1 (lookup) + Task 2 (field). ✓
- §6 media-aware web badge — Task 6. ✓
- §7 component boundaries (models.py / app.py / base.html / index.html) — Tasks 3 / 1+2+4 / 5 / 6. ✓
- §8 testing — Task 1 (lookup), Task 2 (derivation + `physical_media` mixed/empty), Task 3 (migration: digital→physical, both untouched, untagged untouched, multi-platform, idempotent), Task 4 (create no longer tags). Badge rendering = manual (Task 7 Step 6), per spec. ✓
- §9 risks — fresh DB backup + owner-gated live restart (Task 7 closing note); never-downgrade-`both` asserted (Task 3). ✓

**Placeholder scan:** No TBD/TODO/"add error handling"/"similar to Task N" — every code step shows the actual code. ✓

**Type consistency:** `PLATFORM_MEDIA`/`MEDIA_CARTRIDGE`/`MEDIA_DISC`/`PHYSICAL_FORMATS` defined in Task 1, consumed verbatim in Task 2. `game.physical_media` produced in Task 2, consumed in Task 6. `migrate_tagged_games_to_physical` defined + registered in Task 3. ✓
