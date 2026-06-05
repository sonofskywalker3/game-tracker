# Fix-match modal redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the "Wrong version? Fix match" modal usable — drop junk/duplicate candidates, show full covers, lead with the current cover + a "keep current" action, and badge the recommended option.

**Architecture:** One pure helper (`igdb_match.modal_candidates`) does the filter/dedupe/fallback and is unit-tested. The candidates route runs raw `candidates_for` output through it and also returns the game's current cover. A new `igdb-keep` route clears review without changing the match. The template's `loadIgdbCandidates` renders the new grid (current tile + candidate tiles).

**Tech Stack:** Python 3, Flask, sqlite3, pytest (with a `client` Flask-test fixture and `temp_db`), vanilla JS in a Jinja template. Tests: `uv run python -m pytest`. Lint: `ruff check`.

**Conventions (project memory):**
- Tests with `uv run python -m pytest` (plain `uv run pytest` fails).
- Lint gate is `ruff check` ONLY — never `ruff format`.
- `temp_db` fixture is a `pathlib.Path`; open `conn = models.get_db()` in tests.
- A `client` pytest fixture provides the Flask test client (see `tests/test_app_pin_steam.py`).
- Subagents touch only the pytest temp DB — never the live `games.db` or the running app.
- Commit directly to `main` with the Co-Authored-By trailer.
- Flask app served from `app.py` on port 5000 with `use_reloader=False`.

---

### Task 1: `modal_candidates` filter/dedupe helper

**Files:**
- Modify: `igdb_match.py` (add `modal_candidates` after `candidates_for`, ~line 216)
- Test: `tests/test_igdb_match.py` (append at end of file)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_igdb_match.py`:

```python
def test_modal_candidates_drops_junk_and_dedupes():
    cands = [
        {"igdb_id": 1, "name": "Aria of Sorrow", "cover_url": "https://x/co_a.jpg",
         "source": "search", "score": 110},
        {"igdb_id": 2, "name": "Aria of Sorrow", "cover_url": "https://x/co_a.jpg",
         "source": "search", "score": 100},                       # duplicate art -> collapsed
        {"igdb_id": 3, "name": "Aria of Sorrow Alter", "cover_url": "https://x/co_b.jpg",
         "source": "search", "score": 50},                        # title mismatch (mod) -> dropped
        {"igdb_id": 4, "name": "Anything", "cover_url": "https://x/co_c.jpg",
         "source": "bundle"},                                     # bundle -> kept regardless of title
        {"igdb_id": 5, "name": "Aria of Sorrow", "cover_url": None,
         "source": "search"},                                     # no cover -> dropped
    ]
    out = igdb_match.modal_candidates(cands, "Aria of Sorrow")
    assert [c["igdb_id"] for c in out] == [1, 4]


def test_modal_candidates_fallback_when_no_title_match():
    cands = [
        {"igdb_id": 7, "name": "Totally Different Name", "cover_url": "https://x/co_z.jpg",
         "source": "search", "score": 40},
    ]
    out = igdb_match.modal_candidates(cands, "Aria of Sorrow")
    assert [c["igdb_id"] for c in out] == [7]   # no title match -> fall back to all-with-cover


def test_modal_candidates_empty_input():
    assert igdb_match.modal_candidates([], "Whatever") == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_igdb_match.py -k modal_candidates -v`
Expected: FAIL — `AttributeError: module 'igdb_match' has no attribute 'modal_candidates'`.

- [ ] **Step 3: Implement the helper**

In `igdb_match.py`, immediately AFTER the `candidates_for` function (which ends ~line 216, before `def resolve_identity`), insert:

```python
def modal_candidates(cands: list[dict], game_title: str) -> list[dict]:
    """Shape `candidates_for` output for the Fix-match modal.

    Keeps candidates that have a cover AND either come from the bundle or whose name
    normalises equal to the game's title (drops mod/hack junk like "... Alter"). If
    no candidate matches the title, falls back to all cover-bearing candidates so the
    modal is never empty when art exists. De-duplicates by cover stem, keeping the
    first of each identical-art group (input is already best-first)."""
    target = normalize_title(game_title)
    with_cover = [c for c in cands if c.get("cover_url")]
    matched = [c for c in with_cover
               if c.get("source") == "bundle"
               or normalize_title(c.get("name") or "") == target]
    pool = matched or with_cover
    out: list[dict] = []
    seen: set[str | None] = set()
    for c in pool:
        stem = _cover_stem(c.get("cover_url"))
        if stem in seen:
            continue
        seen.add(stem)
        out.append(c)
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_igdb_match.py -k modal_candidates -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Lint**

