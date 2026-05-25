# "Scrape now" button (web-driven library sync) — design

**Date:** 2026-05-25
**Branch:** main (work directly on main, per repo workflow)
**Status:** approach approved; ready for implementation plan

## Goal

Run the full vendor-library sync from the web app instead of the terminal: a
button in the **Add Game modal** opens the vendor login in a real browser, and
after the user logs in and clicks **Continue**, the app scrapes the library and
runs the existing pipeline end to end — **scrape → import → IGDB-enrich DLC →
mark DLC ownership** — showing live progress and a final summary. This is the
GUI counterpart to the `scrape_libraries.py` + `import_scraped.py` CLIs.

Assumes the Flask app runs locally on the user's machine (it does), so the headed
Chrome window opens on the user's desktop. Remote/headless operation is out of
scope.

## Background (verified facts)

- **Current scrape (CLI):** `scrape_libraries.run_scrape(vendor)` opens a headed
  browser via `scrapers.base.capturing_browser(headless=False)` (persistent
  `.pw-profile`), `page.goto(mod.VENDOR_URL)`, then `_wait_for_user(page, ...)`
  which blocks on terminal `input()` in a thread while pumping the browser, then
  `mod.collect(page, captured)` and `write_scrape(vendor, games)`
  (`scrapers/base.py:write_scrape` → `scraped/<vendor>_<YYYYMMDD>.json`).
  `SCRAPERS = {"playstation","xbox","nintendo"}` all expose `collect`.
- **Import pipeline (CLI):** `import_scraped.main` reads the JSON, partitions rows
  by `kind`, `import_games(...)`, then `run_dlc_enrichment(conn)`, then
  `dlc_ownership.mark_ownership(conn, addons)`. `import_games` takes a list of
  scraped-game dicts (`title, platform, source, external_id, cover_url,
  source_title, kind`) and a `confirm_fn` for fuzzy matches; `run_dlc_enrichment`
  returns `None` when no Twitch creds are configured.
- **Existing background pattern:** `background_tasks.py` has a thread-safe
  `TaskManager`/`TaskProgress` and `run_cover_fetch_background` (daemon thread +
  generator); Flask exposes `POST /api/covers/fetch` and
  `GET /api/covers/fetch/status` (`app.py:1516-1535`); the UI polls the status
  endpoint. This is the pattern to mirror.
- **Add Game modal:** `templates/base.html:118-150` (`#add-game-modal`), opened
  from the "+ Add Game" button; JS helpers around `base.html:1106+`
  (`searchIGDB`, `addNewGame`, `closeAddGameModal`). The `api` helper wraps
  fetch; existing endpoints return `{data: ...}` / `{data:{error}}` shapes.
- **Creds:** `config.get_twitch_credentials()` returns `(client_id, secret)` or
  `(None, None)`.
- **Playwright (sync) threading rule:** all browser operations must happen on the
  thread that created the Playwright context. The background runner therefore owns
  the entire browser lifecycle; Flask request threads only set events / read
  status.

## Architecture

### New module `scrape_service.py`
Owns a single, module-level, thread-safe scrape state plus the runner. One scrape
at a time (the `.pw-profile` lock and the single headed window make concurrency
unsafe).

State (guarded by a `Lock`):
- `phase: str` — one of `idle, launching, awaiting_login, scraping, importing,
  enriching, matching, complete, error, cancelled`.
- `vendor: str | None`, `message: str` (human status line), `error: str | None`,
  `summary: dict` (filled at completion), `started_at` / `finished_at`.
- `_continue: threading.Event`, `_cancel: threading.Event`.

Public API:
- `start(vendor, *, browser_factory=capturing_browser, collect=None) ->
  tuple[bool, str]` — rejects (False) if a run is active or `vendor` is unknown;
  else resets state, clears events, spawns the daemon thread, returns (True,
  "started"). Two test seams: `browser_factory` is a context manager yielding
  `(page, captured)` (default `scrapers.base.capturing_browser`); `collect`
  defaults to `SCRAPERS[vendor].collect` when `None`. The import → enrich →
  ownership pipeline runs for real against `models` (tests use a temp DB and
  monkeypatch `igdb_dlc.enrich_missing`, exactly as the import_scraped tests do),
  so it is NOT injected.
- `signal_continue() -> bool` — sets `_continue` (no-op unless `awaiting_login`).
- `cancel() -> bool` — sets `_cancel`.
- `status() -> dict` — snapshot for the poll endpoint.

### Runner (daemon thread)
```
phase=launching;  page = browser_factory(); page.goto(VENDOR_URL)
phase=awaiting_login;  wait on _continue (pump page.wait_for_timeout(300)),
                       bail to cancelled if _cancel
phase=scraping;  games = mod.collect(page, captured); close browser; write_scrape
phase=importing; backup games.db; partition rows by kind;
                 import_games(conn, games_only, source, confirm_fn=_safe_auto_confirm,
                              skip_non_games=True); commit
phase=enriching; run_dlc_enrichment(conn)            # skipped (noted) if no creds
phase=matching;  mark_ownership(conn, addons); commit
phase=complete;  summary = {new_games, platform_links, dlc_added, owned_marked,
                            held, unmatched, enrich_skipped, backup_path, ...}
```
The browser is always closed in a `finally`; any exception sets `phase=error` +
`message` and never crashes Flask. `_cancel` is checked at each phase boundary
(and during the login wait); on cancel the browser is closed and `phase=cancelled`.

