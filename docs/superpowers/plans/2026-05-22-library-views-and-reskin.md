# Library Views + Whole-App Reskin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add five switchable library views (All / Modern consoles / Legacy / PC / Physical) to the Game Tracker and reskin the whole app around a stats hero with the view switcher.

**Architecture:** Platforms gain an `era` `category` column (`modern_console` / `legacy_console` / `pc`); the `/api/games` payload exposes each game's `categories` + a `physical` flag; the index page filters client-side by the active mode (combined with the existing status/platform/search filters), persists the active view in `localStorage`, and renders a stats hero with the mode switcher. The reskin centralizes tokens + the app shell in `base.html`, which every other page inherits.

**Tech Stack:** Python 3, Flask, SQLite (`sqlite3`), Jinja2 templates, vanilla JS, Tailwind (CDN), pytest.

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `models.py` | Schema, platform era classification, migration | Modify |
| `app.py` | `/api/games` serializer exposes `categories` + `physical` | Modify |
| `templates/base.html` | App shell + visual tokens (inherited by all pages) | Modify |
| `templates/index.html` | Stats hero, mode switcher, relocated filters, mode filtering, persistence | Modify |
| `templates/series.html`, `recommendations.html`, `settings.html` | Inherit reskin; spot-fix clashes | Verify/spot-fix |
| `requirements.txt` | Add `pytest` (dev) | Modify |
| `tests/conftest.py` | Temp-DB + Flask client fixtures | Create |
| `tests/test_platform_category.py` | `classify_platform` unit tests | Create |
| `tests/test_migration.py` | Migration idempotency + backfill | Create |
| `tests/test_api_games.py` | API payload includes `categories` + `physical` | Create |

**Conventions:** Run all commands from the project root `C:\Users\Jeff\Documents\Projects\Game Tracker`. Tests run with `python -m pytest`. Commit after every task.

---

### Task 1: Test scaffolding + `classify_platform`

**Files:**
- Modify: `requirements.txt`
- Modify: `models.py` (add era constants + `classify_platform`)
- Create: `tests/conftest.py`
- Create: `tests/test_platform_category.py`

- [ ] **Step 1: Add pytest to requirements**

Append to `requirements.txt`:

```
pytest>=8.0.0
```

Install it:

Run: `python -m pip install -r requirements.txt`
Expected: pytest installs (or "already satisfied").

- [ ] **Step 2: Create the shared test fixtures**

Create `tests/conftest.py`:

```python
"""Shared pytest fixtures: isolated temp DB + Flask test client."""
import pytest

import models
import app as app_module


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point models at a throwaway DB and initialize the schema."""
    db_path = tmp_path / "test_games.db"
    monkeypatch.setattr(models, "DB_PATH", db_path)
    models.init_db()
    return db_path


@pytest.fixture
def client(temp_db):
    """Flask test client backed by the temp DB."""
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()
```

- [ ] **Step 3: Write the failing test**

Create `tests/test_platform_category.py`:

```python
from models import classify_platform


def test_modern_consoles_classify_as_modern():
    for short in ("PS4", "PS5", "Switch", "Xbox"):
        assert classify_platform(short) == "modern_console"


def test_pc_storefronts_classify_as_pc():
    for short in ("PC", "Steam", "GOG", "Epic"):
        assert classify_platform(short) == "pc"


def test_old_systems_classify_as_legacy():
    for short in ("PS3", "PS2", "X360", "Wii", "3DS", "Vita", "SNES"):
        assert classify_platform(short) == "legacy_console"


def test_unknown_defaults_to_modern():
    assert classify_platform("SomeFutureConsole") == "modern_console"
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `python -m pytest tests/test_platform_category.py -v`
Expected: FAIL with `ImportError: cannot import name 'classify_platform'`.

- [ ] **Step 5: Implement `classify_platform`**

In `models.py`, immediately after the `DB_PATH = ...` line (top of file), add:

```python
# Platform era classification (module-level, immutable).
PC_PLATFORMS = frozenset({"PC", "Steam", "GOG", "Epic", "EGS"})
LEGACY_PLATFORMS = frozenset({
    "PS3", "PS2", "PS1", "PSX", "PSV", "Vita", "PSP",
    "X360", "XBOX", "OGXbox",
    "Wii", "WiiU", "GC", "GCN", "N64", "SNES", "NES",
    "3DS", "NDS", "DS", "GBA", "GBC", "GB",
    "Genesis", "Saturn", "Dreamcast",
})

