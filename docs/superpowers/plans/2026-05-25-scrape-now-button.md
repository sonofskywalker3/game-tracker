# "Scrape now" Button Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a web-driven library sync — a button in the Add Game modal opens the vendor login in a real browser, and after the user clicks Continue the app scrapes, imports, enriches DLC, and marks ownership, showing live progress.

**Architecture:** A new `scrape_service.py` owns a single thread-safe scrape state, a daemon-thread runner that owns the Playwright lifecycle, a login handshake via `threading.Event`, and the import→enrich→ownership pipeline. Thin Flask endpoints mirror the existing cover-fetch background pattern; the Add Game modal gets a vendor-button section that polls a status endpoint.

**Tech Stack:** Python 3, Flask, sqlite3, Playwright (sync, headed), pytest, `uv`, `ruff`. No new dependencies.

Spec: `docs/superpowers/specs/2026-05-25-scrape-now-button-design.md`.

## CRITICAL environment notes (every task)
- Run tests with `uv run python -m pytest` (NOT `uv run pytest` — that fails with `ModuleNotFoundError: models`).
- Lint gate is `uv run ruff check <files>` only (must print "All checks passed!"). Do NOT run `ruff format` (the repo uses a hand-aligned style on purpose).
- Work on `main`, no branch, no push. Conventional commits, NO co-author trailer.
- Tests use temp-DB / fakes ONLY. NEVER launch a real browser, run the live app, or touch the real `games.db`.

---

## File Structure

- **Create** `scrape_service.py` — scrape state, events, `backup_db`, `_run_pipeline`, the threaded `_run` runner, and the public API (`start`, `signal_continue`, `cancel`, `status`, `VENDORS`). One responsibility: orchestrate a single web-driven scrape+import.
- **Modify** `app.py` — `import scrape_service` + four thin endpoints.
- **Modify** `templates/base.html` — a "Sync a whole library" section in the Add Game modal + polling JS.
- **Create** `tests/test_scrape_service.py` — service unit tests (fakes + temp DB).
- **Create** `tests/test_api_scrape.py` — endpoint tests (orchestration monkeypatched).

---

## Task 1: `scrape_service` state, `status()`, and `backup_db()`

**Files:**
- Create: `scrape_service.py`
- Test: `tests/test_scrape_service.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scrape_service.py`:

```python
from pathlib import Path

import pytest

import models
import scrape_service


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset the global scrape state around every test (it is module-level)."""
    scrape_service._reset()
    yield
    scrape_service._continue.set()
    scrape_service._cancel.set()
    scrape_service._reset()


def test_status_initial_shape():
    st = scrape_service.status()
    assert st["phase"] == "idle"
    assert st["vendor"] is None
    assert st["summary"] == {}


def test_vendors_constant():
    assert scrape_service.VENDORS == ("playstation", "xbox", "nintendo")


def test_backup_db_copies_when_present(temp_db):
    path = scrape_service.backup_db()
    assert path is not None
    assert Path(path).exists()
    assert ".bak-" in Path(path).name


def test_backup_db_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "DB_PATH", tmp_path / "nope.db")
    assert scrape_service.backup_db() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_scrape_service.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'scrape_service'`).

- [ ] **Step 3: Create the module skeleton**

Create `scrape_service.py`:

```python
"""Web-driven vendor library sync: scrape -> import -> enrich DLC -> mark ownership.

A single scrape runs at a time, driven by one daemon thread that owns the whole
Playwright lifecycle. The login step parks on a threading.Event set by the web UI
(the GUI counterpart to scrape_libraries._wait_for_user's terminal input). The
browser/collect seams are injectable so the suite runs offline; the live headed
scrape is verified manually. See
docs/superpowers/specs/2026-05-25-scrape-now-button-design.md.
"""
from __future__ import annotations

import logging
import shutil
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import models
from scrape_libraries import SCRAPERS
from scrapers.base import capturing_browser, write_scrape

logger = logging.getLogger(__name__)

VENDORS = ("playstation", "xbox", "nintendo")
# Phases during which a scrape is considered "running" (start() is rejected).
_ACTIVE = frozenset({"launching", "awaiting_login", "scraping",
                     "importing", "enriching", "matching"})

_lock = threading.Lock()
_continue = threading.Event()
_cancel = threading.Event()
_state: dict = {}


def _reset(vendor: str | None = None) -> None:
    """Reset global state to idle and clear the handshake events."""
    _continue.clear()
    _cancel.clear()
    with _lock:
        _state.clear()
        _state.update(phase="idle", vendor=vendor, message="", error=None,
                      summary={}, started_at=None, finished_at=None)


_reset()  # initialize at import


def _set(**kw) -> None:
    with _lock:
        _state.update(kw)


def status() -> dict:
    """A snapshot of the current scrape state (safe to call from any thread)."""
    with _lock:
        return dict(_state)


def _is_active() -> bool:
    with _lock:
        return _state.get("phase") in _ACTIVE


def backup_db() -> str | None:
    """Copy the live DB to games.db.bak-YYYYMMDD-HHMMSS; return the path (or None
    if the DB file does not exist yet)."""
    src = Path(models.DB_PATH)
    if not src.exists():
        return None
    dst = src.with_name(f"{src.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(src, dst)
    return str(dst)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_scrape_service.py -v`
