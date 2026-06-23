# Scan-for-Info Core — Plan 2: Web Format + Category Editors

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the canonical web editor (Flask + `templates/base.html`) the minimal UI to set per-platform physical/digital `format` and to manage `mobile`/`subscription` platform membership, backed by an enriched game-detail payload and a non-destructive format-setting API.

**Architecture:** Pure Flask/SQLite backend + vanilla-JS-in-Jinja frontend (no build step; all UI logic lives inline in `templates/base.html`). Plan 1 already added the `game_platforms.format` column, `platforms.has_digital_market`, and the `mobile`/`subscription` category seeds — this plan only surfaces and edits them. The web app is the canonical editor; mobile (Plan 3) consumes this data read-only.

**Tech Stack:** Python 3.11, SQLite (`sqlite3`), Flask, Jinja2, vanilla JS, Tailwind utility classes. pytest for backend. Package/env via `uv`.

## Global Constraints

- Tests: `uv run python -m pytest` (NOT plain `pytest`). Lint: `ruff check` ONLY (never `ruff format`).
- Tests use the pytest temp DB (`temp_db`/`client` fixtures); NEVER touch the live `games.db` or the running server on :5000.
- Type hints on all new Python function signatures. Use the existing `app.py` patterns (it uses `print` in migrations; route code raises/returns JSON as the surrounding handlers do).
- Named constants for enums/lookups at module scope where new ones are introduced.
- **Display rule (spec §2.3):** the `(Physical/Digital)` qualifier is shown only when `has_digital_market = 1`. Cartridge/disc-only legacy platforms have NO format choice in the editor.
- **DO NOT change the platform membership / removal semantics** of the existing `PUT /api/games/<id>` `platforms`-replace path (the owner explicitly said to leave web platform removal alone — see memory `web-main-mobile-streamlined`). This plan only *preserves `format` across* that path and *adds* new branches; it must not alter which platforms get inserted or deleted.
- Frontend changes (`base.html` JS) have no pytest coverage. Their verification is a **controller-run browser check** (Playwright + Chrome against a COPY of `games.db` on an alternate port — see memory `verify-ui-changes-yourself`), performed in the gate task. Subagents implementing frontend tasks do static work only.
- Semantic rule (carried from Plan 1): `barcode_registry` = "what game is this UPC" (knowledge); `game_platforms` = ownership. Setting a format never changes ownership and vice-versa.

---

## File Structure

- `app.py` —
  - `api_game()` (GET `/api/games/<id>`, ~304-365): enrich the platforms sub-query with `category`, `has_digital_market`, `gp.format`.
  - `api_update_game()` (PUT `/api/games/<id>`, ~832-965): (a) preserve `format` across the existing `platforms`-replace path; (b) add a `platform_formats` setter branch.
- `templates/base.html` —
  - `renderPlatformCheckboxes(game)` (~847-881) is left intact for membership; add two new render helpers `renderPlatformFormats(game)` and `renderExtraPlatforms(game)` next to it (~882).
  - `loadGameModal()` Platforms block (~1117-1129): render the two new helpers.
  - `togglePlatform()` / `setLegacyPlatform()` (~1488-1515): append a `loadGameModal(gameId)` re-render so the Format list tracks membership changes (re-render only — the PUT logic is untouched).
  - New JS: `renderPlatformFormats`, `renderExtraPlatforms`, `setPlatformFormat`.
- `tests/test_api_games.py` — backend coverage for the enriched GET and the two PUT behaviors.

---

## Task 1: Enrich the game-detail platforms payload

**Files:**
- Modify: `app.py` — `api_game()` platforms sub-query (~334-341)
- Test: `tests/test_api_games.py`