MODERN_CONSOLE = "modern_console"
LEGACY_CONSOLE = "legacy_console"
PC_CATEGORY = "pc"


def classify_platform(short_name: str) -> str:
    """Map a platform short_name to an era category."""
    if short_name in PC_PLATFORMS:
        return PC_CATEGORY
    if short_name in LEGACY_PLATFORMS:
        return LEGACY_CONSOLE
    return MODERN_CONSOLE
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `python -m pytest tests/test_platform_category.py -v`
Expected: 4 passed.

- [ ] **Step 7: Commit**

```bash
git add requirements.txt models.py tests/conftest.py tests/test_platform_category.py
git commit -m "feat: add platform era classification + test scaffolding"
```

---

### Task 2: Schema seed + idempotent migration

**Files:**
- Modify: `models.py` (`platforms` CREATE TABLE, seed, new `migrate_platform_category`, hook into `migrate_db`)
- Create: `tests/test_migration.py`

- [ ] **Step 1: Write the failing migration test**

Create `tests/test_migration.py`:

```python
import sqlite3

from models import migrate_platform_category


def _old_schema_conn():
    """A platforms table WITHOUT the category column (pre-migration shape)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE platforms (id INTEGER PRIMARY KEY, name TEXT, short_name TEXT)"
    )
    conn.executemany(
        "INSERT INTO platforms (name, short_name) VALUES (?, ?)",
        [("PlayStation 4", "PS4"), ("PC", "PC"), ("PlayStation 3", "PS3")],
    )
    return conn


def test_migration_adds_column_and_backfills():
    conn = _old_schema_conn()
    migrate_platform_category(conn)
    cats = {r["short_name"]: r["category"]
            for r in conn.execute("SELECT short_name, category FROM platforms")}
    assert cats == {"PS4": "modern_console", "PC": "pc", "PS3": "legacy_console"}


def test_migration_is_idempotent():
    conn = _old_schema_conn()
    migrate_platform_category(conn)
    first = {r["short_name"]: r["category"]
             for r in conn.execute("SELECT short_name, category FROM platforms")}
    migrate_platform_category(conn)  # second run must not error or change values
    second = {r["short_name"]: r["category"]
              for r in conn.execute("SELECT short_name, category FROM platforms")}
    assert first == second
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_migration.py -v`
Expected: FAIL with `ImportError: cannot import name 'migrate_platform_category'`.

- [ ] **Step 3: Implement the migration helper**

In `models.py`, add this function (place it just above `def migrate_db():`):

```python
def migrate_platform_category(conn):
    """Add platforms.category if missing and (re)backfill from short_name.

    Idempotent: safe to run on every startup. Backfill is deterministic
    (derived purely from short_name), so re-running never loses data.
    """
    cols = [c[1] for c in conn.execute("PRAGMA table_info(platforms)").fetchall()]
    if "category" not in cols:
        conn.execute(
            "ALTER TABLE platforms ADD COLUMN category TEXT NOT NULL "
            "DEFAULT 'modern_console'"
        )
    for row in conn.execute("SELECT id, short_name FROM platforms").fetchall():
        conn.execute(
            "UPDATE platforms SET category = ? WHERE id = ?",
            (classify_platform(row[1]), row[0]),
        )
    conn.commit()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_migration.py -v`
Expected: 2 passed.

- [ ] **Step 5: Add the column to the fresh-DB schema + seed**

In `models.py` `init_db()`, change the `platforms` CREATE TABLE to include the column:

```python
        -- Platforms table
        CREATE TABLE IF NOT EXISTS platforms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            short_name TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL DEFAULT 'modern_console'
        );
```

And replace the default-platforms seed block with category-aware rows:

```python
    # Insert default platforms
    platforms = [
        ("PlayStation", "PS", "modern_console"),
        ("Nintendo Switch", "Switch", "modern_console"),
        ("Xbox", "Xbox", "modern_console"),
        ("PC", "PC", "pc"),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO platforms (name, short_name, category) VALUES (?, ?, ?)",
        platforms
    )
```

- [ ] **Step 6: Hook the migration into `migrate_db`**

