# DLC Scrape-Results + Hero Tile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a scrape, show the DLC it imported this run (with owned/not-owned status), and add an `owned/total DLC` tile to the library hero bar.

**Architecture:** The scrape pipeline records the DLC it added this run (via `dlc.created_at`) and which DLC it flipped to owned (via a new `OwnershipReport.marked_items`), and carries them in the scrape `summary`; the Add Game modal renders an expandable, view-only results block at completion. `/api/stats` gains DLC counts and the hero bar renders a tile.

**Tech Stack:** Python 3, Flask, sqlite3, pytest, `uv`, `ruff`. No new deps.

Spec: `docs/superpowers/specs/2026-05-25-dlc-ownership-visibility-design.md`. This plan covers **Features 2 and 3**. Feature 1 (PSN add-on capture) is a separate, recon-gated effort.

## CRITICAL environment notes (every task)
- Run tests with `uv run python -m pytest` (NOT `uv run pytest` — that fails with `ModuleNotFoundError: models`).
- Lint gate is `uv run ruff check <files>` only (must print "All checks passed!"). Do NOT run `ruff format` (the repo uses a hand-aligned style on purpose).
- Work on `main`, no branch, no push. Conventional commits, NO Co-Authored-By trailer.
- Tests use temp-DB / `client` fixtures ONLY. Never launch a real browser or touch the real `games.db`.

---

## File Structure
- **Modify** `dlc_ownership.py` — `OwnershipReport` gains `marked_items`; `mark_ownership` records the applied matches.
- **Modify** `scrape_service.py` — `_run_pipeline` records `added_dlc` / `newly_owned` / `review` into the summary.
- **Modify** `app.py` — `/api/stats` returns `dlc_total` / `dlc_owned`.
- **Modify** `templates/index.html` — hero `DLC` tile.
- **Modify** `templates/base.html` — scrape result block + `renderScrapeResults`.
- **Tests:** `tests/test_dlc_ownership.py`, `tests/test_scrape_service.py`, new `tests/test_api_stats.py`.

---

## Task 1: `OwnershipReport.marked_items`

**Files:**
- Modify: `dlc_ownership.py` (the `OwnershipReport` dataclass and `mark_ownership`)
- Test: `tests/test_dlc_ownership.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dlc_ownership.py` (it already has a `_seed` helper that inserts a game "The Witcher 3: Wild Hunt" with a "Hearts of Stone" dlc row, and imports `dlc_ownership as own` + `models`):

```python
def test_mark_ownership_records_marked_items(temp_db):
    conn = models.get_db()
    _seed(conn)
    conn.commit()
    rep = own.mark_ownership(conn, [{"title": "The Witcher 3: Wild Hunt - Hearts of Stone"}])
    conn.commit()
    assert rep.marked == 1
    assert len(rep.marked_items) == 1
    assert rep.marked_items[0].dlc_id is not None
    # already-owned re-run does not re-append
    rep2 = own.mark_ownership(conn, [{"title": "The Witcher 3: Wild Hunt - Hearts of Stone"}])
    assert rep2.marked == 0 and rep2.marked_items == []
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_dlc_ownership.py -k marked_items -v`
Expected: FAIL (`AttributeError: 'OwnershipReport' object has no attribute 'marked_items'`).

- [ ] **Step 3: Implement**

In `dlc_ownership.py`, add the field to `OwnershipReport` (after `unmatched`):

```python
@dataclass
class OwnershipReport:
    """Outcome counts + the held/unmatched lists for manual review."""
    marked: int = 0
    already_owned: int = 0
    held: list[Match] = field(default_factory=list)
    unmatched: list[Match] = field(default_factory=list)
    marked_items: list[Match] = field(default_factory=list)
```

In `mark_ownership`, in the branch that flips a row 0→1 (the `else` under `if owned:`), append the match. The block becomes:

```python
        if apply_it:
            owned = conn.execute("SELECT owned FROM dlc WHERE id = ?", (m.dlc_id,)).fetchone()[0]
            if owned:
                report.already_owned += 1
            else:
                report.marked += 1
                report.marked_items.append(m)
                if not dry_run:
                    conn.execute("UPDATE dlc SET owned = 1 WHERE id = ?", (m.dlc_id,))
        elif m.action == "hold":
            report.held.append(m)
        elif m.action == "unmatched":
            report.unmatched.append(m)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_dlc_ownership.py -q`