Expected: PASS (4 tests).
Run: `uv run ruff check scrape_service.py tests/test_scrape_service.py`
Expected: "All checks passed!"

- [ ] **Step 5: Commit**

```bash
git add scrape_service.py tests/test_scrape_service.py
git commit -m "feat: scrape_service state, status, and games.db backup helper"
```

---

## Task 2: `_run_pipeline` (import → enrich → ownership)

**Files:**
- Modify: `scrape_service.py`
- Test: `tests/test_scrape_service.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scrape_service.py` (add `from scrapers.base import ScrapedGame` to the imports at the top of the file):

```python
def _fake_enrich(conn, *, client_id, token):
    for (gid,) in conn.execute("SELECT id FROM games WHERE igdb_id IS NULL").fetchall():
        conn.execute("UPDATE games SET igdb_id = 1 WHERE id = ?", (gid,))
        conn.execute("INSERT OR IGNORE INTO dlc (game_id, name, source) "
                     "VALUES (?, 'Hearts of Stone', 'igdb')", (gid,))
    conn.commit()
    return {"games": 1, "matched": 1, "added": 1, "errors": 0}


def test_run_pipeline_imports_enriches_marks(temp_db, monkeypatch):
    import igdb_dlc
    monkeypatch.setattr(igdb_dlc, "enrich_missing", _fake_enrich)
    monkeypatch.setattr("config.get_twitch_credentials", lambda: ("cid", "secret"))
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda c, s: "tok")

    games = [
        ScrapedGame(title="The Witcher 3: Wild Hunt", platform="PS5",
                    source="playstation", external_id="G1"),
        ScrapedGame(title="The Witcher 3: Wild Hunt - Hearts of Stone", platform="PS5",
                    source="playstation", external_id="A1", kind="addon"),
    ]
    conn = models.get_db()
    summary = scrape_service._run_pipeline(conn, "playstation", games)
    conn.commit()
    assert summary["new_games"] == 1
    assert summary["owned_marked"] == 1
    assert summary["dlc_added"] == 1
    assert summary["enrich_skipped"] is False
    assert summary["backup_path"] and Path(summary["backup_path"]).exists()
    assert conn.execute("SELECT owned FROM dlc WHERE name='Hearts of Stone'").fetchone()[0] == 1
    conn.close()


def test_run_pipeline_skips_enrich_without_creds(temp_db, monkeypatch):
    monkeypatch.setattr("config.get_twitch_credentials", lambda: (None, None))
    games = [ScrapedGame(title="Hades", platform="PS5", source="playstation",
                         external_id="G2")]
    conn = models.get_db()
    summary = scrape_service._run_pipeline(conn, "playstation", games)
    conn.commit()
    assert summary["enrich_skipped"] is True
    assert summary["new_games"] == 1
    assert summary["owned_marked"] == 0
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_scrape_service.py -k run_pipeline -v`
Expected: FAIL (`AttributeError: module 'scrape_service' has no attribute '_run_pipeline'`).

- [ ] **Step 3: Implement `_run_pipeline`**

Append to `scrape_service.py`:

```python
def _run_pipeline(conn, vendor: str, games) -> dict:
    """Back up the DB, then import games, enrich DLC, and mark ownership.

    `games` is a list of ScrapedGame objects (or dicts). Updates the phase as it
    goes and returns a summary dict. Fuzzy matches use the safe non-interactive
    confirmer (auto-merges only spacing/punctuation variants).
    """
    import dlc_ownership
    import import_scraped

    rows = [g if isinstance(g, dict) else asdict(g) for g in games]
    games_only = [r for r in rows if r.get("kind", "game") == "game"]
    addons = [r for r in rows if r.get("kind") == "addon"]

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
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_scrape_service.py -k run_pipeline -v`
Expected: PASS (2 tests).
Run: `uv run ruff check scrape_service.py tests/test_scrape_service.py`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add scrape_service.py tests/test_scrape_service.py
git commit -m "feat: scrape_service._run_pipeline (import, enrich, mark ownership)"
```

---

## Task 3: threaded runner + `start` / `signal_continue` / `cancel`

**Files:**
- Modify: `scrape_service.py`
- Test: `tests/test_scrape_service.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scrape_service.py` (add `import time` and `import contextlib` to the top imports):

```python
class _FakePage:
    def goto(self, url):
        pass

    def wait_for_timeout(self, ms):
        pass


@contextlib.contextmanager
def _fake_browser(headless=False):
    yield _FakePage(), []


def _wait_phase(target, timeout=3.0):
    """Poll status() until phase == target (or in target tuple); return reached."""
    targets = (target,) if isinstance(target, str) else tuple(target)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if scrape_service.status()["phase"] in targets:
            return True
        time.sleep(0.02)
    return False


def test_start_runs_full_flow(temp_db, monkeypatch):
    import igdb_dlc
    monkeypatch.setattr(igdb_dlc, "enrich_missing", _fake_enrich)
    monkeypatch.setattr("config.get_twitch_credentials", lambda: ("cid", "secret"))
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda c, s: "tok")
    monkeypatch.setattr(scrape_service, "write_scrape", lambda *a, **k: None)

    def fake_collect(page, captured):
        return [
            ScrapedGame(title="The Witcher 3: Wild Hunt", platform="PS5",
                        source="playstation", external_id="G1"),
            ScrapedGame(title="The Witcher 3: Wild Hunt - Hearts of Stone",
                        platform="PS5", source="playstation",
                        external_id="A1", kind="addon"),
        ]

    ok, _ = scrape_service.start("playstation", browser_factory=_fake_browser,
                                 collect=fake_collect)
    assert ok
    assert _wait_phase("awaiting_login")
    scrape_service.signal_continue()
    assert _wait_phase("complete")
    st = scrape_service.status()
    assert st["summary"]["owned_marked"] == 1
    assert st["summary"]["new_games"] == 1


def test_cancel_before_continue(temp_db, monkeypatch):
    monkeypatch.setattr(scrape_service, "write_scrape", lambda *a, **k: None)
    ok, _ = scrape_service.start("playstation", browser_factory=_fake_browser,
                                 collect=lambda p, c: [])
    assert ok
    assert _wait_phase("awaiting_login")
    scrape_service.cancel()
    assert _wait_phase("cancelled")
    # nothing imported
    conn = models.get_db()
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0
    conn.close()


def test_start_rejects_unknown_vendor():
    ok, msg = scrape_service.start("steam")
    assert ok is False


def test_start_rejects_when_active():
    scrape_service._set(phase="scraping")
    ok, msg = scrape_service.start("xbox")
    assert ok is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_scrape_service.py -k "start or cancel" -v`
Expected: FAIL (`AttributeError: ... has no attribute 'start'`).

- [ ] **Step 3: Implement the runner and public API**

Append to `scrape_service.py`:

```python
def start(vendor: str, *, browser_factory=None, collect=None) -> tuple[bool, str]:
    """Begin a scrape in a daemon thread. Rejects if a scrape is active or the
    vendor is unknown. browser_factory/collect are test seams."""
    if vendor not in VENDORS:
        return False, f"unknown vendor: {vendor}"
    if _is_active():
        return False, "a scrape is already running"
    _reset(vendor=vendor)
    thread = threading.Thread(target=_run, args=(vendor, browser_factory, collect),
                              daemon=True)
    thread.start()
    return True, "started"


def signal_continue() -> bool:
    """Tell the runner the user has logged in and the scrape may proceed."""
    _continue.set()
    return True


def cancel() -> bool:
    """Request cancellation; the runner stops at the login wait / next boundary."""
    _cancel.set()
    return True