**Interfaces:**
- Produces: `GET /api/games/<id>` `platforms[]` entries now carry `category` (str), `has_digital_market` (int 0/1) and `format` (str `'physical'|'digital'` or null) in addition to the existing `id`, `name`, `short_name`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api_games.py`:
```python
def test_game_detail_platforms_include_format_and_market(client):
    import models
    conn = models.get_db()
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (1, 'A', 'a')")
    conn.execute("INSERT INTO platforms (name, short_name, category, has_digital_market) "
                 "VALUES ('PlayStation 5', 'PS5', 'modern_console', 1)")
    pid = conn.execute("SELECT id FROM platforms WHERE short_name='PS5'").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) "
                 "VALUES (1, ?, 'digital')", (pid,))
    conn.commit(); conn.close()

    p = client.get("/api/games/1").get_json()["platforms"][0]
    assert p["short_name"] == "PS5"
    assert p["format"] == "digital"
    assert p["has_digital_market"] == 1
    assert p["category"] == "modern_console"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_api_games.py::test_game_detail_platforms_include_format_and_market -v`
Expected: FAIL — `KeyError: 'format'` (the current query returns only id/name/short_name).

- [ ] **Step 3: Write minimal implementation**

In `app.py` `api_game()`, replace the platforms sub-query (currently `SELECT p.id, p.name, p.short_name ...`):
```python
    # Get platforms (with per-platform format + the platform's digital-market flag,
    # which drives the (Physical/Digital) qualifier in the editor and on mobile)
    platforms = conn.execute("""
        SELECT p.id, p.name, p.short_name, p.category,
               p.has_digital_market, gp.format
        FROM platforms p
        JOIN game_platforms gp ON gp.platform_id = p.id
        WHERE gp.game_id = ?
    """, (game_id,)).fetchall()
    result['platforms'] = [dict(p) for p in platforms]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_api_games.py -v`
Expected: PASS (new test + existing game-detail tests still green).

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_api_games.py
git commit -m "feat(api): include format + has_digital_market + category in game-detail platforms"
```

---

## Task 2: Preserve `format` across the platforms-replace path

**Files:**
- Modify: `app.py` — `api_update_game()` `if 'platforms' in data:` block (~924-939)
- Test: `tests/test_api_games.py`

**Interfaces:**
- Consumes: none new.
- Produces: PUT `/api/games/<id>` with `{"platforms": [...]}` re-inserts each kept platform with its **previously stored** `format` (membership/removal semantics unchanged; only `format` is carried forward).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api_games.py`:
```python
def test_platforms_replace_preserves_format(client):
    import models
    conn = models.get_db()
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (1, 'A', 'a')")
    conn.execute("INSERT INTO platforms (name, short_name, category) "
                 "VALUES ('PlayStation 5', 'PS5', 'modern_console')")
    conn.execute("INSERT INTO platforms (name, short_name, category) "
                 "VALUES ('Nintendo Switch', 'Switch', 'modern_console')")
    pid = conn.execute("SELECT id FROM platforms WHERE short_name='PS5'").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) "
                 "VALUES (1, ?, 'physical')", (pid,))
    conn.commit(); conn.close()

    # Add Switch via the full-replace path; PS5's existing format must survive.
    resp = client.put("/api/games/1", json={"platforms": ["PS5", "Switch"]})
    assert resp.status_code == 200

    conn = models.get_db()
    fmts = {r[0]: r[1] for r in conn.execute(
        "SELECT p.short_name, gp.format FROM game_platforms gp "
        "JOIN platforms p ON p.id = gp.platform_id WHERE gp.game_id = 1").fetchall()}
    conn.close()
    assert fmts["PS5"] == "physical"   # preserved, not wiped
    assert fmts["Switch"] is None      # newly added, no format yet
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_api_games.py::test_platforms_replace_preserves_format -v`
Expected: FAIL — `fmts["PS5"]` is `None` (the current replace path drops format on delete+reinsert).

- [ ] **Step 3: Write minimal implementation**

In `app.py` `api_update_game()`, replace the entire `if 'platforms' in data:` block with:
```python
        # Update platforms if provided (full replace). Preserve each platform's
        # existing `format` across the delete+reinsert so editing membership never
        # wipes the per-platform physical/digital values set elsewhere. The set of
        # platforms inserted is unchanged from before — only `format` is carried.
        if 'platforms' in data:
            existing_fmt = {
                r['short_name']: r['format']
                for r in conn.execute(
                    "SELECT p.short_name, gp.format FROM game_platforms gp "
                    "JOIN platforms p ON p.id = gp.platform_id WHERE gp.game_id = ?",
                    (game_id,)).fetchall()
            }
            conn.execute("DELETE FROM game_platforms WHERE game_id = ?", (game_id,))
            for platform_short_name in data['platforms']:
                platform = conn.execute(
                    "SELECT id FROM platforms WHERE short_name = ?",
                    (platform_short_name,)
                ).fetchone()
                if platform:
                    conn.execute(
                        "INSERT OR IGNORE INTO game_platforms (game_id, platform_id, format) "
                        "VALUES (?, ?, ?)",
                        (game_id, platform['id'], existing_fmt.get(platform_short_name))
                    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_api_games.py -v`
Expected: PASS (new test + the Plan 1 `test_add_platform_to_existing_game` + existing platform tests all green).

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_api_games.py
git commit -m "fix(api): preserve game_platforms.format across the platforms-replace path"
```

---

## Task 3: `platform_formats` setter on `PUT /api/games/<id>`

**Files:**
- Modify: `app.py` — `api_update_game()`, add a branch after the `add_platform` block (~958, before `conn.commit()`)
- Test: `tests/test_api_games.py`

**Interfaces:**
- Consumes: none new.
- Produces: PUT `/api/games/<id>` accepts `{"platform_formats": {"<short_name>": "physical"|"digital", ...}}` and updates `game_platforms.format` for each named owned platform, **without** changing membership. Invalid format values and unknown short_names are ignored.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api_games.py`:
```python
def test_platform_formats_setter_updates_without_membership_change(client):
    import models
    conn = models.get_db()
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (1, 'A', 'a')")
    conn.execute("INSERT INTO platforms (name, short_name, category) "
                 "VALUES ('PlayStation 5', 'PS5', 'modern_console')")
    pid = conn.execute("SELECT id FROM platforms WHERE short_name='PS5'").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) "
                 "VALUES (1, ?, 'digital')", (pid,))
    conn.commit(); conn.close()

    resp = client.put("/api/games/1", json={
        "platform_formats": {"PS5": "physical", "Bogus": "physical", "PS5_bad": "weird"}})
    assert resp.status_code == 200

    conn = models.get_db()
    rows = conn.execute(
        "SELECT p.short_name, gp.format FROM game_platforms gp "
        "JOIN platforms p ON p.id = gp.platform_id WHERE gp.game_id = 1").fetchall()
    conn.close()
    assert len(rows) == 1                 # membership unchanged
    assert dict(rows)["PS5"] == "physical"  # format updated
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_api_games.py::test_platform_formats_setter_updates_without_membership_change -v`
Expected: FAIL — format stays `'digital'` (no setter branch yet).

- [ ] **Step 3: Write minimal implementation**

In `app.py` `api_update_game()`, after the `add_platform` block and before `conn.commit()`, add:
```python
        # Per-platform format setter (web format editor): set physical/digital for
        # already-owned platforms without touching membership. Unknown platforms and
        # invalid format values are ignored.
        fmts = data.get('platform_formats')
        if isinstance(fmts, dict):
            for short_name, fmt in fmts.items():
                if fmt not in ('physical', 'digital'):
                    continue
                prow = conn.execute(
                    "SELECT id FROM platforms WHERE short_name = ?", (short_name,)
                ).fetchone()
                if prow:
                    conn.execute(
                        "UPDATE game_platforms SET format = ? "
                        "WHERE game_id = ? AND platform_id = ?",
                        (fmt, game_id, prow['id']))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_api_games.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_api_games.py
