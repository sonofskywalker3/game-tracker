# Dedup Grouping (Thread A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the one-pair-at-a-time dedup review with a grouped review — connected-component families, per-group "Mark all safe", per-item "remove from group", and inline "merge selected" — without dropping any detection (recall stays 100%).

**Architecture:** Pure clustering (`group_candidates`) added to `dedup.py`; `GET /api/duplicates` returns `groups`; `POST /api/duplicates/dismiss` accepts a bulk `pairs` list; the dedup modal in `templates/base.html` renders collapsible group cards with the three actions. This is Phase 1 of `docs/superpowers/specs/2026-05-23-dedup-grouping-design.md`; Threads B (series) and C (not-a-game) get their own plans after this is validated live.

**Tech Stack:** Python 3 / Flask / sqlite3, vanilla JS + Tailwind in `templates/base.html`, pytest. Package/env via `uv`. Lint `ruff check .`.

---

## File structure

- **Modify** `dedup.py` — add pure `group_candidates(candidates)` (union-find over pairs → connected components).
- **Modify** `app.py:408-436` (`api_duplicates`) — include `groups`; `app.py:461-478` (`api_dismiss_duplicate`) — accept bulk `pairs`.
- **Modify** `templates/base.html` — rewrite the candidate section of the dedup modal JS (replace `renderDedup`, delete `renderCandidate`/`confirmMerge`/`keepSeparate`, add group rendering + actions).
- **Modify** `tests/test_dedup.py` — unit tests for `group_candidates`.
- **Modify** `tests/test_api_games.py` — endpoint tests for `groups` payload and bulk dismiss.

No DB schema change (reuses the existing `not_duplicates` table and `/api/games/merge`).

---

## Task 1: `group_candidates` in `dedup.py`

**Files:**
- Modify: `dedup.py` (add function near `find_duplicate_groups`, after line 117)
- Test: `tests/test_dedup.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_dedup.py`. First extend the import on line 3 to include `group_candidates`:

```python
from dedup import (
    base_key, compute_merged_curation, find_duplicate_groups, group_candidates,
    merge_games, refresh_normalized_titles, strip_edition_key,
)
```

Then append these tests:

```python
def _c(a, b, reason="similar", score=0.9):
    return {"a": a, "b": b, "reason": reason, "score": score}


def test_group_candidates_clusters_connected_pairs():
    # 1-2 and 2-3 are one family (transitive); 10-11 is a separate family.
    groups = group_candidates([_c(1, 2), _c(2, 3), _c(10, 11)])
    assert [g["members"] for g in groups] == [[1, 2, 3], [10, 11]]


def test_group_candidates_orders_members_and_pairs():
    groups = group_candidates([_c(5, 2)])
    assert groups[0]["members"] == [2, 5]
    assert groups[0]["pairs"] == [[2, 5]]


def test_group_candidates_sorts_by_size_descending():
    groups = group_candidates([_c(1, 2), _c(10, 11), _c(11, 12)])
    assert [len(g["members"]) for g in groups] == [3, 2]


def test_group_candidates_empty():
    assert group_candidates([]) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_dedup.py -k group_candidates -v`
Expected: FAIL — `ImportError: cannot import name 'group_candidates'`.

- [ ] **Step 3: Implement `group_candidates`**

Add to `dedup.py` after `find_duplicate_groups` (after line 117). `defaultdict` is already imported (line 19):

```python
def group_candidates(candidates: list[dict]) -> list[dict]:
    """Cluster candidate pairs into families via union-find (connected components).

    Each group: {"members": [ids sorted asc], "pairs": [[lo, hi], ...]}. Groups
    are sorted by member count descending, then by smallest member id, so the
    noisiest families surface first. Pure; high recall preserved (nothing dropped).
    """
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:  # path compression
            parent[x], x = root, parent[x]
        return root

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for c in candidates:
        union(c["a"], c["b"])

    members: dict[int, set[int]] = defaultdict(set)
    pairs: dict[int, list[list[int]]] = defaultdict(list)
    for c in candidates:
        root = find(c["a"])
        members[root].update((c["a"], c["b"]))
        pairs[root].append(sorted((c["a"], c["b"])))

    groups = [{"members": sorted(members[r]), "pairs": pairs[r]} for r in members]
    groups.sort(key=lambda g: (-len(g["members"]), g["members"][0]))
    return groups
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_dedup.py -k group_candidates -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add dedup.py tests/test_dedup.py
git commit -m "feat: group_candidates clusters dedup pairs into families"
```