def _run(vendor: str, browser_factory, collect) -> None:
    """Daemon-thread body: own the browser, wait for login, scrape, run pipeline.

    Cancellation is honored during the (potentially long) login wait. Any error
    sets phase=error and is surfaced to the UI; the browser is always closed.
    """
    factory = browser_factory or capturing_browser
    collect_fn = collect or SCRAPERS[vendor].collect
    mod = SCRAPERS[vendor]
    _set(phase="launching", message=f"opening {vendor} in a browser...",
         started_at=datetime.now().isoformat())
    try:
        with factory(headless=False) as (page, captured):
            page.goto(mod.VENDOR_URL)
            _set(phase="awaiting_login",
                 message="log in and open your library, then click Continue")
            while not _continue.is_set():
                if _cancel.is_set():
                    _set(phase="cancelled", message="cancelled",
                         finished_at=datetime.now().isoformat())
                    return
                page.wait_for_timeout(300)
            if _cancel.is_set():
                _set(phase="cancelled", message="cancelled",
                     finished_at=datetime.now().isoformat())
                return
            _set(phase="scraping", message=f"scraping your {vendor} library...")
            games = collect_fn(page, captured)
        write_scrape(vendor, games)
        conn = models.get_db()
        try:
            summary = _run_pipeline(conn, vendor, games)
        finally:
            conn.close()
        _set(phase="complete", message="done", summary=summary,
             finished_at=datetime.now().isoformat())
    except Exception as exc:  # never crash Flask; surface to the UI
        logger.exception("scrape failed")
        _set(phase="error", error=str(exc), message="scrape failed",
             finished_at=datetime.now().isoformat())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_scrape_service.py -v`
Expected: PASS (all tests in the file).
Run: `uv run ruff check scrape_service.py tests/test_scrape_service.py`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add scrape_service.py tests/test_scrape_service.py
git commit -m "feat: scrape_service threaded runner + start/continue/cancel"
```

---

## Task 4: Flask endpoints

**Files:**
- Modify: `app.py` (add `import scrape_service` near the other imports; add four routes near the cover-fetch routes around `app.py:1532`)
- Test: `tests/test_api_scrape.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api_scrape.py`:

```python
import scrape_service


def test_scrape_status_shape(client):
    data = client.get("/api/scrape/status").get_json()
    assert "phase" in data


def test_scrape_start_bad_vendor(client):
    res = client.post("/api/scrape/start", json={"vendor": "steam"})
    assert res.status_code == 400


def test_scrape_start_ok(client, monkeypatch):
    monkeypatch.setattr(scrape_service, "start", lambda v: (True, "started"))
    res = client.post("/api/scrape/start", json={"vendor": "xbox"})
    assert res.status_code == 200
    assert res.get_json()["success"] is True


def test_scrape_start_conflict(client, monkeypatch):
    monkeypatch.setattr(scrape_service, "start", lambda v: (False, "already running"))
    res = client.post("/api/scrape/start", json={"vendor": "xbox"})
    assert res.status_code == 409


def test_scrape_continue_and_cancel(client, monkeypatch):
    calls = {"continue": 0, "cancel": 0}
    monkeypatch.setattr(scrape_service, "signal_continue",
                        lambda: calls.__setitem__("continue", calls["continue"] + 1))
    monkeypatch.setattr(scrape_service, "cancel",
                        lambda: calls.__setitem__("cancel", calls["cancel"] + 1))
    assert client.post("/api/scrape/continue").status_code == 200
    assert client.post("/api/scrape/cancel").status_code == 200
    assert calls == {"continue": 1, "cancel": 1}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_api_scrape.py -v`
Expected: FAIL (404s — routes not defined yet).

- [ ] **Step 3: Add the import and endpoints**

In `app.py`, add to the imports (near `from background_tasks import ...`):

```python
import scrape_service
```

Then add these routes immediately after the `api_fetch_covers_status` function (around `app.py:1535`):

```python
@app.route('/api/scrape/start', methods=['POST'])
def api_scrape_start():
    """Start a web-driven vendor library scrape in the background."""
    vendor = (request.json or {}).get('vendor', '')
    if vendor not in scrape_service.VENDORS:
        return jsonify({'error': f'unknown vendor: {vendor}'}), 400
    ok, message = scrape_service.start(vendor)
    if ok:
        return jsonify({'success': True, 'message': message})
    return jsonify({'error': message}), 409  # already running


@app.route('/api/scrape/continue', methods=['POST'])
def api_scrape_continue():
    """Signal that the user has logged in and the scrape may proceed."""
    scrape_service.signal_continue()
    return jsonify({'success': True})


@app.route('/api/scrape/cancel', methods=['POST'])
def api_scrape_cancel():
    """Request cancellation of the running scrape."""
    scrape_service.cancel()
    return jsonify({'success': True})


@app.route('/api/scrape/status')
def api_scrape_status():
    """Current scrape phase/progress for the UI poller."""
    return jsonify(scrape_service.status())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_api_scrape.py -v`