git commit -m "feat(api): platform_formats setter (per-platform physical/digital, no membership change)"
```

---

## Task 4: Per-platform format toggle in the game-detail modal

**Files:**
- Modify: `templates/base.html` — add `renderPlatformFormats(game)` + `setPlatformFormat(...)` (~882); render it in the Platforms block (~1121-1124); append a `loadGameModal(gameId)` re-render to `togglePlatform`/`setLegacyPlatform` (~1499, ~1514).
- Test: controller browser verification (no pytest — see Global Constraints).

**Interfaces:**
- Consumes: `game.platforms[]` now carries `format` + `has_digital_market` (Task 1); `PUT /api/games/<id>` accepts `platform_formats` (Task 3).
- Produces: JS `renderPlatformFormats(game) -> string` and `async setPlatformFormat(gameId, shortName, fmt)`.

- [ ] **Step 1: Add the render + setter JS**

In `templates/base.html`, immediately AFTER the `renderPlatformCheckboxes(game)` function (ends ~line 881), add:
```javascript
        function renderPlatformFormats(game) {
            // Per-platform Physical/Digital toggle. Shown only for owned platforms
            // whose platform has a digital storefront (has_digital_market); pure
            // cartridge/disc legacy systems have no format choice (spec §2.3).
            const owned = (game.platforms || []).filter(p => p.has_digital_market);
            if (!owned.length) return '';
            return '<div class="text-xs text-gray-400 mb-1 mt-2">Format</div>' +
                owned.map(p => {
                    const fmt = p.format || 'digital';
                    return '<div class="flex items-center gap-2 mb-1">' +
                        '<span class="platform-badge platform-' + p.short_name + '">' + p.short_name + '</span>' +
                        '<select onchange="setPlatformFormat(' + game.id + ', \'' + p.short_name + '\', this.value)" ' +
                        'class="bg-surface rounded border border-gray-600 px-2 py-1 text-white text-xs focus:border-accent focus:outline-none">' +
                        '<option value="physical"' + (fmt === 'physical' ? ' selected' : '') + '>Physical</option>' +
                        '<option value="digital"' + (fmt === 'digital' ? ' selected' : '') + '>Digital</option>' +
                        '</select></div>';
                }).join('');
        }

        async function setPlatformFormat(gameId, shortName, fmt) {
            await api.put('/api/games/' + gameId, { platform_formats: { [shortName]: fmt } });
            if (typeof refreshGameList === 'function') refreshGameList();
        }