---

## Task 2: `GET /api/duplicates` returns `groups`

**Files:**
- Modify: `app.py:435-436` (the `jsonify` in `api_duplicates`)
- Test: `tests/test_api_games.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api_games.py`:

```python
def test_duplicates_endpoint_groups_related_candidates(client):
    conn = models.get_db()
    conn.executemany(
        "INSERT INTO games (title, normalized_title) VALUES (?, ?)",
        [("Final Fantasy", "final fantasy"),
         ("Final Fantasy VII", "final fantasy vii"),
         ("Final Fantasy X", "final fantasy x"),
         ("Celeste", "celeste"),
         ("Celest", "celest")],
    )
    conn.commit()
    conn.close()

    body = client.get("/api/duplicates").get_json()
    assert "groups" in body
    sizes = sorted(len(g["members"]) for g in body["groups"])
    # the three Final Fantasy rows form one family; Celeste/Celest a pair
    assert sizes == [2, 3]
    big = max(body["groups"], key=lambda g: len(g["members"]))
    assert big["pairs"]  # internal pairs are exposed for bulk dismiss
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_api_games.py::test_duplicates_endpoint_groups_related_candidates -v`
Expected: FAIL — `assert "groups" in body` (KeyError / missing key).

- [ ] **Step 3: Add `groups` to the response**

In `app.py`, change the `return jsonify(...)` at the end of `api_duplicates` (lines 435-436) to:

```python
    return jsonify({"definite": groups["definite"],
                    "candidates": groups["candidates"],
                    "groups": dedup.group_candidates(groups["candidates"]),
                    "games": games})
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_api_games.py::test_duplicates_endpoint_groups_related_candidates -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_api_games.py
git commit -m "feat: /api/duplicates returns candidate groups"
```

---

## Task 3: Bulk dismiss in `POST /api/duplicates/dismiss`

**Files:**
- Modify: `app.py:461-478` (`api_dismiss_duplicate`)
- Test: `tests/test_api_games.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api_games.py`:

```python
def test_dismiss_endpoint_bulk_pairs(client):
    conn = models.get_db()
    conn.executemany("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                     [("A", "a"), ("B", "b"), ("C", "c")])
    rows = {r["title"]: r["id"] for r in conn.execute("SELECT id, title FROM games")}
    a, b, c = rows["A"], rows["B"], rows["C"]
    conn.commit()
    conn.close()

    resp = client.post("/api/duplicates/dismiss",
                       json={"pairs": [[a, b], [a, c], [b, c]]})
    assert resp.status_code == 200
    assert resp.get_json()["count"] == 3
    conn = models.get_db()
    cnt = conn.execute("SELECT COUNT(*) FROM not_duplicates").fetchone()[0]
    conn.close()
    assert cnt == 3
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_api_games.py::test_dismiss_endpoint_bulk_pairs -v`
Expected: FAIL — the current handler reads `game_id_a`/`game_id_b` only; with `pairs` those are `None` → 400.

- [ ] **Step 3: Accept either a single pair or a bulk `pairs` list**

Replace the body of `api_dismiss_duplicate` (`app.py:461-478`) with:

```python
@app.route('/api/duplicates/dismiss', methods=['POST'])
def api_dismiss_duplicate():
    """Record pair(s) as confirmed-distinct so dedup never re-asks.

    Accepts a single pair {game_id_a, game_id_b} or bulk {pairs: [[a, b], ...]}
    (used by Mark-all-safe and Remove-from-group). Unknown ids fail the FK and
    are rejected with 400.
    """
    data = request.json or {}
    raw_pairs = data['pairs'] if data.get('pairs') is not None \
        else [[data.get('game_id_a'), data.get('game_id_b')]]

    pairs = []
    for p in raw_pairs:
        if not p or len(p) != 2 or not p[0] or not p[1] or p[0] == p[1]:
            return jsonify({'error': 'each pair needs two distinct game ids'}), 400
        pairs.append((min(p[0], p[1]), max(p[0], p[1])))

    conn = get_db()
    try:
        conn.executemany(
            "INSERT OR IGNORE INTO not_duplicates (game_id_lo, game_id_hi) VALUES (?, ?)",
            pairs)
        conn.commit()
        return jsonify({'success': True, 'count': len(pairs)})
    except sqlite3.IntegrityError as e:
        return jsonify({'error': str(e)}), 400
    finally:
        conn.close()
```

- [ ] **Step 4: Run the dismiss tests to verify all pass**

Run: `python -m pytest tests/test_api_games.py -k dismiss -v`
Expected: PASS — `test_dismiss_endpoint_bulk_pairs`, `test_dismiss_endpoint_records_not_duplicate`, and `test_dismiss_endpoint_rejects_unknown_game` all green (single-pair path and FK rejection preserved).

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_api_games.py
git commit -m "feat: bulk pair dismiss for dedup mark-all-safe / remove-from-group"
```

---

## Task 4: Modal — render collapsible group cards

Frontend; verified live (no JS test harness). The dedup modal JS lives in `templates/base.html` (~lines 196-358).

**Files:**
- Modify: `templates/base.html` — `renderDedup` (lines 241-265) and `openDedupModal` (217-223); add group-rendering helpers; delete `renderCandidate`/`confirmMerge`/`keepSeparate` (299-358) and the `candidateIndex` global (198).

- [ ] **Step 1: Reset group state on open**

Replace `openDedupModal` (lines 217-223) with:

```javascript
        async function openDedupModal() {
            showModalEl('dedup-modal');
            document.getElementById('dedup-body').innerHTML =
                '<div class="text-gray-400 text-sm">Scanning your library…</div>';
            dedupData = await api.get('/api/duplicates');
            removedFromGroup = {};
            renderDedup();
        }
```

Replace the `let candidateIndex = 0;` line (198) with:

```javascript
        let removedFromGroup = {};   // group index -> Set of member ids removed from the group