Expected: PASS (5 tests).
Run: `uv run ruff check app.py tests/test_api_scrape.py`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_api_scrape.py
git commit -m "feat: /api/scrape start/continue/cancel/status endpoints"
```

---

## Task 5: Add Game modal UI (manual-verified)

**Files:**
- Modify: `templates/base.html` (the `#add-game-modal` markup around line 143-147, and the Add Game JS around line 1119)

This task adds UI + JS that drives the endpoints. There is no automated test (the JS and the live scrape are verified manually); after the edits, run the full suite + lint to confirm nothing else broke, then verify by hand.

- [ ] **Step 1: Add the modal section**

In `templates/base.html`, inside `#add-game-modal`, immediately AFTER the existing "Add Game" submit button block (the `<button onclick="addNewGame()" ...>Add Game</button>` at lines ~144-146) and BEFORE the closing `</div>`s of the modal body, insert:

```html
                    <div class="border-t border-gray-700 pt-4 mt-2">
                        <div class="text-sm font-medium text-gray-400 mb-2">Or sync a whole library</div>
                        <div id="scrape-vendors" class="flex gap-2">
                            <button onclick="startScrape('playstation')" class="scrape-vendor-btn flex-1 px-3 py-2 bg-surface hover:bg-surface-lighter rounded-lg text-white text-sm transition-colors">PlayStation</button>
                            <button onclick="startScrape('xbox')" class="scrape-vendor-btn flex-1 px-3 py-2 bg-surface hover:bg-surface-lighter rounded-lg text-white text-sm transition-colors">Xbox</button>
                            <button onclick="startScrape('nintendo')" class="scrape-vendor-btn flex-1 px-3 py-2 bg-surface hover:bg-surface-lighter rounded-lg text-white text-sm transition-colors">Nintendo</button>
                        </div>
                        <div id="scrape-status" class="hidden mt-3 text-sm">
                            <div id="scrape-message" class="text-gray-300"></div>
                            <div id="scrape-actions" class="hidden mt-2 flex gap-2">
                                <button onclick="continueScrape()" class="px-3 py-1.5 bg-accent hover:bg-accent-hover rounded text-white text-sm">Continue</button>
                                <button onclick="cancelScrape()" class="px-3 py-1.5 bg-surface hover:bg-surface-lighter rounded text-white text-sm">Cancel</button>
                            </div>
                        </div>
                    </div>
```

- [ ] **Step 2: Add the JS**

In `templates/base.html`, in the `// Add Game Modal` script block, immediately after the `closeAddGameModal()` function (around line 1125), insert:

```javascript
        // ---- Scrape now (web-driven library sync) ----
        let scrapePollInterval = null;
        const SCRAPE_ACTIVE = ['launching', 'awaiting_login', 'scraping',
                               'importing', 'enriching', 'matching'];

        async function startScrape(vendor) {
            const res = await api.post('/api/scrape/start', { vendor });
            document.getElementById('scrape-status').classList.remove('hidden');
            if (!res.ok) {
                document.getElementById('scrape-message').textContent =
                    (res.data && res.data.error) || 'Could not start scrape';
                return;
            }
            setScrapeButtonsDisabled(true);
            startScrapePolling();
        }

        async function continueScrape() {
            await api.post('/api/scrape/continue', {});
        }

        async function cancelScrape() {
            await api.post('/api/scrape/cancel', {});
        }

        function setScrapeButtonsDisabled(disabled) {
            document.querySelectorAll('.scrape-vendor-btn').forEach(b => b.disabled = disabled);
        }

        function renderScrapeStatus(st) {
            const msg = document.getElementById('scrape-message');
            document.getElementById('scrape-actions').classList.toggle(
                'hidden', st.phase !== 'awaiting_login');
            if (st.phase === 'complete') {
                const s = st.summary || {};
                msg.textContent = `Done: +${s.new_games || 0} games, ` +
                    `${s.owned_marked || 0} add-ons marked owned, ` +
                    `${s.dlc_added || 0} DLC added. Refresh to see them.`;
            } else if (st.phase === 'error') {
                msg.textContent = 'Error: ' + (st.error || 'scrape failed');
            } else if (st.phase === 'cancelled') {
                msg.textContent = 'Cancelled.';
            } else {
                msg.textContent = st.message || st.phase;
            }
        }

        function startScrapePolling() {
            if (scrapePollInterval) return;
            scrapePollInterval = setInterval(async () => {
                const st = await api.get('/api/scrape/status');
                renderScrapeStatus(st);
                if (!SCRAPE_ACTIVE.includes(st.phase)) {
                    clearInterval(scrapePollInterval);
                    scrapePollInterval = null;
                    setScrapeButtonsDisabled(false);
                    if (st.phase === 'complete') {
                        if (typeof refreshGameList === 'function') refreshGameList();
                        if (typeof loadNavStats === 'function') loadNavStats();
                    }
                }
            }, 1000);
        }

        function refreshScrapeSection() {
            // On modal open: reflect an in-progress scrape, else reset the section.
            api.get('/api/scrape/status').then(st => {
                const statusEl = document.getElementById('scrape-status');
                if (SCRAPE_ACTIVE.includes(st.phase)) {
                    statusEl.classList.remove('hidden');
                    setScrapeButtonsDisabled(true);
                    renderScrapeStatus(st);
                    startScrapePolling();
                } else {
                    statusEl.classList.add('hidden');
                    setScrapeButtonsDisabled(false);
                }
            });
        }
```