```

- [ ] **Step 2: Render the format toggle in the modal**

In `templates/base.html`, in the Platforms block (currently the `<div>` containing `platform-checkboxes-${game.id}`, ~1119-1124), add a sibling div BELOW the checkboxes div:
```html
                            <div>
                                <label class="block text-sm font-medium text-gray-400 mb-1">Platforms</label>
                                <div class="flex flex-wrap gap-2" id="platform-checkboxes-${game.id}">
                                    ${renderPlatformCheckboxes(game)}
                                </div>
                                <div id="platform-formats-${game.id}">
                                    ${renderPlatformFormats(game)}
                                </div>
                            </div>
```

- [ ] **Step 3: Re-render the modal after a membership change**

So the Format list appears/disappears when a platform is added/removed, append a modal re-render to the END of `togglePlatform` and `setLegacyPlatform` (do NOT change their PUT logic). In `togglePlatform` (~1499) after `if (typeof refreshGameList === 'function') refreshGameList();` add:
```javascript
            if (typeof loadGameModal === 'function') loadGameModal(gameId);
```
Do the same at the end of `setLegacyPlatform` (~1514).

- [ ] **Step 4: Manual/controller verification**

This is a frontend change with no pytest. The controller verifies in a real browser (Playwright + Chrome) against a COPY of `games.db` on an alternate port (NOT the live server). Verification checklist (recorded for the gate task):
- Open a game owned on PS5 → a "Format" row shows `PS5 [Physical|Digital]` reflecting the stored value.
- Change the toggle → reload the modal → the new value persists (and DB row updated).
- Open a game owned only on a cartridge-only legacy system (e.g. SNES) → NO format row appears.
- Add a platform via the checkboxes → the modal re-renders and a format toggle appears for the new platform (if it has a digital market).

- [ ] **Step 5: Commit**

```bash
git add templates/base.html
git commit -m "feat(web): per-platform physical/digital format toggle in game-detail editor"
```

---

## Task 5: Mobile / subscription membership picker in the modal

**Files:**
- Modify: `templates/base.html` — add `renderExtraPlatforms(game)` (~882, next to `renderPlatformFormats`); render it in the Platforms block.
- Test: controller browser verification (no pytest).

**Interfaces:**
- Consumes: `allPlatforms` (already loaded from `/api/platforms`, includes `category` for `mobile`/`subscription`); the generic `togglePlatform(gameId, shortName, checked)` (membership toggle — unchanged).
- Produces: JS `renderExtraPlatforms(game) -> string`.

- [ ] **Step 1: Add the render JS**

In `templates/base.html`, immediately after `renderPlatformFormats` (Task 4), add:
```javascript
        function renderExtraPlatforms(game) {
            // Mobile + subscription membership (stub categories from Plan 1). These
            // are pickable like owning a console platform; selecting one records you
            // "own" it for this game (no availability catalog — that's Spec 3). No
            // format choice (inherently digital).
            const gamePlatforms = (game.platforms || []).map(p => p.short_name);
            const extra = allPlatforms
                .filter(p => p.category === 'mobile' || p.category === 'subscription')
                .sort((a, b) => a.category.localeCompare(b.category) || a.name.localeCompare(b.name));
            if (!extra.length) return '';
            return '<div class="text-xs text-gray-400 mb-1 mt-3">Mobile &amp; Subscriptions</div>' +
                '<div class="flex flex-wrap gap-x-3 gap-y-1">' +
                extra.map(p => {
                    const checked = gamePlatforms.includes(p.short_name) ? 'checked' : '';
                    return '<label class="flex items-center gap-1 cursor-pointer text-sm">' +
                        '<input type="checkbox" ' + checked + ' ' +
                        'onchange="togglePlatform(' + game.id + ', \'' + p.short_name + '\', this.checked)" ' +
                        'class="accent-accent w-4 h-4">' +
                        '<span class="text-gray-200">' + escapeHtml(p.name) + '</span></label>';
                }).join('') + '</div>';
        }