In `models.py` `migrate_db()`, after the series-columns migration block and before the title-cleanup loop, add:

```python
    # Add/backfill platform era category
    migrate_platform_category(conn)
```

- [ ] **Step 7: Run the full test suite**

Run: `python -m pytest -v`
Expected: all tests pass (Task 1 + Task 2).

- [ ] **Step 8: Apply the migration to the real database**

Run: `python -c "import models; models.migrate_db()"`
Expected: prints migration messages, no error. (Operates on the live `games.db`.)

Verify the backfill:

Run: `python -c "import models; c=models.get_db(); print({r['short_name']: r['category'] for r in c.execute('SELECT short_name, category FROM platforms')})"`
Expected: `{'Switch': 'modern_console', 'Xbox': 'modern_console', 'PC': 'pc', 'PS4': 'modern_console', 'PS5': 'modern_console'}` (exact set may vary; PC must be `pc`, all consoles `modern_console`).

- [ ] **Step 9: Commit**

```bash
git add models.py tests/test_migration.py
git commit -m "feat: add platforms.category column with idempotent migration + backfill"
```

---

### Task 3: Expose `categories` + `physical` in `/api/games`

**Files:**
- Modify: `app.py` (the `api_games()` per-row serializer, ~lines 143-161)
- Create: `tests/test_api_games.py`

- [ ] **Step 1: Write the failing API test**

Create `tests/test_api_games.py`:

```python
import models


def _ensure_platform(name, short, category):
    conn = models.get_db()
    conn.execute(
        "INSERT OR IGNORE INTO platforms (name, short_name, category) VALUES (?, ?, ?)",
        (name, short, category),
    )
    conn.commit()
    conn.close()


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
    conn.execute(
        "INSERT INTO game_platforms (game_id, platform_id) VALUES (?, ?)", (gid, pid)
    )
    conn.execute("INSERT INTO user_ratings (game_id, status) VALUES (?, 'backlog')", (gid,))
    if physical:
        conn.execute("INSERT OR IGNORE INTO tags (name, category) VALUES ('Physical', 'custom')")
        tid = conn.execute("SELECT id FROM tags WHERE name = 'Physical'").fetchone()[0]
        conn.execute("INSERT INTO game_tags (game_id, tag_id) VALUES (?, ?)", (gid, tid))
    conn.commit()
    conn.close()
    return gid


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
    assert by_title["Retro Game"]["categories"] == ["legacy_console"]
    assert by_title["Retro Game"]["physical"] is False
    assert by_title["Desktop Game"]["categories"] == ["pc"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_api_games.py -v`
Expected: FAIL with `KeyError: 'categories'`.

- [ ] **Step 3: Update the serializer**

In `app.py` `api_games()`, replace the "Get platforms for this game" block:

```python
        # Get platforms for this game
        platforms = conn.execute("""
            SELECT p.short_name
            FROM platforms p
            JOIN game_platforms gp ON gp.platform_id = p.id
            WHERE gp.game_id = ?
        """, (row['id'],)).fetchall()
        game['platforms'] = [p['short_name'] for p in platforms]
```

with:

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

Then, immediately after the existing `game['tags'] = [...]` line, add:

```python
        game['physical'] = any(t['name'] == 'Physical' for t in game['tags'])
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_api_games.py -v`
Expected: 1 passed.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_api_games.py
git commit -m "feat: expose platform categories + physical flag in /api/games"
```

---

### Task 4: Index page — stats hero, mode switcher, relocated filters, mode filtering

This task is verified manually in the browser (UI). It keeps the existing client-side
filter pattern and adds mode awareness. Visual polish (spacing/colors) is refined in-browser
against the approved mockup; the markup/JS below is the working baseline.

**Files:**
- Modify: `templates/index.html` (remove `nav_filters` block; new `content` block; new JS)

- [ ] **Step 1: Remove the nav-bar filter block**

Delete the entire `{% block nav_filters %} ... {% endblock %}` section (lines ~6-67). The
search + filters move into the page body in Step 2.

- [ ] **Step 2: Replace the `{% block content %}` with the hero + modes + filter row**

Replace the whole `{% block content %} ... {% endblock %}` with:

```html
{% block content %}
<!-- Stats hero: stats left, mode switcher right -->
<div class="rounded-2xl mb-6 p-5 bg-gradient-to-br from-[#241b3a] via-[#191522] to-[#151515] border border-gray-700/50 flex flex-wrap items-center gap-4">
    <div id="hero-stats" class="flex gap-6">
        <!-- Filled by renderHeroStats() -->
    </div>
    <div id="mode-switcher" class="ml-auto flex gap-1.5 bg-black/30 border border-gray-700/60 rounded-xl p-1.5">
        <!-- Filled by renderModeBar() -->
    </div>