```

- [ ] **Step 2: Replace `renderDedup` with the grouped view**

Replace `renderDedup` (lines 241-265) with:

```javascript
        function renderDedup() {
            const body = document.getElementById('dedup-body');
            const def = dedupData.definite || [];
            const groups = dedupData.groups || [];
            if (!def.length && !groups.length) {
                body.innerHTML = '<div class="text-gray-400 text-sm">No duplicates found. 🎉</div>';
                return;
            }
            let html = '';
            if (def.length) {
                html += `<div><div class="flex items-center justify-between mb-2">
                    <h3 class="text-white font-semibold">Exact matches (${def.length})</h3>
                    <button onclick="mergeAllDefinite()" class="px-3 py-1.5 bg-accent hover:bg-accent-hover rounded-lg text-white text-sm">Merge all</button>
                    </div><div class="space-y-2">` +
                    def.map(group => `<div class="bg-surface rounded-lg p-3 space-y-1">
                        ${group.map(id => gameLine(dedupGame(id))).join('')}</div>`).join('') +
                    `</div></div>`;
            }
            if (groups.length) {
                const total = groups.reduce((n, g) => n + g.members.length, 0);
                html += `<div><h3 class="text-white font-semibold mb-2">Review — ${groups.length} groups (${total} games)</h3>
                    <div class="space-y-2">${groups.map((g, i) => groupCard(i)).join('')}</div></div>`;
            }
            body.innerHTML = html;
        }

        function activeMembers(idx) {
            const removed = removedFromGroup[idx] || new Set();
            return dedupData.groups[idx].members.filter(id => !removed.has(id));
        }

        function groupCard(idx) {
            const members = activeMembers(idx);
            const label = baseTitle(members);   // shortest member title = family label
            return `<div class="bg-surface rounded-lg" id="grp-${idx}">
                <div class="flex items-center justify-between p-3">
                    <button onclick="toggleGroup(${idx})" class="flex items-center gap-2 text-left text-white text-sm font-medium">
                        <span id="grp-caret-${idx}">▸</span>
                        <span>${escapeHtml(label)}</span>
                        <span class="text-gray-500">· <span id="grp-count-${idx}">${members.length}</span> games</span>
                    </button>
                    <button onclick="markAllSafe(${idx})" class="px-2 py-1 text-xs bg-surface-light hover:bg-surface-lighter border border-gray-600 rounded text-white whitespace-nowrap">Mark all safe</button>
                </div>
                <div id="grp-body-${idx}" class="hidden px-3 pb-3 space-y-2"></div>
            </div>`;
        }

        function toggleGroup(idx) {
            const bodyEl = document.getElementById(`grp-body-${idx}`);
            const caret = document.getElementById(`grp-caret-${idx}`);
            if (bodyEl.classList.contains('hidden')) {
                renderGroupBody(idx);
                bodyEl.classList.remove('hidden');
                caret.textContent = '▾';
            } else {
                bodyEl.classList.add('hidden');
                caret.textContent = '▸';
            }
        }

        function renderGroupBody(idx) {
            const bodyEl = document.getElementById(`grp-body-${idx}`);
            const members = activeMembers(idx);
            bodyEl.innerHTML = members.map(id => memberRow(idx, id)).join('') + `
                <div class="flex items-center gap-2 pt-1">
                    <button onclick="mergeSelected(${idx})" class="px-3 py-1.5 bg-accent hover:bg-accent-hover rounded-lg text-white text-sm">Merge selected</button>
                    <span id="grp-merge-bar-${idx}"></span>
                </div>`;
        }

        function memberRow(idx, id) {
            return `<div class="flex items-center gap-2" id="grp-${idx}-row-${id}">
                <input type="checkbox" value="${id}" class="grp-check-${idx}">
                <div class="flex-1">${gameLine(dedupGame(id))}</div>
                <button onclick="removeFromGroup(${idx}, ${id})" title="Not part of this group"
                    class="text-gray-500 hover:text-red-400 text-sm px-1">✕</button>
            </div>`;
        }
```

- [ ] **Step 3: Delete the obsolete one-pair walker**

Delete `renderCandidate`, `confirmMerge`, and `keepSeparate` (the block at lines 299-358). They are replaced by the group functions (`markAllSafe`, `removeFromGroup`, `mergeSelected`, `confirmMergeSelected` — the last two added in Task 6). Stubs to satisfy `onclick` until Task 5/6 are not needed because those handlers are added before they can be invoked from the freshly-rendered cards in the next tasks; if you run the app between tasks, clicking an unwired button is a no-op console error only.

- [ ] **Step 4: Verify live — groups render**

Run: `python app.py` (or rely on the debug-reload instance). Open http://127.0.0.1:5000, click **Dedup**.
Expected: "Exact matches (4)" with Merge all on top; then "Review — N groups (M games)" with collapsed cards; the largest is "Final Fantasy · 34 games". Clicking a header expands it to show member rows (checkbox + title + ✕) and a "Merge selected" button. No console errors from rendering.

- [ ] **Step 5: Commit**

```bash
git add templates/base.html
git commit -m "feat: dedup modal renders collapsible candidate groups"
```

---

## Task 5: Wire "Mark all safe" and "Remove from group"

Both call the bulk dismiss endpoint from Task 3. Frontend; verified live.

**Files:**
- Modify: `templates/base.html` — add `markAllSafe` and `removeFromGroup` (place with the other dedup functions, e.g. after `memberRow`).

- [ ] **Step 1: Add the two handlers**

```javascript
        async function markAllSafe(idx) {
            const active = new Set(activeMembers(idx));
            const pairs = dedupData.groups[idx].pairs.filter(([a, b]) => active.has(a) && active.has(b));
            if (pairs.length) await api.post('/api/duplicates/dismiss', { pairs });
            const el = document.getElementById(`grp-${idx}`);
            if (el) el.remove();
        }

        async function removeFromGroup(idx, id) {
            const active = new Set(activeMembers(idx));
            const pairs = dedupData.groups[idx].pairs.filter(([a, b]) =>
                (a === id && active.has(b)) || (b === id && active.has(a)));
            if (pairs.length) await api.post('/api/duplicates/dismiss', { pairs });
            (removedFromGroup[idx] = removedFromGroup[idx] || new Set()).add(id);
            if (activeMembers(idx).length < 2) {
                document.getElementById(`grp-${idx}`).remove();
            } else {
                document.getElementById(`grp-count-${idx}`).textContent = activeMembers(idx).length;
                renderGroupBody(idx);
            }
        }