Run: `ruff check igdb_match.py tests/test_igdb_match.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add igdb_match.py tests/test_igdb_match.py
git commit -m "feat(igdb): modal_candidates filters junk + dedupes by cover

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: candidates route uses the helper + new `igdb-keep` route

**Files:**
- Modify: `app.py` (`api_igdb_candidates`, ~lines 615-639; add `api_igdb_keep` after `api_igdb_pick`, ~line 660)
- Test: `tests/test_app_fix_match.py` (new)

- [ ] **Step 1: Write the failing route tests**

Create `tests/test_app_fix_match.py`:

```python
"""Fix-match modal endpoints: shaped candidates + keep-current."""
from __future__ import annotations

import models


def _insert_game(title: str, cover: str | None = None) -> int:
    conn = models.get_db()
    conn.execute(
        "INSERT INTO games (title, normalized_title, cover_url, needs_igdb_review, "
        "igdb_review_reason, igdb_id) VALUES (?, ?, ?, 1, 'bundle', 999)",
        (title, models.normalize_title(title), cover))
    gid = conn.execute("SELECT id FROM games WHERE title = ?", (title,)).fetchone()[0]
    conn.commit()
    conn.close()
    return gid


def test_candidates_returns_shaped_list_and_current(client, monkeypatch):
    import config
    import igdb_dlc
    import igdb_match
    gid = _insert_game("Aria of Sorrow", cover="https://x/co_old.jpg")
    monkeypatch.setattr(config, "get_twitch_credentials", lambda: ("cid", "secret"))
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda *a, **k: "tok")
    monkeypatch.setattr(igdb_match, "candidates_for", lambda *a, **k: [
        {"igdb_id": 1, "name": "Aria of Sorrow", "cover_url": "https://x/co_a.jpg",
         "source": "search", "score": 110},
        {"igdb_id": 2, "name": "Aria of Sorrow", "cover_url": "https://x/co_a.jpg",
         "source": "search", "score": 100},         # dup art -> collapsed
        {"igdb_id": 3, "name": "Aria of Sorrow Alter", "cover_url": "https://x/co_b.jpg",
         "source": "search", "score": 50},          # junk -> dropped
    ])
    res = client.get(f"/api/games/{gid}/igdb-candidates")
    assert res.status_code == 200
    body = res.get_json()
    assert [c["igdb_id"] for c in body["candidates"]] == [1]
    assert body["current"]["cover_url"] == "https://x/co_old.jpg"
    assert body["current"]["title"] == "Aria of Sorrow"


def test_keep_current_clears_review_without_changing_match(client):
    gid = _insert_game("Keep Me", cover="https://x/keep.jpg")
    res = client.post(f"/api/games/{gid}/igdb-keep")
    assert res.status_code == 200
    assert res.get_json() == {"success": True}
    conn = models.get_db()
    row = conn.execute(
        "SELECT igdb_id, cover_url, COALESCE(igdb_locked,0), "
        "COALESCE(needs_igdb_review,0), igdb_review_reason FROM games WHERE id=?",
        (gid,)).fetchone()
    conn.close()
    assert row[0] == 999                       # igdb_id unchanged
    assert row[1] == "https://x/keep.jpg"      # cover unchanged
    assert row[2] == 1                          # locked
    assert row[3] == 0 and row[4] is None       # review cleared