</div>

<!-- Filter row -->
<div class="flex flex-wrap items-center gap-2 mb-6">
    <input type="text" id="search-input" placeholder="Search..."
           class="w-48 bg-surface rounded-lg border border-gray-600 px-3 py-1.5 text-white text-sm placeholder-gray-500 focus:border-accent focus:outline-none">

    <!-- Status Filter -->
    <div class="relative" id="status-dropdown">
        <button type="button" onclick="toggleDropdown('status')"
                class="bg-surface rounded-lg border border-gray-600 px-2 py-1.5 text-white text-sm flex items-center gap-1">
            <span id="status-label">Status</span>
            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
        </button>
        <div id="status-options" class="absolute z-50 mt-1 w-40 bg-surface-light border border-gray-700 rounded-lg shadow-lg hidden">
            <label class="flex items-center gap-2 px-3 py-1.5 hover:bg-surface cursor-pointer"><input type="checkbox" value="backlog" checked class="status-check accent-accent w-3.5 h-3.5"><span class="text-white text-sm">Backlog</span></label>
            <label class="flex items-center gap-2 px-3 py-1.5 hover:bg-surface cursor-pointer"><input type="checkbox" value="playing" checked class="status-check accent-accent w-3.5 h-3.5"><span class="text-white text-sm">Playing</span></label>
            <label class="flex items-center gap-2 px-3 py-1.5 hover:bg-surface cursor-pointer"><input type="checkbox" value="parked" checked class="status-check accent-accent w-3.5 h-3.5"><span class="text-white text-sm">Parked</span></label>
            <label class="flex items-center gap-2 px-3 py-1.5 hover:bg-surface cursor-pointer"><input type="checkbox" value="completed" checked class="status-check accent-accent w-3.5 h-3.5"><span class="text-white text-sm">Complete</span></label>
            <label class="flex items-center gap-2 px-3 py-1.5 hover:bg-surface cursor-pointer"><input type="checkbox" value="100" checked class="status-check accent-accent w-3.5 h-3.5"><span class="text-white text-sm">100%</span></label>
            <label class="flex items-center gap-2 px-3 py-1.5 hover:bg-surface cursor-pointer"><input type="checkbox" value="dropped" class="status-check accent-accent w-3.5 h-3.5"><span class="text-white text-sm">Dropped</span></label>
            <label class="flex items-center gap-2 px-3 py-1.5 hover:bg-surface cursor-pointer"><input type="checkbox" value="wishlist" class="status-check accent-accent w-3.5 h-3.5"><span class="text-white text-sm">Wishlist</span></label>
        </div>
    </div>

    <!-- Platform Filter -->
    <div class="relative" id="platform-dropdown">
        <button type="button" onclick="toggleDropdown('platform')"
                class="bg-surface rounded-lg border border-gray-600 px-2 py-1.5 text-white text-sm flex items-center gap-1">
            <span id="platform-label">Platforms</span>
            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
        </button>
        <div id="platform-options" class="absolute z-50 mt-1 w-40 bg-surface-light border border-gray-700 rounded-lg shadow-lg hidden"></div>
    </div>

    <!-- Sort -->
    <select id="sort-select" class="bg-surface rounded-lg border border-gray-600 px-2 py-1.5 text-white text-sm focus:border-accent focus:outline-none">
        <option value="title">Sort: Name</option>
        <option value="rating">Sort: Rating</option>
        <option value="priority">Sort: Priority</option>
        <option value="metacritic">Sort: Metacritic</option>
    </select>
</div>

<!-- Alphabet Bar - Fixed to left edge -->
<div id="alphabet-bar" class="hidden lg:flex fixed left-0 top-1/2 -translate-y-1/2 flex-col items-center py-4 px-1 z-30"></div>

<!-- Game Grid -->
<div id="games-grid" class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-8 gap-4 lg:ml-8"></div>