```

- [ ] **Step 2: Verify live — Mark all safe**

Reload the page, open **Dedup**. On a clearly-distinct family (e.g. a 2-game pair that is NOT a dup), click **Mark all safe**. The card disappears. Reopen Dedup → that family does not return (its pairs are in `not_duplicates`).

- [ ] **Step 3: Verify live — Remove from group**

Expand "Final Fantasy", click **✕** on `XIII` (a false member). The row disappears and the count drops by one. Reopen Dedup → the FF group no longer contains `XIII` (its cross-pairs were dismissed). The remaining FF family is intact.

- [ ] **Step 4: Commit**

```bash
git add templates/base.html
git commit -m "feat: dedup group mark-all-safe and remove-from-group actions"
```

---

## Task 6: Wire inline "Merge selected"

Reuses `chooseSurvivor`/`baseTitle` and `POST /api/games/merge`. Frontend; verified live.

**Files:**
- Modify: `templates/base.html` — add `selectedInGroup`, `mergeSelected`, `confirmMergeSelected`.

- [ ] **Step 1: Add the merge handlers**

```javascript
        function selectedInGroup(idx) {
            return [...document.querySelectorAll(`.grp-check-${idx}:checked`)].map(c => parseInt(c.value));
        }

        function mergeSelected(idx) {
            const ids = selectedInGroup(idx);
            if (ids.length < 2) { alert('Check at least two games to merge.'); return; }
            const [survivor] = chooseSurvivor(ids);
            const names = ids.map(id => dedupGame(id).title).sort((a, b) => a.length - b.length);
            document.getElementById(`grp-merge-bar-${idx}`).innerHTML = `
                <span class="inline-flex items-center gap-2 flex-wrap">
                  <select id="grp-surv-${idx}" class="bg-surface-light border border-gray-600 rounded px-2 py-1 text-white text-sm">
                    ${ids.map(id => `<option value="${id}" ${id === survivor ? 'selected' : ''}>${escapeHtml(dedupGame(id).title)}</option>`).join('')}
                  </select>
                  <input id="grp-title-${idx}" type="text" value="${escapeHtml(names[0])}"
                         class="bg-surface-light border border-gray-600 rounded px-2 py-1 text-white text-sm">
                  <button onclick="confirmMergeSelected(${idx})" class="px-2 py-1 bg-accent hover:bg-accent-hover rounded text-white text-sm">Confirm</button>
                  <button onclick="document.getElementById('grp-merge-bar-${idx}').innerHTML=''" class="px-2 py-1 text-gray-400 hover:text-white text-sm">Cancel</button>
                </span>`;
        }

        async function confirmMergeSelected(idx) {
            const ids = selectedInGroup(idx);
            const survivor = parseInt(document.getElementById(`grp-surv-${idx}`).value);
            const drops = ids.filter(id => id !== survivor);
            const title = document.getElementById(`grp-title-${idx}`).value.trim();
            const res = await api.post('/api/games/merge',
                { survivor_id: survivor, drop_ids: drops, title });
            if (!res.ok) { alert(res.data.error || 'Merge failed'); return; }
            const removed = (removedFromGroup[idx] = removedFromGroup[idx] || new Set());
            drops.forEach(id => removed.add(id));
            if (typeof refreshGameList === 'function') refreshGameList();
            if (activeMembers(idx).length < 2) {
                document.getElementById(`grp-${idx}`).remove();
            } else {
                document.getElementById(`grp-count-${idx}`).textContent = activeMembers(idx).length;
                renderGroupBody(idx);
            }
        }