- [ ] **Step 3: Call `refreshScrapeSection()` when the modal opens**

In `templates/base.html`, in `openAddGameModal()` (around line 1117), add a call right before the final `document.getElementById('new-game-title').focus();` line:

```javascript
            refreshScrapeSection();
```

- [ ] **Step 4: Verify nothing else broke + lint**

Run: `uv run python -m pytest -q`
Expected: full suite passes.
Run: `uv run ruff check .`
Expected: "All checks passed!"

- [ ] **Step 5: Manual verification (user)**

Start the app (`uv run python app.py` or the documented run command), open "+ Add Game", click a vendor, log in to the real vendor in the Chrome window that opens, open your library/purchase history, click **Continue**, and confirm progress advances through scraping → importing → enriching → matching → done with a summary. (This step is performed by the user; the automated suite does not launch a real browser.)

- [ ] **Step 6: Commit**

```bash
git add templates/base.html
git commit -m "feat: Add Game modal 'sync a whole library' scrape button + polling"
```

---

## Self-Review

**1. Spec coverage:**
- Flow & phases (launching→awaiting_login→scraping→importing→enriching→matching→complete/error/cancelled) → Task 1 (`_ACTIVE`/state) + Task 3 (`_run`). ✓
- `scrape_service.py` module (state, events, start/continue/cancel/status, backup, injectable browser_factory/collect) → Tasks 1–3. ✓
- `_run_pipeline` (backup → import with `_safe_auto_confirm` → enrich → ownership; summary) → Task 2. ✓
- Flask endpoints (start 409/400, continue, cancel, status) → Task 4. ✓
- Add Game modal UI + polling, Continue/Cancel, summary, refresh-on-complete → Task 5. ✓
- Single-run guard → Task 3 (`_is_active`), tested. ✓
- Pre-import backup → Task 1 (`backup_db`), called in Task 2. ✓
- No-creds enrich skip → Task 2, tested. ✓
- Error handling closes browser / never crashes Flask → Task 3 (`try/finally`/`except`). ✓
- Tests never launch a real browser / touch real DB → fakes + temp_db throughout; live scrape is manual (Task 5 Step 5). ✓

**2. Placeholder scan:** No TBD/TODO/"handle errors" placeholders. Task 5 Step 5 is explicit manual verification (the live headed scrape cannot be unit-tested), not a code placeholder.

**3. Type consistency:** `VENDORS` (tuple) used in Task 3 + Task 4; `status()` dict keys (`phase`, `vendor`, `message`, `error`, `summary`, `started_at`, `finished_at`) consistent across tasks and JS; `start(vendor, *, browser_factory, collect)`, `signal_continue()`, `cancel()`, `_run_pipeline(conn, vendor, games)`, `backup_db()` signatures consistent between definitions, tests, endpoints, and the runner. Summary keys (`new_games`, `owned_marked`, `dlc_added`, `enrich_skipped`, `backup_path`, `held`, `unmatched`, `platform_links`, `scraped`, `vendor`) defined in Task 2 and read by Task 5 JS (`new_games`/`owned_marked`/`dlc_added`). ✓