<div id="loading-state" class="text-center py-12"><div class="animate-pulse text-gray-500">Loading games...</div></div>
<div id="empty-state" class="hidden text-center py-12">
    <div class="text-6xl mb-4">🎮</div>
    <h3 class="text-xl font-medium text-gray-400" id="empty-title">No games found</h3>
    <p class="text-gray-500 mt-2" id="empty-subtitle">Try adjusting your filters</p>
</div>
{% endblock %}
```

- [ ] **Step 3: Add the mode + hero JS (top of the `{% block scripts %}` script)**

At the very top of the existing `<script>` in `{% block scripts %}` (just after `let allGames = [];`), add:

```javascript
    // ---- Library view modes ----
    const MODES = [
        { id: 'all',      label: 'All',     match: () => true },
        { id: 'modern',   label: 'Modern',  match: g => (g.categories || []).includes('modern_console') },
        { id: 'legacy',   label: 'Legacy',  match: g => (g.categories || []).includes('legacy_console') },
        { id: 'pc',       label: 'PC',      match: g => (g.categories || []).includes('pc') },
        { id: 'physical', label: 'Physical', match: g => !!g.physical },
    ];
    let currentMode = 'all';

    function modeCount(mode) {
        return allGames.filter(mode.match).length;
    }

    function renderModeBar() {
        const bar = document.getElementById('mode-switcher');
        bar.innerHTML = MODES.map(m => {
            const count = modeCount(m);
            const active = m.id === currentMode;
            const empty = count === 0;
            return `<button onclick="setMode('${m.id}')"
                class="px-3 py-1.5 rounded-lg text-sm whitespace-nowrap transition-colors
                       ${active ? 'bg-accent text-white' : 'text-gray-300 hover:bg-surface-light'}
                       ${empty && !active ? 'opacity-50' : ''}">
                ${m.label} <span class="opacity-60 text-xs">${count}</span>
            </button>`;
        }).join('');
    }

    function setMode(modeId) {
        currentMode = modeId;
        renderModeBar();
        filterAndRenderGames();
        saveViewState();
    }

    function renderHeroStats(stats) {
        const done = stats.total_games
            ? Math.round(((stats.by_status?.completed || 0) + (stats.by_status?.['100'] || 0)) / stats.total_games * 100)
            : 0;
        const tiles = [
            ['total_games', stats.total_games || 0, 'games'],
            ['completed', stats.by_status?.completed || 0, 'completed'],
            ['playing', stats.by_status?.playing || 0, 'playing'],
            ['backlog', stats.by_status?.backlog || 0, 'backlog'],
            ['done', done + '%', 'done'],
        ];
        document.getElementById('hero-stats').innerHTML = tiles.map(([, v, label]) =>
            `<div><div class="text-2xl font-bold text-white leading-none">${v}</div>
                  <div class="text-xs text-gray-400 mt-1">${label}</div></div>`
        ).join('');
    }

    async function loadHeroStats() {
        const stats = await api.get('/api/stats');
        if (stats) renderHeroStats(stats);
    }
```

- [ ] **Step 4: Wire mode + sort into `filterAndRenderGames` and the empty state**

Replace the existing `filterAndRenderGames()` function with:

```javascript
    function filterAndRenderGames() {
        const search = document.getElementById('search-input').value.toLowerCase();
        const statuses = getSelectedStatuses();
        const platforms = getSelectedPlatforms();
        const mode = MODES.find(m => m.id === currentMode) || MODES[0];
        const sort = document.getElementById('sort-select').value;

        let filtered = allGames.filter(game => {
            if (!mode.match(game)) return false;
            if (statuses.length > 0 && !statuses.includes(game.status || 'backlog')) return false;
            if (platforms.length > 0) {
                const gp = (game.platforms || []);
                if (!gp.some(p => platforms.includes(p))) return false;
            }
            if (search && !game.title.toLowerCase().includes(search)) return false;
            return true;
        });

        filtered = sortGames(filtered, sort);

        updateFilterLabels();
        renderGames(filtered);
        updateAlphabetBar(filtered);
        updateEmptyState(filtered.length, mode);
    }

    function sortGames(games, sort) {
        const arr = [...games];
        if (sort === 'rating')      arr.sort((a, b) => (b.rating || 0) - (a.rating || 0));
        else if (sort === 'priority') arr.sort((a, b) => (b.priority || 0) - (a.priority || 0));
        else if (sort === 'metacritic') arr.sort((a, b) => (b.metacritic_score || 0) - (a.metacritic_score || 0));
        else arr.sort((a, b) => (a.series_name || a.title).localeCompare(b.series_name || b.title));
        return arr;
    }

    function updateEmptyState(count, mode) {
        const empty = document.getElementById('empty-state');
        if (count > 0) { empty.classList.add('hidden'); return; }
        const emptyModes = {
            legacy: ['No legacy games yet', 'These fill in when you import your older-console libraries.'],
            pc: ['No PC games yet', 'These fill in when you import your Steam / GOG / Epic libraries.'],
        };
        const [title, subtitle] = emptyModes[mode.id] || ['No games found', 'Try adjusting your filters'];
        document.getElementById('empty-title').textContent = title;
        document.getElementById('empty-subtitle').textContent = subtitle;
        empty.classList.remove('hidden');
    }