```

- [ ] **Step 2: Render it in the modal**

In the Platforms block (Task 4's edited `<div>`), add a third child div below `platform-formats-${game.id}`:
```html
                                <div id="platform-extra-${game.id}">
                                    ${renderExtraPlatforms(game)}
                                </div>
```

- [ ] **Step 3: Manual/controller verification**

Recorded for the gate task:
- Open any game → a "Mobile & Subscriptions" section lists iOS, Android, and the 6 subscriptions as checkboxes.
- Check "Xbox Game Pass" → reopen the modal → it stays checked (DB has a `game_platforms` row for it).
- Uncheck it → reopen → unchecked.
- Confirm these picks do NOT appear in the Library's Modern/Legacy/PC platform tabs (those are filtered by category; mobile/subscription are intentionally separate).

- [ ] **Step 4: Commit**

```bash
git add templates/base.html
git commit -m "feat(web): mobile + subscription membership picker in game-detail editor"
```

---

## Task 6: Full backend gate + controller browser verification

**Files:** none (verification task)

- [ ] **Step 1: Run the full backend suite**

Run: `uv run python -m pytest -q`
Expected: PASS (all green, including Plan 1's 752 tests plus the new Task 1-3 tests).

- [ ] **Step 2: Lint**

Run: `uv run ruff check`
Expected: `All checks passed!` (fix any new findings; do NOT run `ruff format`).

- [ ] **Step 3: Controller browser verification (NOT a subagent)**

The controller (not a subagent — subagents must not run the app) verifies the frontend against a COPY of `games.db` on an alternate port:
- Copy `games.db` to a scratch path; launch the app on an alt port pointing at the copy (e.g. set `DB_PATH` + a non-5000 port) so the live server and DB are untouched.
- Drive Chrome via Playwright through the Task 4 and Task 5 verification checklists.
- Confirm: format toggle persists; legacy cartridge-only games show no format row; mobile/subscription checkboxes persist; the existing platform add/remove behavior is unchanged from before this plan.

- [ ] **Step 4: Commit (only if lint fixes were needed)**

```bash
git add -A
git commit -m "chore(web): lint + full-suite green for scan-for-info web editors"
```

---

## Self-Review

- **Spec coverage:** §6 "Web (canonical editor): minimal UI to set per-platform `format`" → Tasks 1 (payload) + 3 (setter API) + 4 (UI). §6 "manage mobile/subscription category membership" → Task 5 (UI; backend already supports it via the generic platforms path). §2.3 display rule (qualifier only when `has_digital_market`) → enforced in `renderPlatformFormats` (Task 4) and surfaced via Task 1's payload. The clobbering interaction (replace path wiped `format`) → Task 2. Mobile consuming format read-only (§6) is Plan 3.
- **Out of scope (by design):** the per-platform format on *mobile* (read-only display + scan-for-info use) is Plan 3; subscription/mobile *availability catalogs* are Spec 3; the pre-existing "web platform removal doesn't seem to work" bug is explicitly NOT touched (owner instruction) — Task 4 Step 3 only adds a re-render that may incidentally make the true persisted state visible, which the controller confirms is no worse than before.
- **Placeholder scan:** every step has concrete SQL/JS/HTML and exact commands. Frontend tasks substitute controller browser verification for pytest, stated explicitly.
- **Type consistency:** `platform_formats` payload shape (`{short_name: 'physical'|'digital'}`) is defined in Task 3 and produced by `setPlatformFormat` in Task 4. The enriched `platforms[]` keys (`format`, `has_digital_market`, `category`) defined in Task 1 are consumed by `renderPlatformFormats` (Task 4) and `renderExtraPlatforms` (Task 5). `togglePlatform`/`setLegacyPlatform` signatures are unchanged (only a trailing re-render appended).
- **Open verification at implementation:** confirm `escapeHtml` and `refreshGameList` helpers exist in `base.html` (they are already used at ~874 and ~1499) before relying on them; confirm `api.put` is the established fetch wrapper (used throughout, e.g. ~812).