def test_keep_current_404_for_missing_game(client):
    res = client.post("/api/games/999999/igdb-keep")
    assert res.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_app_fix_match.py -v`
Expected: FAIL — `igdb-candidates` response has no `current` key (KeyError) and returns all 3 candidates; `igdb-keep` route does not exist (404 for the happy-path test too).

- [ ] **Step 3: Update `api_igdb_candidates`**

In `app.py`, replace the body of `api_igdb_candidates`. Change the games-row SELECT to also fetch `cover_url`, and pass the candidates through `modal_candidates`, returning `current`. The full function becomes:

```python
@app.route('/api/games/<int:game_id>/igdb-candidates', methods=['GET'])
def api_igdb_candidates(game_id):
    """Shaped IGDB identity candidates for the Fix-match modal, plus the game's
    current cover (bundle-first, junk/duplicate-filtered)."""
    import config
    import igdb_dlc
    import igdb_match
    conn = get_db()
    row = conn.execute(
        "SELECT title, cover_url, collection_name FROM games WHERE id = ?", (game_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Game not found'}), 404
    client_id, secret = config.get_twitch_credentials()
    if not client_id:
        conn.close()
        return jsonify({'error': 'IGDB credentials not configured'}), 400
    plat_short = [r[0] for r in conn.execute(
        "SELECT p.short_name FROM game_platforms gp JOIN platforms p "
        "ON p.id = gp.platform_id WHERE gp.game_id = ?", (game_id,))]
    conn.close()
    token = igdb_dlc.get_access_token(client_id, secret)
    cands = igdb_match.candidates_for(
        row['title'], igdb_match.platform_ids_for(plat_short),
        row['collection_name'], client_id, token)
    shaped = igdb_match.modal_candidates(cands, row['title'])
    return jsonify({'candidates': shaped,
                    'current': {'cover_url': row['cover_url'], 'title': row['title']}})
```

- [ ] **Step 4: Add the `api_igdb_keep` route**

In `app.py`, immediately AFTER the `api_igdb_pick` function (ends ~line 660, the `return jsonify({'success': True})` then a blank line), insert:

```python
@app.route('/api/games/<int:game_id>/igdb-keep', methods=['POST'])
def api_igdb_keep(game_id):
    """Keep the current IGDB match as-is: lock it and clear the review flag without
    changing igdb_id or cover_url (the 'this one is fine' action)."""
    conn = get_db()
    if not conn.execute("SELECT 1 FROM games WHERE id = ?", (game_id,)).fetchone():
        conn.close()
        return jsonify({'error': 'Game not found'}), 404
    conn.execute(
        "UPDATE games SET igdb_locked = 1, needs_igdb_review = 0, "
        "igdb_review_reason = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (game_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_app_fix_match.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Lint**

Run: `ruff check app.py tests/test_app_fix_match.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add app.py tests/test_app_fix_match.py
git commit -m "feat(api): shaped fix-match candidates + igdb-keep endpoint

candidates route now returns junk/dup-filtered candidates plus the
current cover; new POST igdb-keep clears review while keeping the match.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: redesign the modal grid (template)

**Files:**
- Modify: `templates/base.html` — the `#igdb-candidates-<id>` container class (~line 980), `loadIgdbCandidates` (~lines 1263-1281), and add `keepCurrentIgdb` after `pickIgdb` (~line 1288).

This task has no unit harness (vanilla JS in a Jinja template). Verification is running the app (Task 4 / operator step).

- [ ] **Step 1: Widen the candidates container**

In `templates/base.html`, find (~line 980):

```html
                          <div id="igdb-candidates-${game.id}" class="mt-2 hidden grid grid-cols-2 gap-2"></div>
```

Change `grid-cols-2` to `grid-cols-3`:

```html
                          <div id="igdb-candidates-${game.id}" class="mt-2 hidden grid grid-cols-3 gap-2"></div>
```

- [ ] **Step 2: Rewrite `loadIgdbCandidates`**

Replace the entire `loadIgdbCandidates` function (~lines 1263-1281) with:

```javascript
        async function loadIgdbCandidates(gameId) {
            const box = document.getElementById(`igdb-candidates-${gameId}`);
            box.classList.remove('hidden');
            box.innerHTML = '<span class="text-xs text-gray-400 col-span-3">Searching IGDB…</span>';
            const res = await api.get(`/api/games/${gameId}/igdb-candidates`);
            if (res.error) { box.innerHTML = `<span class="text-xs text-yellow-400 col-span-3">${escapeHtml(res.error)}</span>`; return; }
            const cands = (res.candidates || []);
            const current = res.current || {};
            const tile = (inner) => `<div class="bg-surface rounded-lg border border-gray-700 p-2 flex flex-col">${inner}</div>`;
            const cover = (url) => url
                ? `<img src="${escapeHtml(url)}" class="w-full aspect-[3/4] object-contain rounded mb-2 bg-black/20">`
                : `<div class="w-full aspect-[3/4] rounded mb-2 bg-black/20 flex items-center justify-center text-2xl">🎮</div>`;
            const currentTile = tile(
                `<div class="text-[10px] uppercase tracking-wide text-gray-400 mb-1">Current</div>`
                + cover(current.cover_url)
                + `<button onclick='keepCurrentIgdb(${gameId})'
                          class="mt-auto text-xs bg-surface-light hover:bg-gray-600 rounded px-2 py-1">Keep this one</button>`);
            if (!cands.length) {
                box.innerHTML = currentTile
                    + `<span class="text-xs text-yellow-400 col-span-2 self-center">No other versions found.</span>`;
                return;
            }
            const candTiles = cands.map((c, i) => tile(
                `<div class="text-[10px] uppercase tracking-wide ${i === 0 ? 'text-accent' : 'text-gray-500'} mb-1">${i === 0 ? 'Recommended' : '&nbsp;'}</div>`
                + cover(c.cover_url)
                + `<button onclick='pickIgdb(${gameId}, ${c.igdb_id}, ${JSON.stringify(c.cover_url)})'
                          class="mt-auto text-xs bg-accent/80 hover:bg-accent text-white rounded px-2 py-1">Use this</button>`)).join('');
            box.innerHTML = currentTile + candTiles;
        }
```

- [ ] **Step 3: Add `keepCurrentIgdb` after `pickIgdb`**

In `templates/base.html`, immediately AFTER the `pickIgdb` function (~line 1288), insert:

```javascript
        async function keepCurrentIgdb(gameId) {
            const res = await api.post(`/api/games/${gameId}/igdb-keep`, {});
            if (!res.ok) { alert(res.data?.error || 'Could not keep the current match'); return; }
            closeModal();
            if (typeof refreshGameList === 'function') refreshGameList();
        }
```

- [ ] **Step 4: Confirm the existing suite still passes (template change touches no Python)**

Run: `uv run python -m pytest -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add templates/base.html
git commit -m "feat(ui): redesign fix-match modal (current tile, full covers, keep-current)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: full suite + lint + operator verify

- [ ] **Step 1: Full suite**

Run: `uv run python -m pytest`
Expected: PASS (all — previously 566 + Task 1's 3 + Task 2's 3 = 572).

- [ ] **Step 2: Lint**

Run: `ruff check igdb_match.py app.py tests/test_igdb_match.py tests/test_app_fix_match.py`
Expected: no errors. (Never `ruff format`.)

**Operator verification (main session, NOT a subagent):** restart the app, open a
flagged game, click "Wrong version? Fix match", confirm: full covers render, no
ROM-hack/duplicate tiles, the Current tile shows the existing cover with "Keep this
one", the first option is badged "Recommended"; click "Keep this one" on one game and
confirm it drops out of the review list; click "Use this" on another and confirm the
cover changes and it drops out.

---

## Self-review

**Spec coverage:**
- Filter junk + dedupe by art + fallback + drop no-cover → Task 1 `modal_candidates`. ✓
- Candidates route shaped + returns current cover → Task 2. ✓
- `igdb-keep` clears review without changing match, 404 on missing → Task 2. ✓
- Full covers, current tile + Keep, Recommended badge, grid widened, caption removed → Task 3. ✓
- Testing (unit for helper, route tests for endpoints, run-the-app for JS) → Tasks 1/2/4. ✓

**Placeholder scan:** none — all steps have complete code.

**Type consistency:** `modal_candidates(cands, game_title) -> list[dict]` (Task 1) is
called as `igdb_match.modal_candidates(cands, row['title'])` in Task 2. The route
returns `{candidates, current:{cover_url,title}}`, consumed by the test as
`body["candidates"]` / `body["current"]["cover_url"]` (Task 2) and by the JS as
`res.candidates` / `res.current.cover_url` (Task 3). `keepCurrentIgdb` posts to
`/igdb-keep`, which Task 2 defines. Consistent. ✓