```

- [ ] **Step 5: Rebuild mode counts after games load + register sort listener**

Replace the existing `loadGames()` with:

```javascript
    async function loadGames() {
        const games = await api.get('/api/games');
        allGames = games;
        renderModeBar();
        filterAndRenderGames();
    }
```

In the "Initialize" `DOMContentLoaded` handler at the bottom, change it to:

```javascript
    document.addEventListener('DOMContentLoaded', () => {
        restoreViewState();   // defined in Task 5
        loadPlatforms();
        loadHeroStats();
        loadGames();
        document.getElementById('sort-select').addEventListener('change', () => {
            filterAndRenderGames();
            saveViewState();   // defined in Task 5
        });
    });
```

> Note: `restoreViewState` / `saveViewState` are added in Task 5. To keep this task runnable on its own, add temporary no-op stubs near the top of the script now:
> ```javascript
>     function saveViewState() {}
>     function restoreViewState() {}
> ```
> Task 5 replaces these stubs with real implementations.

- [ ] **Step 6: Verify in the browser**

Run: `python app.py` (then open http://127.0.0.1:5000)
Expected: hero shows stats (598 games etc.); five mode buttons with counts (All 598, Modern 598, Legacy 0, PC 0, Physical 18); clicking a mode filters the grid; Legacy/PC show the friendly empty message; status/platform/search/sort still work within the active mode.

- [ ] **Step 7: Commit**

```bash
git add templates/index.html
git commit -m "feat: stats hero + 5-mode library switcher with mode-aware filtering"
```

---

### Task 5: Persist the active view in localStorage

**Files:**
- Modify: `templates/index.html` (replace the Task 4 stubs)

- [ ] **Step 1: Replace the `saveViewState` / `restoreViewState` stubs**

Replace the two stub functions from Task 4 with:

```javascript
    const VIEW_STATE_KEY = 'gametracker.viewState.v1';

    function saveViewState() {
        const state = {
            mode: currentMode,
            statuses: getSelectedStatuses(),
            platforms: getSelectedPlatforms(),
            sort: document.getElementById('sort-select').value,
        };
        try { localStorage.setItem(VIEW_STATE_KEY, JSON.stringify(state)); } catch (e) {}
    }

    function restoreViewState() {
        let state;
        try { state = JSON.parse(localStorage.getItem(VIEW_STATE_KEY)); } catch (e) { state = null; }
        if (!state) return;
        if (MODES.some(m => m.id === state.mode)) currentMode = state.mode;
        if (state.sort) {
            const sel = document.getElementById('sort-select');
            if ([...sel.options].some(o => o.value === state.sort)) sel.value = state.sort;
        }
        // Status/platform checkbox restoration happens after their checkboxes exist:
        window._pendingViewState = state;
    }

    function applyPendingCheckboxState() {
        const state = window._pendingViewState;
        if (!state) return;
        if (Array.isArray(state.statuses)) {
            document.querySelectorAll('.status-check').forEach(cb => {
                cb.checked = state.statuses.includes(cb.value);
            });
        }
        if (Array.isArray(state.platforms)) {
            document.querySelectorAll('.platform-check').forEach(cb => {
                cb.checked = state.platforms.includes(cb.value);
            });
        }
        window._pendingViewState = null;
    }