```

- [ ] **Step 2: Verify live — inline merge**

Reload, open **Dedup**. Expand a family with a genuine duplicate (a clean 2-game pair like `Ghost of Tsushima` ⇄ `Ghost of Tsushima: Director's Cut`). Check both, click **Merge selected** → the inline bar appears with the survivor dropdown and editable name. Click **Confirm** → the rows collapse into one, the main game list refreshes, and the merged title is correct. Confirm in the library that there is now one row.

- [ ] **Step 3: Verify the merge is real (DB)**

Run:
```bash
python -c "import sqlite3; c=sqlite3.connect('games.db'); print([r[0] for r in c.execute(\"SELECT title FROM games WHERE title LIKE 'Ghost of Tsushima%'\")])"
```
Expected: a single `Ghost of Tsushima` row (or whichever pair you merged), not two.

> Note: this writes to the real `games.db`. **Back it up first** (`Copy-Item games.db games.db.bak-<timestamp>`), per the project rule on bulk DB mutations. If you only want to rehearse, do the live verification against a copied DB.

- [ ] **Step 4: Commit**

```bash
git add templates/base.html
git commit -m "feat: inline merge-selected within a dedup group"
```

---

## Task 7: Full verification

- [ ] **Step 1: Run the whole suite**

Run: `python -m pytest`
Expected: all tests pass (114 prior + 6 new from Tasks 1-3 = 120).

- [ ] **Step 2: Lint**

Run: `ruff check .`
Expected: clean (no new findings).

- [ ] **Step 3: Manual smoke checklist (browser)**

Open **Dedup** and confirm, end to end:
- Exact matches "Merge all" still works (the 4 definite groups).
- Groups are collapsed, largest first; header shows label + count.
- Expand / collapse toggles the caret and body.
- Mark all safe removes a family and it does not return on reopen.
- ✕ removes a member; the count updates; it does not return on reopen.
- Merge selected (≥2 checked) → inline bar → Confirm merges; <2 checked → alert.
- The known cases resolve: Don't Starve 262/822 merges, `Don't Starve Together` 263 stays separate; Disco Elysium 629/715; Connection Haunted 234/824.

- [ ] **Step 4: Hand back for feel review**

Stop here for the user to judge the modal's feel and request tweaks (FUZZY_THRESHOLD, grouping, layout) before Threads B (series) and C (not-a-game) are planned. Do NOT push or open a PR yet.

---

## Self-review notes (author)

- **Spec coverage (Thread A):** grouping (`group_candidates`, Task 1) ✓; `groups` in API (Task 2) ✓; bulk dismiss for Mark-all-safe + Remove-from-group (Task 3, 5) ✓; collapsed largest-first cards (Task 4) ✓; inline quick-merge (Task 6) ✓; recall preserved — detection untouched ✓. Threads B/C deliberately out of this plan.
- **Type consistency:** group shape `{members: [int], pairs: [[lo, hi]]}` is identical across `group_candidates`, the API test, and every JS consumer (`activeMembers`, `markAllSafe`, `removeFromGroup`). Dismiss endpoint `{pairs: [[a, b]]}` matches the JS `api.post('/api/duplicates/dismiss', { pairs })` calls. Merge reuses the existing `{survivor_id, drop_ids, title}` contract.
- **No placeholders:** every code step is complete; frontend steps are live-verified because there is no JS test harness (consistent with the spec).
