# Create Series From Group (Thread B, core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** From a dedup group, create a new series (or add the group's games to an existing one) via a confirm dialog, persisting new series names to a durable, per-user pattern table so a re-import reproduces them.

**Architecture:** Move the hardcoded `known_series` map in `models.py` into a committed `series_patterns.default.json` seed plus a gitignored per-user `series_patterns.json` (load/add helpers). `/api/duplicates` groups gain `existing_series_id`/`existing_series_name` (from members' current `series_id`) so the modal shows "Create series" vs "Add to series". A new `POST /api/series/from-group` creates-or-finds the series, assigns the chosen games in order, and optionally remembers the pattern. The dedup modal gets a per-group button + a confirm dialog.

**Tech Stack:** Python 3 / Flask / sqlite3, vanilla JS + Tailwind in `templates/base.html`, pytest. Spec: `docs/superpowers/specs/2026-05-23-dedup-grouping-design.md` (sections B, B2). This is the **core**; IGDB name-canonicalization and "order by release" are a deliberate fast-follow.

**Guardrails for implementers:** Branch `feature/cover-art-igdb` is checked out — do NOT create a worktree, switch branches, or push. Conventional commits, NO co-author trailer. Verify with `python -m pytest` (temp-DB fixtures) and `ruff check .` ONLY — do NOT start the dev server, POST to the running app, or call mutating functions against the real `games.db`. Never touch `games.db`/`config.json`/`*.db`.

---

## File structure

- **Modify** `models.py` — extract `known_series` → `series_patterns.default.json`; add `SERIES_PATTERNS_PATH`, `SERIES_PATTERNS_DEFAULT_PATH`, `load_series_patterns()`, `add_series_pattern()`; repoint `auto_populate_series()`.
- **Create** `series_patterns.default.json` (committed seed, generated from the current map).
- **Modify** `.gitignore` — ignore the per-user `series_patterns.json`.
- **Modify** `app.py` — enrich `/api/duplicates` groups; add `POST /api/series/from-group` (import `add_series_pattern`).
- **Modify** `templates/base.html` — per-group "Create/Add to series" button + `#series-dialog` + JS.
- **Create** `tests/test_series_patterns.py`; **Modify** `tests/test_api_games.py`.

---

## Task 1: Series patterns → data file + load/add helpers

**Files:**
- Modify: `models.py` (`auto_populate_series` ~449-643; add helpers near top, after `DB_PATH`)
- Create: `series_patterns.default.json`
- Modify: `.gitignore`
- Test: `tests/test_series_patterns.py`

- [ ] **Step 1: Write failing tests** in a new file `tests/test_series_patterns.py`:

```python
import json

import models


def _write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def test_load_falls_back_to_default(tmp_path, monkeypatch):
    default = tmp_path / "series_patterns.default.json"
    user = tmp_path / "series_patterns.json"
    _write(default, {"Halo": "Halo"})
    monkeypatch.setattr(models, "SERIES_PATTERNS_DEFAULT_PATH", default)
    monkeypatch.setattr(models, "SERIES_PATTERNS_PATH", user)
    assert models.load_series_patterns() == {"Halo": "Halo"}


def test_load_prefers_user_file(tmp_path, monkeypatch):
    default = tmp_path / "series_patterns.default.json"
    user = tmp_path / "series_patterns.json"
    _write(default, {"Halo": "Halo"})
    _write(user, {"Halo": "Halo", "Doom": "DOOM"})
    monkeypatch.setattr(models, "SERIES_PATTERNS_DEFAULT_PATH", default)
    monkeypatch.setattr(models, "SERIES_PATTERNS_PATH", user)
    assert models.load_series_patterns() == {"Halo": "Halo", "Doom": "DOOM"}


def test_add_seeds_from_default_and_is_idempotent(tmp_path, monkeypatch):
    default = tmp_path / "series_patterns.default.json"
    user = tmp_path / "series_patterns.json"
    _write(default, {"Halo": "Halo"})
    monkeypatch.setattr(models, "SERIES_PATTERNS_DEFAULT_PATH", default)
    monkeypatch.setattr(models, "SERIES_PATTERNS_PATH", user)

    assert models.add_series_pattern("SteamWorld", "SteamWorld") is True
    saved = json.loads(user.read_text(encoding="utf-8"))
    assert saved == {"Halo": "Halo", "SteamWorld": "SteamWorld"}  # default seeded in
    # second time is a no-op
    assert models.add_series_pattern("SteamWorld", "SteamWorld") is False


def test_add_rejects_blank(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "SERIES_PATTERNS_DEFAULT_PATH", tmp_path / "d.json")
    monkeypatch.setattr(models, "SERIES_PATTERNS_PATH", tmp_path / "u.json")
    assert models.add_series_pattern("", "x") is False
    assert models.add_series_pattern("x", "  ") is False
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_series_patterns.py -v`
Expected: FAIL — `AttributeError: module 'models' has no attribute 'load_series_patterns'`.

- [ ] **Step 3: Add `json` import + paths + helpers to `models.py`**

At the top of `models.py`, change the imports (currently `import sqlite3` / `from pathlib import Path`) to also import `json`:

```python
import json
import sqlite3
from pathlib import Path
```

Immediately after the `DB_PATH = Path(__file__).parent / "games.db"` line, add:

```python
SERIES_PATTERNS_PATH = Path(__file__).parent / "series_patterns.json"           # per-user (gitignored)
SERIES_PATTERNS_DEFAULT_PATH = Path(__file__).parent / "series_patterns.default.json"  # committed seed


def load_series_patterns() -> dict:
    """Load the prefix->series-name table (per-user file, else committed seed)."""
    path = SERIES_PATTERNS_PATH if SERIES_PATTERNS_PATH.exists() else SERIES_PATTERNS_DEFAULT_PATH
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def add_series_pattern(prefix: str, name: str) -> bool:
    """Add prefix->name to the per-user patterns file (seeding from the default on
    first write). Returns True if added, False if blank or already present."""
    prefix, name = prefix.strip(), name.strip()
    if not prefix or not name:
        return False
    patterns = dict(load_series_patterns())
    if patterns.get(prefix) == name:
        return False
    patterns[prefix] = name
    with open(SERIES_PATTERNS_PATH, "w", encoding="utf-8") as f:
        json.dump(patterns, f, indent=2, ensure_ascii=False, sort_keys=True)
    return True
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_series_patterns.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Extract the hardcoded map into the committed seed**

In `models.py`, find the `known_series = { ... }` dict literal inside `auto_populate_series()` (it begins `known_series = {` near line 454 and ends with the closing `}` around line 571). **Cut** that entire dict and **paste it at module scope** (e.g. just above `def auto_populate_series():`) renamed to `_SEED_SERIES_PATTERNS`:

```python
_SEED_SERIES_PATTERNS = {
    # ...exact same key/value pairs that were in known_series...
}
```

Then inside `auto_populate_series()`, where the literal used to be, set:

```python
    known_series = load_series_patterns()
```

- [ ] **Step 6: Generate `series_patterns.default.json` from the seed**

Run (from the project root):
```bash
python -c "import json, models; json.dump(models._SEED_SERIES_PATTERNS, open('series_patterns.default.json','w',encoding='utf-8'), indent=2, ensure_ascii=False, sort_keys=True)"
```
Then verify the count matches the original map:
```bash
python -c "import json; print(len(json.load(open('series_patterns.default.json',encoding='utf-8'))), 'patterns')"
```
Expected: prints the same number of entries the dict had (a number > 100). Open the file to confirm it is valid JSON with the franchise prefixes (e.g. "Final Fantasy", "Assassin's Creed").

- [ ] **Step 7: Remove the now-redundant seed constant**

Now that `series_patterns.default.json` exists and `load_series_patterns()` reads it, delete the `_SEED_SERIES_PATTERNS = { ... }` module constant from `models.py` (the JSON file is the source of truth). Confirm `auto_populate_series()` still references `load_series_patterns()` only.

- [ ] **Step 8: Ignore the per-user file**

In `.gitignore`, under the "Personal library data" section, add a line:
```
series_patterns.json
```
(Do NOT ignore `series_patterns.default.json` — it must be committed.) Verify:
```bash
git check-ignore series_patterns.json && echo IGNORED
git check-ignore series_patterns.default.json || echo TRACKED_OK
```
Expected: prints `IGNORED` then `TRACKED_OK`.

- [ ] **Step 9: Run the full suite + lint**

Run: `python -m pytest -q` → expect all green (128 + 4 new = 132).
Run: `ruff check .` → expect clean.

- [ ] **Step 10: Commit**

```bash
git add models.py series_patterns.default.json .gitignore tests/test_series_patterns.py
git commit -m "refactor: series patterns to extensible data file (committed default + per-user)"
```

---

## Task 2: `/api/duplicates` groups expose existing series

**Files:**
- Modify: `app.py` (`api_duplicates`, lines ~411-438)
- Test: `tests/test_api_games.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_api_games.py`:

```python
def test_duplicates_groups_expose_existing_series(client):
    conn = models.get_db()
    conn.executemany(
        "INSERT INTO games (title, normalized_title) VALUES (?, ?)",
        [("Don't Starve", "dont starve"),
         ("Don't Starve: Console Edition", "dont starve console edition"),
         ("SteamWorld Dig", "steamworld dig"),
         ("SteamWorld Dig 2", "steamworld dig 2")],
    )
    rows = {r["title"]: r["id"] for r in conn.execute("SELECT id, title FROM games")}
    conn.execute("INSERT INTO series (name) VALUES ('Don''t Starve')")
    sid = conn.execute("SELECT id FROM series WHERE name = 'Don''t Starve'").fetchone()["id"]
    conn.execute("INSERT INTO user_ratings (game_id, series_id, series_order) VALUES (?, ?, 0)",
                 (rows["Don't Starve"], sid))
    conn.commit()
    conn.close()

    groups = client.get("/api/duplicates").get_json()["groups"]
    by_member = {tuple(g["members"]): g for g in groups}
    ds = next(g for g in groups if rows["Don't Starve"] in g["members"])
    sw = next(g for g in groups if rows["SteamWorld Dig"] in g["members"])
    assert ds["existing_series_id"] == sid
    assert ds["existing_series_name"] == "Don't Starve"
    assert sw["existing_series_id"] is None and sw["existing_series_name"] is None
```

Note: `SteamWorld Dig` vs `SteamWorld Dig 2` differ only by a number, so they are NOT a candidate pair — instead `SteamWorld Dig` pairs with... nothing here. To guarantee a SteamWorld group, the test relies on the contains link: adjust the fixture so the SteamWorld pair groups via a shared subtitle. Replace the two SteamWorld rows with:
`("SteamWorld Heist", "steamworld heist"), ("SteamWorld Heist: Ultimate Edition", "steamworld heist ultimate edition")` — these match via the edition rule and form a group with no series.

Use this corrected fixture in the test (SteamWorld Heist + its Ultimate Edition), and reference `rows["SteamWorld Heist"]` for `sw`.

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_api_games.py::test_duplicates_groups_expose_existing_series -v`
Expected: FAIL — groups have no `existing_series_id` key (KeyError).

- [ ] **Step 3: Enrich the groups in `api_duplicates`**

In `app.py`, replace the tail of `api_duplicates` (from `conn.close()` through the `return jsonify(...)`, lines ~434-438) with:

```python
    grouped = dedup.group_candidates(groups["candidates"])
    for g in grouped:
        placeholders = ",".join("?" * len(g["members"]))
        rows = conn.execute(
            f"SELECT s.id, s.name FROM user_ratings ur JOIN series s ON s.id = ur.series_id "
            f"WHERE ur.game_id IN ({placeholders})", g["members"]).fetchall()
        if rows:
            counts = Counter((r["id"], r["name"]) for r in rows)
            (sid, sname), _ = counts.most_common(1)[0]
            g["existing_series_id"], g["existing_series_name"] = sid, sname
        else:
            g["existing_series_id"], g["existing_series_name"] = None, None
    conn.close()
    return jsonify({"definite": groups["definite"],
                    "candidates": groups["candidates"],
                    "groups": grouped, "games": games})
```

At the top of `app.py`, add `from collections import Counter` (place it with the other imports, after `import sqlite3`).

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_api_games.py::test_duplicates_groups_expose_existing_series -v`
Expected: PASS. Also run the existing groups test: `python -m pytest tests/test_api_games.py -k duplicates -v` (all green).

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_api_games.py
git commit -m "feat: dedup groups expose existing series id/name"
```

---

## Task 3: `POST /api/series/from-group`

**Files:**
- Modify: `app.py` (add endpoint near the other `/api/series` routes; import `add_series_pattern`)
- Test: `tests/test_api_games.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_api_games.py`:

```python
def test_from_group_creates_series_and_assigns(client):
    conn = models.get_db()
    conn.executemany("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                     [("SteamWorld Heist", "steamworld heist"),
                      ("SteamWorld Dig", "steamworld dig")])
    rows = {r["title"]: r["id"] for r in conn.execute("SELECT id, title FROM games")}
    conn.commit()
    conn.close()

    resp = client.post("/api/series/from-group", json={
        "name": "SteamWorld",
        "game_ids": [rows["SteamWorld Heist"], rows["SteamWorld Dig"]],
        "remember": False})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["created"] is True and body["assigned"] == 2

    conn = models.get_db()
    sid = conn.execute("SELECT id FROM series WHERE name = 'SteamWorld'").fetchone()["id"]
    n = conn.execute("SELECT COUNT(*) FROM user_ratings WHERE series_id = ?", (sid,)).fetchone()[0]
    conn.close()
    assert n == 2


def test_from_group_finds_existing_and_skips_already_assigned(client):
    conn = models.get_db()
    conn.executemany("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                     [("Halo", "halo"), ("Halo 2", "halo 2")])
    rows = {r["title"]: r["id"] for r in conn.execute("SELECT id, title FROM games")}
    conn.execute("INSERT INTO series (name) VALUES ('Halo')")
    sid = conn.execute("SELECT id FROM series WHERE name = 'Halo'").fetchone()["id"]
    conn.execute("INSERT INTO user_ratings (game_id, series_id, series_order) VALUES (?, ?, 0)",
                 (rows["Halo"], sid))
    conn.commit()
    conn.close()

    resp = client.post("/api/series/from-group", json={
        "name": "halo",  # case-insensitive find
        "game_ids": [rows["Halo"], rows["Halo 2"]], "remember": False})
    body = resp.get_json()
    assert body["created"] is False and body["series_id"] == sid
    assert body["assigned"] == 1  # Halo already in the series; only Halo 2 added


def test_from_group_remember_writes_pattern(client, tmp_path, monkeypatch):
    monkeypatch.setattr(models, "SERIES_PATTERNS_PATH", tmp_path / "series_patterns.json")
    monkeypatch.setattr(models, "SERIES_PATTERNS_DEFAULT_PATH", tmp_path / "missing.json")
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('Ori', 'ori')")
    gid = conn.execute("SELECT id FROM games").fetchone()["id"]
    conn.commit()
    conn.close()

    client.post("/api/series/from-group",
                json={"name": "Ori", "game_ids": [gid], "remember": True})
    assert models.load_series_patterns().get("Ori") == "Ori"


def test_from_group_requires_name_and_games(client):
    assert client.post("/api/series/from-group", json={"name": "", "game_ids": [1]}).status_code == 400
    assert client.post("/api/series/from-group", json={"name": "X", "game_ids": []}).status_code == 400
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_api_games.py -k from_group -v`
Expected: FAIL — 404 (route not defined) / assertion errors.

- [ ] **Step 3: Add the endpoint**

In `app.py`, add `add_series_pattern` to the `from models import (...)` block (append it to the imported names). Then add this route next to the other `/api/series` routes (e.g. after `api_create_series`):

```python
@app.route('/api/series/from-group', methods=['POST'])
def api_series_from_group():
    """Create-or-find a series by name and assign the given games to it (in order).

    {name, game_ids, remember} -> {success, series_id, created, assigned}. When
    `remember`, the name is added to the durable per-user series-pattern table so
    a future import reproduces it.
    """
    data = request.json or {}
    name = (data.get('name') or '').strip()
    game_ids = data.get('game_ids') or []
    if not name or not game_ids:
        return jsonify({'error': 'name and game_ids are required'}), 400

    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM series WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
        if row:
            series_id, created = row['id'], False
        else:
            conn.execute("INSERT INTO series (name) VALUES (?)", (name,))
            series_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            created = True

        order = conn.execute(
            "SELECT MAX(series_order) FROM user_ratings WHERE series_id = ?", (series_id,)
        ).fetchone()[0] or 0
        assigned = 0
        for gid in game_ids:
            cur = conn.execute("SELECT series_id FROM user_ratings WHERE game_id = ?", (gid,)).fetchone()
            if cur and cur['series_id'] == series_id:
                continue  # already in this series
            order += 1
            conn.execute(
                "INSERT INTO user_ratings (game_id, series_id, series_order) VALUES (?, ?, ?) "
                "ON CONFLICT(game_id) DO UPDATE SET series_id = excluded.series_id, "
                "series_order = excluded.series_order, updated_at = CURRENT_TIMESTAMP",
                (gid, series_id, order))
            assigned += 1
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        conn.close()

    if data.get('remember'):
        add_series_pattern(name, name)

    return jsonify({'success': True, 'series_id': series_id,
                    'created': created, 'assigned': assigned})
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_api_games.py -k from_group -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_api_games.py
git commit -m "feat: POST /api/series/from-group (create-or-find, assign, remember)"
```

---

## Task 4: Modal — "Create / Add to series" button + confirm dialog

Frontend; verified live (no JS test harness). Edit `templates/base.html`.

**Files:**
- Modify: `templates/base.html` — add `#series-dialog` markup near `#dedup-modal`; replace `groupCard`; add dialog JS.

- [ ] **Step 1: Add the dialog markup**

Immediately after the closing `</div>` of the `#dedup-modal` block (the line right before `<script>`), add:

```html
    <!-- Create / Add Series dialog -->
    <div id="series-dialog" class="fixed inset-0 bg-black/70 z-[60] hidden items-center justify-center p-4">
        <div class="bg-surface-light rounded-xl max-w-lg w-full max-h-[85vh] overflow-y-auto shadow-2xl">
            <div class="p-6" id="series-dialog-body"></div>
        </div>
    </div>
```

- [ ] **Step 2: Replace `groupCard` to include the series button**

Replace the existing `groupCard(idx)` function with:

```javascript
        function groupCard(idx) {
            const g = dedupData.groups[idx];
            const members = activeMembers(idx);
            const label = baseTitle(members);
            const seriesLabel = g.existing_series_id ? 'Add to series' : 'Create series';
            return `<div class="bg-surface rounded-lg" id="grp-${idx}">
                <div class="flex items-center justify-between p-3 gap-2">
                    <button onclick="toggleGroup(${idx})" class="flex items-center gap-2 text-left text-white text-sm font-medium min-w-0">
                        <span id="grp-caret-${idx}">▸</span>
                        <span class="truncate">${escapeHtml(label)}</span>
                        <span class="text-gray-500 whitespace-nowrap">· <span id="grp-count-${idx}">${members.length}</span> games</span>
                    </button>
                    <div class="flex gap-2 shrink-0">
                        <button onclick="openSeriesDialog(${idx})" class="px-2 py-1 text-xs bg-surface-light hover:bg-surface-lighter border border-gray-600 rounded text-white whitespace-nowrap">${seriesLabel}</button>
                        <button onclick="markAllSafe(${idx})" class="px-2 py-1 text-xs bg-surface-light hover:bg-surface-lighter border border-gray-600 rounded text-white whitespace-nowrap">Mark all safe</button>
                    </div>
                </div>
                <div id="grp-body-${idx}" class="hidden px-3 pb-3 space-y-2"></div>
            </div>`;
        }
```

- [ ] **Step 3: Add the dialog JS**

Add these functions alongside the other dedup functions (e.g. after `confirmMergeSelected`):

```javascript
        function openSeriesDialog(idx) {
            const g = dedupData.groups[idx];
            const members = activeMembers(idx);
            const existing = g.existing_series_id;
            const name = existing ? (g.existing_series_name || '') : baseTitle(members);
            const heading = existing ? 'Add to series' : 'Create series';
            const rememberRow = existing ? '' : `
                <label class="flex items-center gap-2 text-sm text-gray-300 mt-3">
                    <input type="checkbox" id="series-remember" checked> Remember for future imports
                </label>`;
            document.getElementById('series-dialog-body').innerHTML = `
                <div class="flex items-center justify-between mb-4">
                    <h2 class="text-lg font-bold text-white">${heading}</h2>
                    <button onclick="closeSeriesDialog()" class="text-white/70 hover:text-white text-2xl">&times;</button>
                </div>
                <label class="block text-xs text-gray-400 mb-1">Series name</label>
                <input id="series-name" type="text" value="${escapeHtml(name)}"
                       class="w-full bg-surface border border-gray-600 rounded px-2 py-1.5 text-white text-sm mb-3">
                <label class="block text-xs text-gray-400 mb-1">Include games</label>
                <div class="space-y-1 max-h-64 overflow-y-auto">
                    ${members.map(id => `<label class="flex items-center gap-2 text-sm text-white">
                        <input type="checkbox" class="series-member" value="${id}" checked>
                        ${escapeHtml(dedupGame(id).title)}</label>`).join('')}
                </div>
                ${rememberRow}
                <div class="flex gap-2 pt-4">
                    <button onclick="confirmSeriesFromGroup(${idx})" class="px-3 py-1.5 bg-accent hover:bg-accent-hover rounded-lg text-white text-sm">${existing ? 'Add' : 'Create'}</button>
                    <button onclick="closeSeriesDialog()" class="px-3 py-1.5 bg-surface hover:bg-surface-lighter border border-gray-600 rounded-lg text-white text-sm">Cancel</button>
                </div>`;
            showModalEl('series-dialog');
        }

        function closeSeriesDialog() { hideModalEl('series-dialog'); }

        async function confirmSeriesFromGroup(idx) {
            if (dedupBusy) return;
            const name = document.getElementById('series-name').value.trim();
            if (!name) { alert('Series name is required.'); return; }
            const gameIds = [...document.querySelectorAll('.series-member:checked')].map(c => parseInt(c.value));
            if (!gameIds.length) { alert('Select at least one game.'); return; }
            const rememberEl = document.getElementById('series-remember');
            const remember = rememberEl ? rememberEl.checked : false;
            dedupBusy = true;
            try {
                const res = await api.post('/api/series/from-group', { name, game_ids: gameIds, remember });
                if (!res.ok) { alert(res.data.error || 'Could not save series'); return; }
                closeSeriesDialog();
                dedupData.groups[idx].existing_series_id = res.data.series_id;
                dedupData.groups[idx].existing_series_name = name;
                const card = document.getElementById(`grp-${idx}`);
                if (card) card.outerHTML = groupCard(idx);  // flips button to "Add to series"
                if (typeof refreshGameList === 'function') refreshGameList();
                alert(`Saved "${name}" — ${res.data.assigned} game${res.data.assigned === 1 ? '' : 's'} ${res.data.created ? 'in a new series' : 'added'}.`);
            } finally {
                dedupBusy = false;
            }
        }
```

- [ ] **Step 4: Confirm the suite still passes**

Run: `python -m pytest -q`
Expected: all green (template change does not affect Python tests; 132+ passing).

- [ ] **Step 5: Static self-review**

Read the dedup `<script>` block. Confirm: `openSeriesDialog`, `closeSeriesDialog`, `confirmSeriesFromGroup` are defined once each; `groupCard` references `g.existing_series_id`/`g.existing_series_name`; `showModalEl`/`hideModalEl` handle `series-dialog`; template literals balanced. Do NOT start the server.

- [ ] **Step 6: Commit**

```bash
git add templates/base.html
git commit -m "feat: create/add-to-series button and dialog in dedup modal"
```

---

## Task 5: Full verification

- [ ] **Step 1: Whole suite** — Run: `python -m pytest` → expect all green (128 prior + 4 patterns + 1 enrichment + 4 from-group = 137).
- [ ] **Step 2: Lint** — Run: `ruff check .` → clean.
- [ ] **Step 3: Confirm no stray files staged** — Run: `git status --short` (clean) and `git check-ignore series_patterns.json` (prints the path). Confirm `series_patterns.default.json` IS committed and `series_patterns.json` is NOT.
- [ ] **Step 4: Hand back for live feel** — Stop for the user to: open Dedup (hard-refresh), see **Create series** on SteamWorld/other un-seriesed groups and **Add to series** on existing ones, create a series, confirm it appears on the Series page and that "remember" wrote `series_patterns.json`. Do NOT push/PR. IGDB name-canonicalization + "order by release" are the next iteration.

---

## Self-review notes (author)

- **Spec coverage (Thread B core):** durable per-user patterns + committed default + `add_series_pattern` (Task 1) ✓; existing-series detection drives Create vs Add (Task 2) ✓; create-or-find + assign + remember (Task 3) ✓; button + confirm dialog with member checklist + remember (Task 4) ✓. Deferred (noted): IGDB `igdb-suggest` canonical naming and "order by release" — fast-follow.
- **Type consistency:** group objects gain `existing_series_id`/`existing_series_name` (Task 2) consumed verbatim in `groupCard`/`openSeriesDialog` (Task 4). `from-group` contract `{name, game_ids, remember}` → `{success, series_id, created, assigned}` matches the JS caller. `load_series_patterns`/`add_series_pattern` names match across models, app import, and tests.
- **No placeholders:** every code step is complete; the `known_series` extraction is mechanical (cut/paste/generate) with a count check; frontend is live-verified (no JS harness), consistent with Thread A.
- **Guardrail:** all verification is pytest temp-DB + ruff; implementers must not touch the live app or real `games.db` (see [[subagent-impl-never-touch-live-db]]).