```

- [ ] **Step 2: Apply restored checkbox state once platforms load**

At the end of `loadPlatforms()` (after the event listeners are attached to `.platform-check`), add:

```javascript
        applyPendingCheckboxState();
        filterAndRenderGames();
```

- [ ] **Step 3: Save state when status/platform checkboxes change**

In the `loadPlatforms()` change-listener for `.platform-check`, change the handler to also save:

```javascript
        container.querySelectorAll('.platform-check').forEach(cb => {
            cb.addEventListener('change', () => { filterAndRenderGames(); saveViewState(); });
        });
```

And update the `.status-check` listener block near the bottom to:

```javascript
    document.querySelectorAll('.status-check').forEach(cb => {
        cb.addEventListener('change', () => { filterAndRenderGames(); saveViewState(); });
    });
```

And the search input listener to also persist:

```javascript
    document.getElementById('search-input').addEventListener('input', () => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(filterAndRenderGames, 300);
    });
```

(Search stays transient — intentionally not persisted.)

- [ ] **Step 4: Verify persistence in the browser**

Run: `python app.py` (open http://127.0.0.1:5000)
Expected: pick Modern + uncheck a status + change sort → reload → the same mode/filters/sort are restored. In DevTools, run `localStorage.removeItem('gametracker.viewState.v1')` → reload → defaults to All.

- [ ] **Step 5: Commit**

```bash
git add templates/index.html
git commit -m "feat: persist active view (mode/filters/sort) in localStorage"
```

---

### Task 6: Whole-app reskin in `base.html`

**Files:**
- Modify: `templates/base.html` (tokens, body, top bar). All other pages inherit.

- [ ] **Step 1: Update the design tokens + body**

In the `tailwind.config` `colors`, set the accent to the spec violet and refine surfaces:

```javascript
                    colors: {
                        surface: {
                            DEFAULT: '#1c1c1c',
                            light: '#202020',
                            lighter: '#2a2a2a'
                        },
                        accent: {
                            DEFAULT: '#6c5ce7',
                            hover: '#7d6df0'
                        }
                    }
```

Replace the `body` style rule:

```css
        body {
            background: #141414;
            min-height: 100vh;
        }
```

- [ ] **Step 2: Restyle the top bar (shell)**

Replace the `<nav> ... </nav>` block. Remove the `nav_filters` slot and the `#stats-summary`
element (stats now live in the index hero). Give nav links an accent underline when active:

```html
    <nav class="bg-surface-light border-b border-gray-800 sticky top-0 z-50">
        <div class="max-w-[1800px] mx-auto px-4 sm:px-6 lg:px-8">
            <div class="flex items-center justify-between h-16 gap-4">
                <div class="flex items-center space-x-6 flex-shrink-0">
                    <a href="/" class="text-xl font-bold text-white">
                        <span class="text-accent">Game</span>Tracker
                    </a>
                    <div class="hidden md:flex space-x-1">
                        <a href="/" class="px-3 py-1.5 rounded-md text-sm font-medium hover:bg-surface-lighter transition-colors {% block nav_library %}{% endblock %}">Library</a>
                        <a href="/series" class="px-3 py-1.5 rounded-md text-sm font-medium hover:bg-surface-lighter transition-colors {% block nav_series %}{% endblock %}">Series</a>
                        <a href="/recommendations" class="px-3 py-1.5 rounded-md text-sm font-medium hover:bg-surface-lighter transition-colors {% block nav_recommendations %}{% endblock %}">Picks</a>
                        <a href="/settings" class="px-3 py-1.5 rounded-md text-sm font-medium hover:bg-surface-lighter transition-colors {% block nav_settings %}{% endblock %}">Settings</a>
                    </div>
                </div>
                <div class="flex items-center flex-shrink-0">
                    <button onclick="openAddGameModal()" class="px-3 py-2 bg-accent hover:bg-accent-hover rounded-lg text-white text-sm font-medium transition-colors whitespace-nowrap">
                        + Add Game
                    </button>
                </div>
            </div>
        </div>
    </nav>
```

- [ ] **Step 3: Remove the now-dead `loadNavStats` usage**

In `base.html`, the `DOMContentLoaded` handler calls `loadNavStats()`. Since `#stats-summary`
no longer exists, make `loadNavStats` a safe no-op-if-missing. Change its body to guard:

```javascript
        async function loadNavStats() {
            const el = document.getElementById('stats-summary');
            if (!el) return;
            const stats = await api.get('/api/stats');
            if (stats) {
                el.innerHTML = `
                    <span><strong class="text-white">${stats.total_games}</strong> games</span>
                `;
            }
        }
```

(Index uses `loadHeroStats`; other pages simply show no inline stats. Leaving the guarded
function avoids touching every page's init.)

- [ ] **Step 4: Verify the reskin across all pages**

Run: `python app.py`
Expected: Library, Series, Picks, and Settings all render with the new violet accent, darker
surfaces, and restyled top bar with the active link highlighted. No console errors. The index
hero/mode bar still works.

- [ ] **Step 5: Commit**

```bash
git add templates/base.html
git commit -m "feat: whole-app reskin — centralized tokens + restyled shell"
```

---

### Task 7: Spot-fix inheriting pages + full regression

**Files:**
- Modify (only if a clash is found): `templates/series.html`, `templates/recommendations.html`, `templates/settings.html`

- [ ] **Step 1: Visually check each inheriting page**

Run: `python app.py` and open each of `/series`, `/recommendations`, `/settings`.
Look for: hard-coded old colors (e.g., `#1a1a2e`, `#7c3aed`) that now clash, broken contrast,
or layout that assumed the old nav filters/stats. Note any issues.

- [ ] **Step 2: Fix any clashes found**

For each clash, replace hard-coded legacy hex values with the token classes
(`bg-surface`, `bg-surface-light`, `text-accent`, etc.). If no clashes are found, record
"no changes needed" and skip to Step 3. (These pages extend `base.html`, so most styling is
already inherited.)

- [ ] **Step 3: Run the full automated suite**

Run: `python -m pytest -v`
Expected: all tests pass.

- [ ] **Step 4: Manual regression checklist (browser)**

Verify each still works:
- Add a game (modal → IGDB search → save) ; new game appears; hero/mode counts update on reload.
- Open a game, toggle Physical → Physical mode count changes after reload; disc icon shows.
- Edit status/rating/platforms; delete a game.
- Series page Kanban drag-and-drop; Picks page recommendations render; Settings cover-fetch UI.
- Alphabet rail jumps to letters within the active mode.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: reskin spot-fixes for series/recommendations/settings + regression pass"
```

---

## Self-Review

**Spec coverage:**
- Five modes (All/Modern/Legacy/PC/Physical) → Task 4 (`MODES`, `setMode`, `filterAndRenderGames`). ✓
- Legacy stays out of Modern (era classification) → Tasks 1-3 (`classify_platform`, migration, API). ✓
- `platforms.category` data-driven model → Tasks 1-2. ✓
- Stats hero with mode switcher right-aligned opposite stats → Task 4 Step 2 markup. ✓
- Filters within active mode + Sort → Task 4 Steps 2/4. ✓
- localStorage persistence (mode/filters/sort; search transient; default All) → Task 5. ✓
- Whole-app reskin via shared shell → Task 6 (+ Task 7 spot-fixes). ✓
- Empty states for Legacy/PC → Task 4 Step 4 (`updateEmptyState`). ✓
- Idempotent migration + tests → Task 2. ✓
- **Deviation noted:** the spec's "affected components" listed `import_data.py` (set category when
  creating platform rows). The current importer never *creates* platform rows (it only links to
  the seeded ones), so no change is needed now; `classify_platform` is exported and ready for the
  scraping sub-project, which is where new platform rows get created. No task — intentional.
- **Deviation noted:** the spec mentioned a "Rating" filter in the filter row. To avoid scope
  creep this plan ships Status/Platform/Search/**Sort** (Sort added per the mockup). A Rating
  filter is deferred; flag to the user during review if it's wanted in v1.

**Placeholder scan:** No "TBD"/"implement later". Task 4 introduces explicit temporary stubs that
Task 5 replaces — called out in both tasks.

**Type/name consistency:** `classify_platform`, `migrate_platform_category`,
`MODES`/`currentMode`/`setMode`/`modeCount`/`renderModeBar`, `saveViewState`/`restoreViewState`/
`applyPendingCheckboxState`, `VIEW_STATE_KEY`, `loadHeroStats`/`renderHeroStats`,
`game.categories`/`game.physical` are used consistently across tasks.
```