Expected: PASS (whole file — the new test plus all existing).
Run: `uv run ruff check dlc_ownership.py tests/test_dlc_ownership.py`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add dlc_ownership.py tests/test_dlc_ownership.py
git commit -m "feat: OwnershipReport records marked_items (DLC flipped owned this run)"
```

---

## Task 2: `_run_pipeline` records added/owned/review in the summary

**Files:**
- Modify: `scrape_service.py` (`_run_pipeline`)
- Test: `tests/test_scrape_service.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scrape_service.py` (it already imports `scrape_service`, `models`, `ScrapedGame`, and defines `_fake_enrich`). This test seeds a pre-existing DLC with an OLD `created_at` (so the "added this run" filter must exclude it) and adds a new one during the run:

```python
def test_run_pipeline_reports_added_owned_review(temp_db, monkeypatch):
    import igdb_dlc
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 ("The Witcher 3: Wild Hunt",
                  models.normalize_title(models.clean_title("The Witcher 3: Wild Hunt"))))
    gid = conn.execute("SELECT id FROM games WHERE title LIKE 'The Witcher%'").fetchone()[0]
    # pre-existing DLC, clearly created before this run
    conn.execute("INSERT INTO dlc (game_id, name, source, created_at) "
                 "VALUES (?, 'Hearts of Stone', 'igdb', '2000-01-01 00:00:00')", (gid,))
    conn.commit()
    conn.close()

    def fake_enrich(conn, *, client_id, token):
        for (g,) in conn.execute("SELECT id FROM games WHERE igdb_id IS NULL").fetchall():
            conn.execute("UPDATE games SET igdb_id = 1 WHERE id = ?", (g,))
            conn.execute("INSERT OR IGNORE INTO dlc (game_id, name, source) "
                         "VALUES (?, 'Blood and Wine', 'igdb')", (g,))
        conn.commit()
        return {"games": 1, "matched": 1, "added": 1, "errors": 0}

    monkeypatch.setattr(igdb_dlc, "enrich_missing", fake_enrich)
    monkeypatch.setattr("config.get_twitch_credentials", lambda: ("cid", "secret"))
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda c, s: "tok")

    games = [
        ScrapedGame(title="The Witcher 3: Wild Hunt", platform="PS5",
                    source="playstation", external_id="G1"),
        ScrapedGame(title="The Witcher 3: Wild Hunt - Hearts of Stone", platform="PS5",
                    source="playstation", external_id="A1", kind="addon"),
        ScrapedGame(title="The Witcher 3: Wild Hunt - Mystery Pack", platform="PS5",
                    source="playstation", external_id="A2", kind="addon"),
    ]
    conn = models.get_db()
    summary = scrape_service._run_pipeline(conn, "playstation", games)
    conn.commit()

    added_names = [d["name"] for d in summary["added_dlc"]]
    assert "Blood and Wine" in added_names          # inserted this run
    assert "Hearts of Stone" not in added_names      # old created_at -> excluded
    assert summary["added_dlc"][0]["game"] == "The Witcher 3: Wild Hunt"

    owned_names = [d["name"] for d in summary["newly_owned"]]
    assert owned_names == ["Hearts of Stone"]        # add-on A1 flipped it owned

    review_titles = [r["title"] for r in summary["review"]]
    assert any("Mystery Pack" in t for t in review_titles)  # A2 had no matching dlc
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_scrape_service.py -k reports_added_owned -v`
Expected: FAIL (`KeyError: 'added_dlc'`).

- [ ] **Step 3: Implement**

Replace the body of `_run_pipeline` in `scrape_service.py` with this version (adds `run_started` capture and the three result lists; keeps all existing counts):

```python
def _run_pipeline(conn: sqlite3.Connection, vendor: str, games: list) -> dict:
    """Back up the DB, then import games, enrich DLC, and mark ownership.

    `games` is a list of ScrapedGame objects (or dicts). Updates the phase as it
    goes and returns a summary dict (counts + the DLC added this run, the DLC
    flipped owned this run, and held/unmatched add-ons for review). Fuzzy matches
    use the safe non-interactive confirmer (auto-merges only spacing/punctuation).
    """
    import dlc_ownership
    import import_scraped

    rows = [g if isinstance(g, dict) else asdict(g) for g in games]
    games_only = [r for r in rows if r.get("kind", "game") == "game"]
    addons = [r for r in rows if r.get("kind") == "addon"]

    # Timestamp (DB clock, matching dlc.created_at) to find DLC added this run.
    run_started = conn.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]

    _set(phase="importing", message=f"importing {len(games_only)} {vendor} games...")
    backup_path = backup_db()
    stats = import_scraped.import_games(
        conn, games_only, vendor, confirm_fn=import_scraped._safe_auto_confirm)
    conn.commit()

    _set(phase="enriching", message="enriching DLC from IGDB...")
    enrich = import_scraped.run_dlc_enrichment(conn)

    _set(phase="matching", message="matching DLC ownership...")
    report = dlc_ownership.mark_ownership(conn, addons)
    conn.commit()

    added_dlc = [
        {"game": r["title"], "name": r["name"], "kind": r["kind"], "owned": bool(r["owned"])}
        for r in conn.execute(
            "SELECT g.title, d.name, d.kind, d.owned FROM dlc d JOIN games g ON g.id = d.game_id "
            "WHERE d.created_at >= ? ORDER BY g.title, d.name", (run_started,))
    ]
    newly_owned = []
    for m in report.marked_items:
        row = conn.execute(
            "SELECT g.title, d.name FROM dlc d JOIN games g ON g.id = d.game_id WHERE d.id = ?",
            (m.dlc_id,)).fetchone()
        if row:
            newly_owned.append({"game": row["title"], "name": row["name"]})
    review = [{"title": m.addon_title, "reason": m.reason}
              for m in (report.held + report.unmatched)]

    return {
        "vendor": vendor,
        "scraped": len(rows),
        "new_games": stats.new_games,
        "platform_links": stats.platform_links_added,
        "dlc_added": (enrich or {}).get("added", 0),
        "enrich_skipped": enrich is None,
        "owned_marked": report.marked,
        "held": len(report.held),
        "unmatched": len(report.unmatched),
        "backup_path": backup_path,
        "added_dlc": added_dlc,
        "newly_owned": newly_owned,
        "review": review,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_scrape_service.py -q`
Expected: PASS (whole file).
Run: `uv run ruff check scrape_service.py tests/test_scrape_service.py`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add scrape_service.py tests/test_scrape_service.py
git commit -m "feat: scrape summary carries added_dlc / newly_owned / review lists"
```

---

## Task 3: `/api/stats` DLC counts

**Files:**
- Modify: `app.py` (`api_stats`, around line 1400-1406, before `conn.close()`)
- Test: `tests/test_api_stats.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_api_stats.py`:

```python
import models


def test_stats_includes_dlc_counts(client):
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('G', 'g')")
    gid = conn.execute("SELECT id FROM games WHERE title='G'").fetchone()[0]
    conn.execute("INSERT INTO dlc (game_id, name, owned, source) VALUES (?, 'A', 1, 'igdb')", (gid,))
    conn.execute("INSERT INTO dlc (game_id, name, owned, source) VALUES (?, 'B', 0, 'igdb')", (gid,))
    conn.commit()
    conn.close()
    data = client.get("/api/stats").get_json()
    assert data["dlc_total"] == 2
    assert data["dlc_owned"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_api_stats.py -v`
Expected: FAIL (`KeyError: 'dlc_total'`).

- [ ] **Step 3: Implement**

In `app.py`, in `api_stats`, add these two lines immediately before `conn.close()`:

```python
    # DLC ownership counts
    stats['dlc_total'] = conn.execute("SELECT COUNT(*) FROM dlc").fetchone()[0]
    stats['dlc_owned'] = conn.execute("SELECT COUNT(*) FROM dlc WHERE owned = 1").fetchone()[0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_api_stats.py -v`
Expected: PASS.
Run: `uv run ruff check app.py tests/test_api_stats.py`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_api_stats.py
git commit -m "feat: /api/stats returns dlc_total and dlc_owned"
```

---

## Task 4: Hero bar DLC tile (frontend, manual-verified)

**Files:**
- Modify: `templates/index.html` (`renderHeroStats`, around lines 201-211)

No automated test (frontend). After the edit, run the full suite + lint to confirm nothing else broke; visually verify by loading the library page.

- [ ] **Step 1: Add the tile**

In `templates/index.html`, in `renderHeroStats`, the `tiles` array currently ends with the `done` tile. Add a DLC tile after it so the array reads:

```javascript
        const tiles = [
            ['total_games', stats.total_games || 0, 'games'],
            ['completed', stats.by_status?.completed || 0, 'completed'],
            ['playing', stats.by_status?.playing || 0, 'playing'],
            ['backlog', stats.by_status?.backlog || 0, 'backlog'],
            ['done', done + '%', 'done'],
            ['dlc', `${stats.dlc_owned || 0}/${stats.dlc_total || 0}`, 'DLC'],
        ];
```

- [ ] **Step 2: Verify nothing broke + lint**

Run: `uv run python -m pytest -q`
Expected: full suite passes.
Run: `uv run ruff check .`
Expected: "All checks passed!"

- [ ] **Step 3: Commit**

```bash
git add templates/index.html
git commit -m "feat: hero bar owned/total DLC tile"
```

---

## Task 5: Scrape result list in the Add Game modal (frontend, manual-verified)

**Files:**
- Modify: `templates/base.html` (the `#scrape-status` markup ~line 154-159, and `renderScrapeStatus` ~line 1172, adding `renderScrapeResults`)

No automated test (frontend + live scrape are manual-verified). After the edit, run the full suite + lint, then verify by running a scrape.

- [ ] **Step 1: Add the results container to the modal**

In `templates/base.html`, inside `#scrape-status`, add a results container right AFTER the `#scrape-actions` div (and before `#scrape-status` closes):

```html
                        <div id="scrape-results" class="mt-2"></div>
```

So the block reads:
```html
                        <div id="scrape-status" class="hidden mt-3 text-sm">
                            <div id="scrape-message" class="text-gray-300"></div>
                            <div id="scrape-actions" class="hidden mt-2 flex gap-2">
                                <button onclick="continueScrape()" class="px-3 py-1.5 bg-accent hover:bg-accent-hover rounded text-white text-sm">Continue</button>
                                <button onclick="cancelScrape()" class="px-3 py-1.5 bg-surface hover:bg-surface-lighter rounded text-white text-sm">Cancel</button>
                            </div>
                            <div id="scrape-results" class="mt-2"></div>
                        </div>
```

- [ ] **Step 2: Update `renderScrapeStatus` and add `renderScrapeResults`**

In `templates/base.html`, replace the existing `renderScrapeStatus` function with this (drops "Refresh to see them" and renders/clears the results block), and add `renderScrapeResults` immediately after it. `escapeHtml` already exists in this file and is reused:

```javascript
        function renderScrapeStatus(st) {
            const msg = document.getElementById('scrape-message');
            document.getElementById('scrape-actions').classList.toggle(
                'hidden', st.phase !== 'awaiting_login');
            if (st.phase === 'complete') {
                const s = st.summary || {};
                msg.textContent = `Done: +${s.new_games || 0} games, ` +
                    `${s.owned_marked || 0} add-ons marked owned, ` +
                    `${s.dlc_added || 0} DLC added.`;
                renderScrapeResults(s);
            } else if (st.phase === 'error') {
                msg.textContent = 'Error: ' + (st.error || 'scrape failed');
                renderScrapeResults(null);
            } else if (st.phase === 'cancelled') {
                msg.textContent = 'Cancelled.';
                renderScrapeResults(null);
            } else {
                msg.textContent = st.message || st.phase;
                renderScrapeResults(null);
            }
        }

        function renderScrapeResults(s) {
            const el = document.getElementById('scrape-results');
            if (!s) { el.innerHTML = ''; return; }
            const added = s.added_dlc || [];
            const owned = s.newly_owned || [];
            const review = s.review || [];
            let html = '';
            if (added.length) {
                const byGame = {};
                added.forEach(d => { (byGame[d.game] = byGame[d.game] || []).push(d); });
                const groups = Object.keys(byGame).sort().map(game => {
                    const rows = byGame[game].map(d =>
                        `<div class="flex items-center justify-between gap-2 pl-3">
                            <span class="text-gray-300">${escapeHtml(d.name)}</span>
                            <span class="${d.owned ? 'text-green-400' : 'text-gray-500'}">${d.owned ? '✓ owned' : 'not owned'}</span>
                         </div>`).join('');
                    return `<div class="mt-1"><div class="text-white font-medium">${escapeHtml(game)}</div>${rows}</div>`;
                }).join('');
                html += `<details class="mt-2"><summary class="cursor-pointer text-gray-300">Imported DLC (${added.length})</summary>
                         <div class="mt-1 max-h-64 overflow-y-auto pr-1">${groups}</div></details>`;
            }
            if (owned.length) {
                const rows = owned.map(d =>
                    `<div class="pl-3 text-green-400">✓ ${escapeHtml(d.game)} — ${escapeHtml(d.name)}</div>`).join('');
                html += `<details class="mt-2" open><summary class="cursor-pointer text-gray-300">Marked owned this run (${owned.length})</summary>
                         <div class="mt-1 max-h-48 overflow-y-auto">${rows}</div></details>`;
            }
            if (review.length) {
                const rows = review.map(r =>
                    `<div class="pl-3 text-yellow-400">${escapeHtml(r.title)} <span class="text-gray-500">[${escapeHtml(r.reason)}]</span></div>`).join('');
                html += `<details class="mt-2"><summary class="cursor-pointer text-gray-300">Needs review (${review.length})</summary>
                         <div class="mt-1 max-h-48 overflow-y-auto">${rows}</div></details>`;
            }
            el.innerHTML = html || '<div class="text-gray-500">No DLC changes.</div>';
        }
```

- [ ] **Step 3: Verify nothing broke + lint**

Run: `uv run python -m pytest -q`
Expected: full suite passes.
Run: `uv run ruff check .`
Expected: "All checks passed!"

- [ ] **Step 4: Manual verification (user)**

Run a scrape from the Add Game modal; at completion confirm the summary line shows counts and an **"Imported DLC (N)"** expandable list (grouped by game, each with owned/not-owned), plus "Marked owned this run" / "Needs review" sections when applicable. (The live scrape isn't unit-tested.)

- [ ] **Step 5: Commit**

```bash
git add templates/base.html
git commit -m "feat: scrape result shows imported DLC + owned status (view-only)"
```

---

## Self-Review

**1. Spec coverage:**
- Feature 2 "pipeline records items" (added via `created_at`, newly_owned via `marked_items`, review via held+unmatched) → Tasks 1 + 2. ✓
- Feature 2 UI (expandable, grouped, owned badges, view-only, replaces "Refresh to see them") → Task 5. ✓
- Feature 3 (`/api/stats` dlc counts + hero tile) → Tasks 3 + 4. ✓
- Feature 1 (PSN capture) → intentionally NOT in this plan (recon-gated, separate effort). ✓
- Semantics (total=catalogue, owned=flagged) → reflected in the tile values and the owned badges. ✓

**2. Placeholder scan:** No TBD/TODO. Frontend Task 4/5 step "manual verification" is genuine (no browser in tests), not a code placeholder; all code blocks are complete.

**3. Type consistency:** `OwnershipReport.marked_items` (list[Match]) defined in Task 1, consumed in Task 2 (`report.marked_items`, each a `Match` with `.dlc_id`). Summary keys `added_dlc` (`{game,name,kind,owned}`), `newly_owned` (`{game,name}`), `review` (`{title,reason}`) defined in Task 2 and consumed verbatim by `renderScrapeResults` in Task 5. `dlc_total`/`dlc_owned` defined in Task 3, consumed by the hero tile in Task 4. ✓