**Fuzzy matches:** the web context has no terminal to confirm, so import uses
`import_scraped._safe_auto_confirm` (auto-merges only spacing/punctuation
variants; everything else becomes a new game) — the safe non-interactive choice.

**games scraped → dicts:** `collect` returns `ScrapedGame`s; the runner converts
them with `dataclasses.asdict` before `import_games`, and `write_scrape` persists
the JSON artifact (same date-named file as the CLI).

**Backup:** before the first import write, copy `models.DB_PATH` to
`games.db.bak-YYYYMMDD-HHMMSS` (matching the existing backup convention); the path
goes into the summary.

### Flask endpoints (thin wrappers, in `app.py`)
- `POST /api/scrape/start` body `{vendor}` → `scrape_service.start(vendor)`; 409 if
  already running, 400 on unknown vendor; else `{success:true}`.
- `POST /api/scrape/continue` → `signal_continue()`; `{success:true}`.
- `POST /api/scrape/cancel` → `cancel()`; `{success:true}`.
- `GET /api/scrape/status` → `scrape_service.status()`.

### UI — Add Game modal (`templates/base.html`)
Add a "**Sync a whole library**" section below the manual-add controls: three
vendor buttons (PlayStation / Xbox / Nintendo) and a status area. Flow (JS,
polling `/api/scrape/status` ~every 1s, mirroring the cover-fetch poller):
- Click a vendor → `POST /api/scrape/start {vendor}`; disable the vendor buttons;
  begin polling.
- `awaiting_login` → show "Log in to <vendor> in the browser window, open your
  library / purchase history, then click Continue", with **Continue**
  (`/api/scrape/continue`) and **Cancel** (`/api/scrape/cancel`) buttons.
- `scraping | importing | enriching | matching` → spinner + phase label.
- `complete` → render the summary (e.g. "+12 games, 4 add-ons marked owned, 30 DLC
  added; backup: games.db.bak-…") and a note to refresh the library; re-enable
  buttons.
- `error | cancelled` → show the message; re-enable buttons.

## Error handling & safety
- Single run at a time (guard in `start`).
- Pre-import `games.db` backup; import/enrich/ownership run inside the runner's
  try/except so a failure is reported, not fatal.
- Enrich + ownership auto-skip cleanly when no Twitch creds (enrich returns
  `None`; ownership still runs against existing DLC rows — for a first-ever import
  with no creds there are simply no DLC rows yet, so nothing is marked).
- Browser closed in `finally`; cancel path closes it too.
- `.pw-profile/`, `scraped/`, `.recon/`, `config.json`, `games.db*` stay
  gitignored.

## Testing
All tests run offline and never launch a real browser or touch the real
`games.db` (consistent with `scrapers/base.py` being "verified manually" and the
project rule that impl/review work uses temp DBs only).
- `scrape_service` pipeline (temp DB): inject a `browser_factory` yielding a fake
  page (no-op `goto`/`wait_for_timeout`) + `[]`, and a `collect` returning canned
  `ScrapedGame`s incl. one `kind="addon"`; `start`, poll `status` until
  `awaiting_login`, `signal_continue`, then assert games imported, mocked enrich
  ran, ownership flipped the add-on, a backup file was created, `summary`
  populated, `phase=complete`.
- Continue/cancel: `cancel` before continue → `phase=cancelled`, no import.
- Single-run guard: second `start` while active → `(False, ...)`.
- No-creds path: enrich skipped, `summary["enrich_skipped"]` true, no crash.
- Flask endpoints (orchestration monkeypatched): `start` 409 when running / 400 on
  bad vendor, `continue`, `cancel`, and `status` shape.
- The headed-browser scrape itself is verified manually by the user.

## Out of scope (later)
- Remote/headless scraping; running the scrape on a different host than the
  browser.
- Auto-detecting login completion (the manual Continue handshake is intentional).
- PSN add-on capture (separate recon-gated follow-up).
- A general settings-page "sync" entry (this lives in the Add Game modal for now).
- Scheduling/automating periodic syncs.

## Constraints
- Public repo: never commit `games.db`/`games.db.bak*`, `.recon/`, `scraped/`,
  `.pw-profile/`, `config.json`, `.igdb_token.json`, `excluded_games.json`.
- Conventional commits, no co-author trailer. Work on `main`.
- Tests run via `uv run python -m pytest`; lint gate is `uv run ruff check` (the
  repo does not use `ruff format`). Match the existing hand-aligned style.
- Browser/import isolated behind injectable callables so the suite runs offline;
  the live scrape backs up `games.db` before writing.
