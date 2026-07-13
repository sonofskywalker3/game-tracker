# BacklogQuest Scraper Desktop App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A branded Windows desktop app (installer + portable flavors) that scrapes vendor game libraries locally, exports CSV, and optionally syncs to a BacklogQuest server — plus the server routes that personalize the portable download with the user's token.

**Architecture:** New `desktop/` package reuses `scrapers/` unchanged (one optional-param seam in `scrapers/base.py`). pywebview renders `desktop/ui/` (HTML/CSS/JS, BacklogQuest dark theme); a worker thread drives Playwright against the user's installed Chrome/Edge. PyInstaller onedir output is wrapped twice: an Inno Setup installer and a portable zip the Flask app personalizes by streaming a `backlogquest.json` sidecar into it.

**Tech Stack:** Python 3.12, Playwright (sync), pywebview (WebView2), PyInstaller (onedir), Inno Setup 6, Flask (existing app), pytest.

**Spec:** `docs/superpowers/specs/2026-07-13-scraper-desktop-app-design.md`

## Global Constraints

- Env var names use the `BACKLOGQUEST_` prefix. New: `BACKLOGQUEST_SCRAPER_DIR` (droplet artifacts dir).
- Tests: `uv run python -m pytest <path> -q` (NEVER plain `uv run pytest` — ModuleNotFoundError). Full suite: add `-n auto`.
- Lint: `uv run ruff check .` only. NEVER `ruff format`.
- Type hints on every function signature. `logging`, never `print()` (exception: none needed here).
- Deps via `uv add` (never pip). Desktop runtime dep group: `pywebview`; build-only group `desktop-build`: `pyinstaller`, `pillow`.
- Commit directly to `main` and push after each task. No branches.
- `scrapers/` vendor logic is UNCHANGED except the Task 1 seam.
- Files stay under ~400 lines; split if approaching.
- App identity: "BacklogQuest Scraper". Palette: bg `#181A22`, surface `#232634`, text `#E6E6EC`, indigo accent `#8B93FF`, secondary text `#B7B9C6`.
- Sword SVG source: `docs/branding/backlogquest-icon.svg`.
- Default server URL: `https://backlogquest.xyz`. Import endpoint: `POST /api/import/scrape` with `Authorization: Bearer <token>`.

---

### Task 1: Profile-dir seam in scrapers/base.py

The frozen app must keep its browser profile in `%APPDATA%\BacklogQuest`, not the install dir. `capturing_browser` gets an optional `profile_dir` param; default behavior is byte-for-byte unchanged.

**Files:**
- Modify: `scrapers/base.py` (`_launch_context` ~line 44, `capturing_browser` ~line 140)
- Test: `tests/test_scraper_base_paths.py` (create)

**Interfaces:**
- Produces: `capturing_browser(headless: bool = False, profile_dir: Path | None = None)` — context manager yielding `(page, captured)`. Task 6 calls it with an explicit `profile_dir`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scraper_base_paths.py
"""_launch_context honors an explicit profile_dir (desktop-app seam)."""
from pathlib import Path

from scrapers.base import PROFILE_DIR, _launch_context


class _FakeChromium:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def launch_persistent_context(self, **kwargs) -> object:
        self.calls.append(kwargs)
        return object()


class _FakeP:
    def __init__(self) -> None:
        self.chromium = _FakeChromium()


def test_launch_context_uses_custom_profile_dir(tmp_path: Path) -> None:
    p = _FakeP()
    _launch_context(p, headless=True, profile_dir=tmp_path / "prof")
    assert p.chromium.calls[0]["user_data_dir"] == str(tmp_path / "prof")


def test_launch_context_defaults_to_module_profile_dir() -> None:
    p = _FakeP()
    _launch_context(p, headless=True)
    assert p.chromium.calls[0]["user_data_dir"] == str(PROFILE_DIR)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/test_scraper_base_paths.py -q`
Expected: FAIL — `_launch_context() got an unexpected keyword argument 'profile_dir'`.

- [ ] **Step 3: Implement the seam**

In `scrapers/base.py`, change `_launch_context`'s signature and both `user_data_dir=` usages:

```python
def _launch_context(p, headless: bool, profile_dir: Path | None = None):
    """Launch a persistent context, trying real browser channels first."""
    user_data_dir = str(profile_dir or PROFILE_DIR)
    for channel in BROWSER_CHANNELS:
        try:
            return p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir, headless=headless,
                channel=channel, args=list(LAUNCH_ARGS),
                ignore_default_args=list(IGNORE_DEFAULT_ARGS),
            )
        except Exception as exc:  # channel not installed on this machine
            logger.debug("browser channel %s unavailable: %s", channel, exc)
    logger.info("falling back to bundled Chromium (no installed Chrome/Edge found)")
    return p.chromium.launch_persistent_context(
        user_data_dir=user_data_dir, headless=headless, args=list(LAUNCH_ARGS),
        ignore_default_args=list(IGNORE_DEFAULT_ARGS),
    )
```

In `capturing_browser`, thread the param through (only these lines change):

```python
@contextmanager
def capturing_browser(headless: bool = False, profile_dir: Path | None = None):
```
```python
    (profile_dir or PROFILE_DIR).mkdir(parents=True, exist_ok=True)
```
```python
    with sync_playwright() as p:
        context = _launch_context(p, headless, profile_dir)
```

- [ ] **Step 4: Run tests**

Run: `uv run python -m pytest tests/test_scraper_base_paths.py -q` → PASS.
Run: `uv run python -m pytest -n auto -q` → all pass (no behavior change).
Run: `uv run ruff check .` → clean.

- [ ] **Step 5: Commit**

```bash
git add scrapers/base.py tests/test_scraper_base_paths.py
git commit -m "feat(scrapers): optional profile_dir seam for the desktop app"
git push
```

---

### Task 2: desktop/config.py — sidecar-seeded config

**Files:**
- Create: `desktop/__init__.py` (empty), `desktop/config.py`
- Test: `tests/test_desktop_config.py`

**Interfaces:**
- Produces:
  - `@dataclass AppConfig: server_url: str = "https://backlogquest.xyz"; token: str = ""`
  - `SIDECAR_NAME = "backlogquest.json"`
  - `appdata_dir() -> Path` — `%APPDATA%/BacklogQuest` (respects `APPDATA` env; tests override it)
  - `load_config(exe_dir: Path, data_dir: Path) -> AppConfig` — sidecar seeds `data_dir/config.json` on first run only; thereafter `data_dir/config.json` wins
  - `save_config(cfg: AppConfig, data_dir: Path) -> None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_desktop_config.py
"""Sidecar backlogquest.json seeds the appdata config once; appdata wins after."""
import json
from pathlib import Path

from desktop.config import SIDECAR_NAME, AppConfig, load_config, save_config


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_defaults_when_nothing_exists(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "exe", tmp_path / "data")
    assert cfg == AppConfig(server_url="https://backlogquest.xyz", token="")


def test_sidecar_seeds_appdata_on_first_run(tmp_path: Path) -> None:
    exe, data = tmp_path / "exe", tmp_path / "data"
    _write(exe / SIDECAR_NAME, {"server_url": "https://x.example", "token": "tok1"})
    cfg = load_config(exe, data)
    assert (cfg.server_url, cfg.token) == ("https://x.example", "tok1")
    saved = json.loads((data / "config.json").read_text(encoding="utf-8"))
    assert saved["token"] == "tok1"          # persisted


def test_appdata_wins_over_sidecar_after_first_run(tmp_path: Path) -> None:
    exe, data = tmp_path / "exe", tmp_path / "data"
    _write(exe / SIDECAR_NAME, {"server_url": "https://x.example", "token": "old"})
    _write(data / "config.json", {"server_url": "https://y.example", "token": "edited"})
    cfg = load_config(exe, data)
    assert (cfg.server_url, cfg.token) == ("https://y.example", "edited")


def test_corrupt_files_fall_back_to_defaults(tmp_path: Path) -> None:
    exe, data = tmp_path / "exe", tmp_path / "data"
    (data).mkdir(); (data / "config.json").write_text("{not json", encoding="utf-8")
    cfg = load_config(exe, data)
    assert cfg == AppConfig()


def test_save_round_trips(tmp_path: Path) -> None:
    save_config(AppConfig(server_url="https://z.example", token="t"), tmp_path / "d")
    cfg = load_config(tmp_path / "exe", tmp_path / "d")
    assert (cfg.server_url, cfg.token) == ("https://z.example", "t")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/test_desktop_config.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'desktop'`.

- [ ] **Step 3: Implement**

```python
# desktop/config.py
"""App settings: a sidecar backlogquest.json (personalized download) seeds the
persisted %APPDATA% config on first run; user edits in the app win thereafter."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

SIDECAR_NAME = "backlogquest.json"
_CONFIG_NAME = "config.json"
DEFAULT_SERVER_URL = "https://backlogquest.xyz"


@dataclass
class AppConfig:
    server_url: str = DEFAULT_SERVER_URL
    token: str = ""


def appdata_dir() -> Path:
    """Per-user data root (profile, scrapes, config, log)."""
    return Path(os.environ.get("APPDATA", str(Path.home()))) / "BacklogQuest"


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning("unreadable config %s: %s", path, exc)
        return None


def _from_dict(data: dict) -> AppConfig:
    return AppConfig(
        server_url=str(data.get("server_url") or DEFAULT_SERVER_URL),
        token=str(data.get("token") or ""),
    )


def save_config(cfg: AppConfig, data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / _CONFIG_NAME).write_text(json.dumps(asdict(cfg)), encoding="utf-8")


def load_config(exe_dir: Path, data_dir: Path) -> AppConfig:
    """Persisted config wins; a sidecar next to the exe seeds it exactly once."""
    persisted = _read_json(data_dir / _CONFIG_NAME)
    if persisted is not None:
        return _from_dict(persisted)
    sidecar = _read_json(exe_dir / SIDECAR_NAME)
    if sidecar is not None:
        cfg = _from_dict(sidecar)
        save_config(cfg, data_dir)
        return cfg
    return AppConfig()
```

Create empty `desktop/__init__.py`.

- [ ] **Step 4: Run tests** — `uv run python -m pytest tests/test_desktop_config.py -q` → 5 PASS. `uv run ruff check .` → clean.

- [ ] **Step 5: Commit**

```bash
git add desktop/__init__.py desktop/config.py tests/test_desktop_config.py
git commit -m "feat(desktop): sidecar-seeded app config"
git push
```

---

### Task 3: desktop/csv_export.py

**Files:**
- Create: `desktop/csv_export.py`
- Test: `tests/test_desktop_csv.py`

**Interfaces:**
- Consumes: scrape payload dicts as written by `scrapers.base.write_scrape` — `{"source": str, "scraped_at": str, "count": int, "games": [{"title", "platform", "source", "external_id", "cover_url", "source_title", "status_hint", "kind", "url_key"}]}`.
- Produces: `write_csv(payloads: list[dict], out_path: Path) -> int` — combined CSV, returns row count. Columns exactly: `title,platform,source,kind,external_id,source_title,cover_url`. Encoding `utf-8-sig` (Excel-friendly).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_desktop_csv.py
"""Combined CSV export from normalized scrape payloads."""
import csv
from pathlib import Path

from desktop.csv_export import COLUMNS, write_csv

_PS = {"source": "playstation", "games": [
    {"title": "Stray", "platform": "PS5", "source": "playstation", "kind": "game",
     "external_id": "123", "source_title": "Stray™", "cover_url": "http://c/1.png",
     "status_hint": None, "url_key": None},
]}
_XB = {"source": "xbox", "games": [
    {"title": "Halo", "platform": "XSX", "source": "xbox", "kind": "game",
     "external_id": None, "source_title": "Halo", "cover_url": None,
     "status_hint": None, "url_key": None},
]}


def test_combined_rows_and_columns(tmp_path: Path) -> None:
    out = tmp_path / "library.csv"
    assert write_csv([_PS, _XB], out) == 2
    with out.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0].keys()) == list(COLUMNS)
    assert rows[0]["title"] == "Stray" and rows[1]["source"] == "xbox"
    assert rows[1]["external_id"] == ""     # None -> empty cell, not "None"


def test_excel_bom(tmp_path: Path) -> None:
    out = tmp_path / "library.csv"
    write_csv([_PS], out)
    assert out.read_bytes().startswith(b"\xef\xbb\xbf")
```

- [ ] **Step 2: Run to verify failure** — `uv run python -m pytest tests/test_desktop_csv.py -q` → FAIL (no module).

- [ ] **Step 3: Implement**

```python
# desktop/csv_export.py
"""Flatten normalized scrape payloads into one combined, Excel-friendly CSV."""
from __future__ import annotations

import csv
from pathlib import Path

COLUMNS: tuple[str, ...] = (
    "title", "platform", "source", "kind", "external_id", "source_title", "cover_url",
)


def write_csv(payloads: list[dict], out_path: Path) -> int:
    """Write every game from every payload; returns the number of rows."""
    rows = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(COLUMNS), extrasaction="ignore")
        writer.writeheader()
        for payload in payloads:
            for game in payload.get("games", []):
                writer.writerow({col: game.get(col) or "" for col in COLUMNS})
                rows += 1
    return rows
```

- [ ] **Step 4: Run tests** — `uv run python -m pytest tests/test_desktop_csv.py -q` → PASS. `uv run ruff check .` → clean.

- [ ] **Step 5: Commit**

```bash
git add desktop/csv_export.py tests/test_desktop_csv.py
git commit -m "feat(desktop): combined CSV export"
git push
```

---

### Task 4: desktop/versioncheck.py

**Files:**
- Create: `desktop/versioncheck.py`
- Test: `tests/test_desktop_versioncheck.py`

**Interfaces:**
- Produces: `APP_VERSION = "0.1.0"`; `parse_version(s: str) -> tuple[int, ...]`; `check_for_update(server_url: str, fetch=..., current: str = APP_VERSION) -> str | None` (newer version string, else None; None on any network/parse error). `fetch(url: str, timeout: int) -> str` injectable (default wraps `requests.get(...).text`). Task 11's release script reads `APP_VERSION`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_desktop_versioncheck.py
"""Soft update check against GET <server>/api/scraper/version."""
from desktop.versioncheck import APP_VERSION, check_for_update, parse_version


def test_parse_version() -> None:
    assert parse_version("1.2.10") == (1, 2, 10)


def test_newer_version_reported() -> None:
    got = check_for_update("https://s", fetch=lambda url, timeout: '{"version": "9.9.9"}',
                           current="0.1.0")
    assert got == "9.9.9"


def test_same_or_older_returns_none() -> None:
    assert check_for_update("https://s", fetch=lambda u, timeout: '{"version": "0.0.1"}',
                            current="0.1.0") is None
    assert check_for_update("https://s", fetch=lambda u, timeout: f'{{"version": "{APP_VERSION}"}}') is None


def test_errors_return_none() -> None:
    def boom(url: str, timeout: int) -> str:
        raise OSError("offline")
    assert check_for_update("https://s", fetch=boom) is None
    assert check_for_update("https://s", fetch=lambda u, timeout: "garbage") is None
```

- [ ] **Step 2: Run to verify failure** — `uv run python -m pytest tests/test_desktop_versioncheck.py -q` → FAIL (no module).

- [ ] **Step 3: Implement**

```python
# desktop/versioncheck.py
"""Launch-time soft update check (vendor pages rot; stale scrapers need a nudge)."""
from __future__ import annotations

import json
import logging
from collections.abc import Callable

import requests

logger = logging.getLogger(__name__)

APP_VERSION = "0.1.0"
_TIMEOUT_S = 5

Fetcher = Callable[[str, int], str]


def _http_fetch(url: str, timeout: int) -> str:
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def parse_version(s: str) -> tuple[int, ...]:
    return tuple(int(part) for part in s.strip().split("."))


def check_for_update(server_url: str, fetch: Fetcher = _http_fetch,
                     current: str = APP_VERSION) -> str | None:
    """Return the server's version string when it is newer; None otherwise/on error."""
    url = server_url.rstrip("/") + "/api/scraper/version"
    try:
        latest = str(json.loads(fetch(url, _TIMEOUT_S))["version"])
        return latest if parse_version(latest) > parse_version(current) else None
    except Exception as exc:
        logger.info("update check skipped: %s", exc)
        return None
```

- [ ] **Step 4: Run tests** — `uv run python -m pytest tests/test_desktop_versioncheck.py -q` → PASS. `uv run ruff check .` → clean.

- [ ] **Step 5: Commit**

```bash
git add desktop/versioncheck.py tests/test_desktop_versioncheck.py
git commit -m "feat(desktop): launch-time update check"
git push
```

---

### Task 5: desktop/sync.py

**Files:**
- Create: `desktop/sync.py`
- Test: `tests/test_desktop_sync.py`

**Interfaces:**
- Consumes: `scrape_libraries.push_scrape(payload: dict, base_url: str, token: str) -> dict` (raises `requests.HTTPError` on non-2xx; the response object is at `exc.response`).
- Produces: `sync_payloads(payloads: list[dict], server_url: str, token: str, push=push_scrape) -> list[SyncResult]` with `@dataclass SyncResult: source: str; ok: bool; summary: str; retryable: bool`. 401 → `summary="token rejected — check your token"`, not retryable. Timeout/connection/5xx → retryable, `summary` starts `"server busy"`. One vendor failing never stops the others.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_desktop_sync.py
"""Per-vendor sync with friendly, retry-aware error mapping."""
import requests

from desktop.sync import SyncResult, sync_payloads

_P1 = {"source": "playstation", "games": []}
_P2 = {"source": "xbox", "games": []}


def _http_error(status: int) -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = status
    return requests.HTTPError(response=resp)


def test_success_reports_server_summary() -> None:
    def push(payload: dict, base_url: str, token: str) -> dict:
        return {"summary": f"added 3, updated 211 ({payload['source']})"}
    results = sync_payloads([_P1], "https://s", "tok", push=push)
    assert results == [SyncResult("playstation", True, "added 3, updated 211 (playstation)", False)]


def test_401_is_not_retryable_and_run_continues() -> None:
    def push(payload: dict, base_url: str, token: str) -> dict:
        if payload["source"] == "playstation":
            raise _http_error(401)
        return {"summary": "ok"}
    r1, r2 = sync_payloads([_P1, _P2], "https://s", "bad", push=push)
    assert (r1.ok, r1.retryable) == (False, False)
    assert "token rejected" in r1.summary
    assert r2.ok


def test_timeout_and_5xx_are_retryable() -> None:
    def push_timeout(payload: dict, base_url: str, token: str) -> dict:
        raise requests.Timeout()
    def push_500(payload: dict, base_url: str, token: str) -> dict:
        raise _http_error(500)
    assert sync_payloads([_P1], "https://s", "t", push=push_timeout)[0].retryable
    r = sync_payloads([_P1], "https://s", "t", push=push_500)[0]
    assert r.retryable and r.summary.startswith("server busy")
```

- [ ] **Step 2: Run to verify failure** — `uv run python -m pytest tests/test_desktop_sync.py -q` → FAIL (no module).

- [ ] **Step 3: Implement**

```python
# desktop/sync.py
"""Push scraped payloads to a BacklogQuest server, one vendor at a time."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import requests

from scrape_libraries import push_scrape

_TOKEN_REJECTED = "token rejected — check your token"
_SERVER_BUSY = "server busy — your CSV is safe, try sync again"

Pusher = Callable[[dict, str, str], dict]


@dataclass
class SyncResult:
    source: str
    ok: bool
    summary: str
    retryable: bool


def _failure(source: str, exc: Exception) -> SyncResult:
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        if exc.response.status_code == 401:
            return SyncResult(source, False, _TOKEN_REJECTED, retryable=False)
        return SyncResult(source, False, f"{_SERVER_BUSY} (HTTP {exc.response.status_code})",
                          retryable=True)
    return SyncResult(source, False, f"{_SERVER_BUSY} ({type(exc).__name__})", retryable=True)


def sync_payloads(payloads: list[dict], server_url: str, token: str,
                  push: Pusher = push_scrape) -> list[SyncResult]:
    """Sync every payload; a vendor failure never stops the others."""
    results: list[SyncResult] = []
    for payload in payloads:
        source = str(payload.get("source", "?"))
        try:
            response = push(payload, server_url, token)
            results.append(SyncResult(source, True, str(response.get("summary", "done")),
                                      retryable=False))
        except (requests.RequestException, OSError, ValueError) as exc:
            results.append(_failure(source, exc))
    return results
```

- [ ] **Step 4: Run tests** — `uv run python -m pytest tests/test_desktop_sync.py -q` → PASS. `uv run ruff check .` → clean.

- [ ] **Step 5: Commit**

```bash
git add desktop/sync.py tests/test_desktop_sync.py
git commit -m "feat(desktop): retry-aware vendor sync"
git push
```

---

### Task 6: desktop/runner.py — scrape orchestration state machine

**Files:**
- Create: `desktop/runner.py`
- Test: `tests/test_desktop_runner.py`

**Interfaces:**
- Consumes: `capturing_browser(headless=False, profile_dir=...)` (Task 1); vendor modules with `VENDOR_URL: str`, `SOURCE: str`, `collect(page, captured) -> list[ScrapedGame]`; `scrapers.base.write_scrape(source, games, out_dir) -> Path`.
- Produces: `class ScrapeRunner` used by Task 7's bridge:
  - `__init__(self, vendors: list[str], out_dir: Path, profile_dir: Path, sink: Callable[[dict], None], modules: dict | None = None, browser_factory=None, write=None)` — `modules` defaults to `scrape_libraries.SCRAPERS`; `browser_factory` defaults to `capturing_browser`; `write` defaults to `write_scrape`.
  - `run(self) -> dict[str, Path]` — blocking (call on a worker thread); returns `{source: payload_path}` for successful vendors.
  - `continue_login(self)`, `skip_vendor(self)` — thread-safe (set `threading.Event`s).
  - `captured_count(self) -> int` — live count of captured responses for ticking progress.
  - Sink event dicts (each has `"type"`): `{"type": "login", "vendor", "label"}`, `{"type": "collecting", "vendor"}`, `{"type": "done", "vendor", "count"}`, `{"type": "skipped", "vendor", "note"}`, `{"type": "finished", "results": {source: str(path)}}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_desktop_runner.py
"""ScrapeRunner state machine — fake browser + fake vendor modules, no Playwright."""
import threading
from contextlib import contextmanager
from pathlib import Path

from desktop.runner import ScrapeRunner


class _FakePage:
    def goto(self, url: str) -> None: ...
    def wait_for_timeout(self, ms: int) -> None: ...


class _FakeModule:
    VENDOR_URL = "https://vendor.example"
    SOURCE = "playstation"
    def __init__(self, games=None, boom: bool = False) -> None:
        self._games, self._boom = games or [], boom
    def collect(self, page, captured) -> list:
        if self._boom:
            raise RuntimeError("vendor page changed")
        captured.extend({"url": "x"} for _ in self._games)
        return self._games


@contextmanager
def _fake_browser(headless: bool = False, profile_dir: Path | None = None):
    yield _FakePage(), []


def _fake_write(source: str, games: list, out_dir: Path) -> Path:
    out = Path(out_dir) / f"{source}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("{}", encoding="utf-8")
    return out


def _run(runner: ScrapeRunner) -> list[dict]:
    events: list[dict] = []
    t = threading.Thread(target=runner.run)
    t.start()
    t.join(timeout=10)
    assert not t.is_alive()
    return events


def test_happy_path_emits_done_and_writes(tmp_path: Path) -> None:
    events: list[dict] = []
    runner = ScrapeRunner(["playstation"], tmp_path, tmp_path / "prof", events.append,
                          modules={"playstation": _FakeModule(games=["g1", "g2"])},
                          browser_factory=_fake_browser, write=_fake_write)
    runner.continue_login()          # pre-set: no user in tests
    results = runner.run()
    types = [e["type"] for e in events]
    assert types == ["login", "collecting", "done", "finished"]
    assert events[2]["count"] == 2
    assert Path(results["playstation"]).exists()


def test_skip_moves_to_next_vendor(tmp_path: Path) -> None:
    events: list[dict] = []
    mods = {"playstation": _FakeModule(games=["g"]), "xbox": _FakeModule(games=["g", "g"])}
    runner = ScrapeRunner(["playstation", "xbox"], tmp_path, tmp_path / "p", events.append,
                          modules=mods, browser_factory=_fake_browser, write=_fake_write)
    runner.skip_vendor()             # pre-set: skips playstation login wait
    runner.continue_login()          # xbox proceeds
    results = runner.run()
    assert [e["type"] for e in events if e["type"] in ("skipped", "done")] == ["skipped", "done"]
    assert "playstation" not in results and "xbox" in results


def test_vendor_error_is_skipped_not_fatal(tmp_path: Path) -> None:
    events: list[dict] = []
    mods = {"playstation": _FakeModule(boom=True), "xbox": _FakeModule(games=["g"])}
    runner = ScrapeRunner(["playstation", "xbox"], tmp_path, tmp_path / "p", events.append,
                          modules=mods, browser_factory=_fake_browser, write=_fake_write)
    runner.continue_login()
    runner.continue_login = runner.continue_login  # readability no-op
    runner._auto_continue = True                   # continue every vendor (test helper attr)
    results = runner.run()
    skipped = [e for e in events if e["type"] == "skipped"]
    assert skipped and "vendor page changed" in skipped[0]["note"]
    assert list(results) == ["xbox"]
```

Note for the implementer: `continue_login()` sets an event the runner consumes (and clears) per vendor. Support a `_auto_continue: bool = False` attribute — when True the runner never waits; tests use it for multi-vendor runs. Keep the wait loop pumping `page.wait_for_timeout(300)` exactly like `scrape_libraries._wait_for_user`.

- [ ] **Step 2: Run to verify failure** — `uv run python -m pytest tests/test_desktop_runner.py -q` → FAIL (no module).

- [ ] **Step 3: Implement**

```python
# desktop/runner.py
"""Sequential vendor scrape driver for the GUI: login-wait -> collect -> write.

Runs on a worker thread (Playwright sync API must stay off the GUI thread).
The GUI's Continue/Skip buttons set threading.Events consumed once per vendor.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

_PUMP_MS = 300

EventSink = Callable[[dict], None]


class ScrapeRunner:
    def __init__(self, vendors: list[str], out_dir: Path, profile_dir: Path,
                 sink: EventSink, modules: dict | None = None,
                 browser_factory=None, write=None) -> None:
        # Late imports keep this module import-safe for tests without Playwright.
        if modules is None:
            from scrape_libraries import SCRAPERS
            modules = SCRAPERS
        if browser_factory is None:
            from scrapers.base import capturing_browser
            browser_factory = capturing_browser
        if write is None:
            from scrapers.base import write_scrape
            write = write_scrape
        self._vendors, self._modules = vendors, modules
        self._out_dir, self._profile_dir = out_dir, profile_dir
        self._sink, self._browser_factory, self._write = sink, browser_factory, write
        self._continue = threading.Event()
        self._skip = threading.Event()
        self._captured: list = []
        self._auto_continue = False

    # -- thread-safe controls (called from the GUI thread) --------------------
    def continue_login(self) -> None:
        self._continue.set()

    def skip_vendor(self) -> None:
        self._skip.set()

    def captured_count(self) -> int:
        return len(self._captured)

    # -- worker-thread body ----------------------------------------------------
    def _wait_for_continue(self, page) -> bool:
        """Pump the browser until Continue (True) or Skip (False)."""
        while True:
            if self._skip.is_set():
                return False
            if self._auto_continue or self._continue.is_set():
                return True
            page.wait_for_timeout(_PUMP_MS)

    def _scrape_one(self, vendor: str) -> Path | None:
        mod = self._modules[vendor]
        self._sink({"type": "login", "vendor": vendor, "label": mod.SOURCE})
        with self._browser_factory(headless=False, profile_dir=self._profile_dir) as (page, captured):
            self._captured = captured
            page.goto(mod.VENDOR_URL)
            if not self._wait_for_continue(page):
                self._sink({"type": "skipped", "vendor": vendor, "note": "skipped"})
                return None
            self._sink({"type": "collecting", "vendor": vendor})
            games = mod.collect(page, captured)
        path = self._write(vendor, games, self._out_dir)
        self._sink({"type": "done", "vendor": vendor, "count": len(games)})
        return path

    def run(self) -> dict[str, Path]:
        results: dict[str, Path] = {}
        for vendor in self._vendors:
            self._skip.clear()
            try:
                path = self._scrape_one(vendor)
            except Exception as exc:   # vendor page changed, browser closed, ...
                logger.exception("vendor %s failed", vendor)
                self._sink({"type": "skipped", "vendor": vendor, "note": str(exc)})
                continue
            finally:
                self._continue.clear()
            if path is not None:
                results[vendor] = path
        self._sink({"type": "finished", "results": {v: str(p) for v, p in results.items()}})
        return results
```

- [ ] **Step 4: Run tests** — `uv run python -m pytest tests/test_desktop_runner.py -q` → PASS. Full suite `-n auto` still green. `uv run ruff check .` → clean.

- [ ] **Step 5: Commit**

```bash
git add desktop/runner.py tests/test_desktop_runner.py
git commit -m "feat(desktop): scrape runner state machine"
git push
```

---

### Task 7: GUI — bridge, main, ui assets, app icon

**Files:**
- Create: `desktop/bridge.py`, `desktop/main.py`, `desktop/ui/index.html`, `desktop/ui/style.css`, `desktop/ui/app.js`, `scripts/make_scraper_icon.py`, `desktop/assets/backlogquest.ico` (generated + committed)
- Modify: `pyproject.toml` (deps)
- Test: `tests/test_desktop_bridge.py` (bridge only; window/JS smoke is manual)

**Interfaces:**
- Consumes: everything from Tasks 2–6.
- Produces: `class Api` (pywebview `js_api`): `get_state() -> dict`, `save_settings(server_url: str, token: str) -> dict`, `start_scrape(vendors: list[str]) -> None`, `continue_login() -> None`, `skip_vendor() -> None`, `poll() -> dict` (drained events + `captured` count), `export_csv() -> str | None`, `sync() -> list[dict]`, `VENDOR_CHOICES = ("playstation", "xbox", "nintendo", "steam")`.

- [ ] **Step 1: Add dependencies**

```bash
uv add pywebview
uv add --group desktop-build pyinstaller pillow
```

- [ ] **Step 2: Write the failing bridge tests**

```python
# tests/test_desktop_bridge.py
"""Api bridge logic with an injected fake runner factory (no webview, no Playwright)."""
from pathlib import Path

from desktop.bridge import Api


class _FakeRunner:
    def __init__(self) -> None:
        self.continued = self.skipped = False
    def continue_login(self) -> None:
        self.continued = True
    def skip_vendor(self) -> None:
        self.skipped = True
    def captured_count(self) -> int:
        return 7
    def run(self) -> dict:
        return {}


def _api(tmp_path: Path) -> tuple[Api, _FakeRunner]:
    fake = _FakeRunner()
    api = Api(data_dir=tmp_path, exe_dir=tmp_path / "exe",
              runner_factory=lambda vendors, sink: fake)
    return api, fake


def test_get_state_reports_config_and_vendors(tmp_path: Path) -> None:
    api, _ = _api(tmp_path)
    state = api.get_state()
    assert state["server_url"] == "https://backlogquest.xyz"
    assert state["vendors"] == ["playstation", "xbox", "nintendo", "steam"]
    assert state["has_token"] is False


def test_save_settings_persists(tmp_path: Path) -> None:
    api, _ = _api(tmp_path)
    api.save_settings("https://s.example", "tok")
    assert Api(data_dir=tmp_path, exe_dir=tmp_path / "e",
               runner_factory=lambda v, s: None).get_state()["has_token"] is True


def test_start_scrape_wires_controls_and_events(tmp_path: Path) -> None:
    api, fake = _api(tmp_path)
    api.start_scrape(["playstation"])
    api._thread.join(timeout=5)
    api.continue_login(); api.skip_vendor()
    assert fake.continued and fake.skipped
    polled = api.poll()
    assert polled["captured"] == 7
    assert isinstance(polled["events"], list)
```

- [ ] **Step 3: Run to verify failure** — `uv run python -m pytest tests/test_desktop_bridge.py -q` → FAIL (no module).

- [ ] **Step 4: Implement the bridge**

```python
# desktop/bridge.py
"""JS<->Python API for the pywebview window. Thin: all logic lives in
config/runner/csv_export/sync/versioncheck; this only wires and queues."""
from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from pathlib import Path

from desktop import config as cfg
from desktop.csv_export import write_csv
from desktop.runner import ScrapeRunner
from desktop.sync import sync_payloads
from desktop.versioncheck import APP_VERSION, check_for_update

logger = logging.getLogger(__name__)

VENDOR_CHOICES: tuple[str, ...] = ("playstation", "xbox", "nintendo", "steam")

RunnerFactory = Callable[[list[str], Callable[[dict], None]], ScrapeRunner]


class Api:
    def __init__(self, data_dir: Path | None = None, exe_dir: Path | None = None,
                 runner_factory: RunnerFactory | None = None) -> None:
        self._data_dir = data_dir or cfg.appdata_dir()
        self._exe_dir = exe_dir or Path(__file__).resolve().parent
        self._runner_factory = runner_factory or self._default_runner
        self._config = cfg.load_config(self._exe_dir, self._data_dir)
        self._events: queue.Queue[dict] = queue.Queue()
        self._runner: ScrapeRunner | None = None
        self._thread: threading.Thread | None = None
        self._payload_paths: dict[str, str] = {}
        self.window = None          # set by main.py (needed for the save dialog)

    def _default_runner(self, vendors: list[str], sink: Callable[[dict], None]) -> ScrapeRunner:
        return ScrapeRunner(vendors, self._data_dir / "scraped",
                            self._data_dir / "pw-profile", sink)

    # -- settings ---------------------------------------------------------------
    def get_state(self) -> dict:
        return {"server_url": self._config.server_url,
                "has_token": bool(self._config.token),
                "vendors": list(VENDOR_CHOICES),
                "version": APP_VERSION,
                "update": check_for_update(self._config.server_url)}

    def save_settings(self, server_url: str, token: str) -> dict:
        self._config = cfg.AppConfig(server_url=server_url.strip() or cfg.DEFAULT_SERVER_URL,
                                     token=token.strip())
        cfg.save_config(self._config, self._data_dir)
        return self.get_state()

    # -- scraping ---------------------------------------------------------------
    def start_scrape(self, vendors: list[str]) -> None:
        chosen = [v for v in VENDOR_CHOICES if v in vendors]
        self._runner = self._runner_factory(chosen, self._on_event)
        self._thread = threading.Thread(target=self._runner.run, daemon=True)
        self._thread.start()

    def _on_event(self, event: dict) -> None:
        if event["type"] == "finished":
            self._payload_paths = event["results"]
        self._events.put(event)

    def continue_login(self) -> None:
        if self._runner:
            self._runner.continue_login()

    def skip_vendor(self) -> None:
        if self._runner:
            self._runner.skip_vendor()

    def poll(self) -> dict:
        events = []
        while not self._events.empty():
            events.append(self._events.get_nowait())
        captured = self._runner.captured_count() if self._runner else 0
        return {"events": events, "captured": captured}

    # -- results ----------------------------------------------------------------
    def _payloads(self) -> list[dict]:
        import json
        return [json.loads(Path(p).read_text(encoding="utf-8"))
                for p in self._payload_paths.values()]

    def export_csv(self) -> str | None:
        import webview
        paths = self.window.create_file_dialog(
            webview.SAVE_DIALOG, save_filename="backlogquest_library.csv",
            file_types=("CSV file (*.csv)",))
        if not paths:
            return None
        out = Path(paths if isinstance(paths, str) else paths[0])
        rows = write_csv(self._payloads(), out)
        logger.info("exported %d rows to %s", rows, out)
        return str(out)

    def sync(self) -> list[dict]:
        results = sync_payloads(self._payloads(), self._config.server_url, self._config.token)
        return [{"source": r.source, "ok": r.ok, "summary": r.summary,
                 "retryable": r.retryable} for r in results]
```

- [ ] **Step 5: Run bridge tests** — `uv run python -m pytest tests/test_desktop_bridge.py -q` → PASS.

- [ ] **Step 6: Implement main.py**

```python
# desktop/main.py
"""BacklogQuest Scraper entry point (dev: `uv run python -m desktop.main`)."""
from __future__ import annotations

import ctypes
import logging
import logging.handlers
import sys
from pathlib import Path

from desktop.bridge import Api
from desktop.config import appdata_dir

_BG = "#181A22"
_WEBVIEW2_HELP = ("The app needs Microsoft WebView2 (normally preinstalled on "
                  "Windows 10/11).\nInstall it from:\n"
                  "https://developer.microsoft.com/microsoft-edge/webview2/")


def _setup_logging(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        data_dir / "scraper.log", maxBytes=512_000, backupCount=2, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")


def _exe_dir() -> Path:
    if getattr(sys, "frozen", False):          # PyInstaller onedir
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def main() -> None:
    data_dir = appdata_dir()
    _setup_logging(data_dir)
    import webview
    api = Api(data_dir=data_dir, exe_dir=_exe_dir())
    ui = _exe_dir() / "ui" / "index.html" if getattr(sys, "frozen", False) \
        else Path(__file__).resolve().parent / "ui" / "index.html"
    api.window = webview.create_window(
        "BacklogQuest Scraper", url=str(ui), js_api=api,
        width=560, height=680, background_color=_BG)
    try:
        webview.start()
    except Exception:
        logging.getLogger(__name__).exception("webview failed to start")
        ctypes.windll.user32.MessageBoxW(None, _WEBVIEW2_HELP, "BacklogQuest Scraper", 0x10)


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Build the UI (three states, dark theme)**

`desktop/ui/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>BacklogQuest Scraper</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<header>
  <svg id="mark" viewBox="0 0 24 24" aria-hidden="true"><path fill="#8B93FF"
    d="M11 2h2v10h3l-4 6-4-6h3z"/><rect x="6" y="19" width="12" height="2" fill="#B7B9C6"/></svg>
  <h1>BacklogQuest Scraper</h1>
  <span id="version"></span>
</header>
<div id="update-banner" class="hidden">Update available — <span id="update-version"></span>
  from your BacklogQuest server's Settings page.</div>

<section id="state-setup">
  <p class="lead">Pick the stores to scrape. Your logins stay on this PC.</p>
  <div id="vendors"></div>
  <button id="start" class="primary">Start scraping</button>
  <div id="sync-chip" class="chip"></div>
  <details id="settings">
    <summary>Server settings</summary>
    <label>Server URL <input id="server-url" type="text"></label>
    <label>Import token <input id="token" type="password"
           placeholder="paste your token to enable sync"></label>
    <button id="save-settings">Save</button>
  </details>
</section>

<section id="state-scraping" class="hidden">
  <h2 id="scrape-vendor"></h2>
  <p id="scrape-instruction">Log into your account in the browser window, open your
     game library / purchase history, then click Continue.</p>
  <p id="scrape-progress" class="hidden"></p>
  <div class="row">
    <button id="continue" class="primary">Continue</button>
    <button id="skip">Skip this store</button>
  </div>
</section>

<section id="state-results" class="hidden">
  <h2>Done!</h2>
  <table id="results-table"></table>
  <div class="row">
    <button id="save-csv" class="primary">Save CSV</button>
    <button id="sync" class="hidden">Sync to BacklogQuest</button>
  </div>
  <p id="action-status"></p>
  <button id="again" class="linkish">Scrape again</button>
</section>
<script src="app.js"></script>
</body>
</html>
```

`desktop/ui/style.css`:

```css
:root { color-scheme: dark; }
* { box-sizing: border-box; margin: 0; }
body { background: #181A22; color: #E6E6EC; font: 15px/1.5 "Segoe UI", system-ui, sans-serif;
       padding: 20px; }
header { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
header h1 { font-size: 20px; flex: 1; }
#mark { width: 28px; height: 28px; }
#version { color: #B7B9C6; font-size: 12px; }
#update-banner { background: #232634; border: 1px solid #8B93FF; border-radius: 8px;
                 padding: 8px 12px; margin-bottom: 12px; font-size: 13px; }
section { background: #232634; border-radius: 12px; padding: 20px; }
.lead, label { color: #B7B9C6; }
#vendors { margin: 14px 0; display: grid; gap: 8px; }
#vendors label { display: flex; gap: 8px; align-items: center; color: #E6E6EC; }
button { border: 0; border-radius: 8px; padding: 10px 18px; font-size: 15px;
         background: #2E3242; color: #E6E6EC; cursor: pointer; }
button.primary { background: #8B93FF; color: #14151C; font-weight: 600; }
button.linkish { background: none; color: #8B93FF; padding: 6px 0; }
button:disabled { opacity: .5; cursor: default; }
.row { display: flex; gap: 10px; margin-top: 14px; }
.chip { margin-top: 12px; font-size: 13px; color: #B7B9C6; }
.chip.ok { color: #7ED9A0; }
details { margin-top: 14px; color: #B7B9C6; }
details input { width: 100%; margin: 4px 0 10px; padding: 8px; border-radius: 6px;
                border: 1px solid #3A3F52; background: #181A22; color: #E6E6EC; }
#results-table { width: 100%; margin: 12px 0; border-collapse: collapse; }
#results-table td { padding: 6px 4px; border-bottom: 1px solid #3A3F52; }
#results-table td:last-child { text-align: right; color: #B7B9C6; }
#scrape-progress { color: #8B93FF; margin-top: 10px; }
#action-status { margin-top: 10px; color: #B7B9C6; font-size: 13px; white-space: pre-line; }
.hidden { display: none !important; }
```

`desktop/ui/app.js`:

```javascript
// Polls the Python bridge (window.pywebview.api) and drives the three states.
const VENDOR_LABELS = {playstation: "PlayStation", xbox: "Xbox",
                       nintendo: "Nintendo", steam: "Steam"};
let doneCounts = {}, skippedNotes = {}, hasToken = false, pollTimer = null;

const $ = (id) => document.getElementById(id);
const show = (id) => ["state-setup", "state-scraping", "state-results"]
    .forEach((s) => $(s).classList.toggle("hidden", s !== id));

async function init() {
  const st = await window.pywebview.api.get_state();
  hasToken = st.has_token;
  $("version").textContent = "v" + st.version;
  if (st.update) { $("update-version").textContent = "v" + st.update;
                   $("update-banner").classList.remove("hidden"); }
  $("server-url").value = st.server_url;
  $("vendors").innerHTML = st.vendors.map((v) =>
    `<label><input type="checkbox" value="${v}" checked> ${VENDOR_LABELS[v]}</label>`).join("");
  renderChip();
}

function renderChip() {
  const chip = $("sync-chip");
  chip.textContent = hasToken ? "✓ Sync configured"
                              : "CSV only — add a token to sync";
  chip.classList.toggle("ok", hasToken);
}

async function saveSettings() {
  const st = await window.pywebview.api.save_settings($("server-url").value, $("token").value);
  hasToken = st.has_token; renderChip();
}

function startPolling() {
  pollTimer = setInterval(async () => {
    const {events, captured} = await window.pywebview.api.poll();
    if (!$("scrape-progress").classList.contains("hidden"))
      $("scrape-progress").textContent = `collecting… ${captured} responses captured`;
    for (const e of events) handleEvent(e);
  }, 500);
}

function handleEvent(e) {
  if (e.type === "login") {
    show("state-scraping");
    $("scrape-vendor").textContent = VENDOR_LABELS[e.vendor];
    $("scrape-instruction").classList.remove("hidden");
    $("scrape-progress").classList.add("hidden");
    $("continue").disabled = false;
  } else if (e.type === "collecting") {
    $("scrape-instruction").classList.add("hidden");
    $("scrape-progress").classList.remove("hidden");
    $("continue").disabled = true;
  } else if (e.type === "done") {
    doneCounts[e.vendor] = e.count;
  } else if (e.type === "skipped") {
    skippedNotes[e.vendor] = e.note;
  } else if (e.type === "finished") {
    clearInterval(pollTimer);
    renderResults();
  }
}

function renderResults() {
  show("state-results");
  const rows = Object.entries(doneCounts).map(([v, n]) =>
      `<tr><td>${VENDOR_LABELS[v]}</td><td>${n} titles</td></tr>`)
    .concat(Object.entries(skippedNotes).map(([v, note]) =>
      `<tr><td>${VENDOR_LABELS[v]}</td><td>skipped—${note === "skipped" ? "" : " " + note}
       ${note !== "skipped" ? "(site changed? check for an update)" : ""}</td></tr>`));
  $("results-table").innerHTML = rows.join("");
  $("sync").classList.toggle("hidden", !hasToken);
}

$("start").onclick = () => {
  doneCounts = {}; skippedNotes = {};
  const vendors = [...document.querySelectorAll("#vendors input:checked")].map((i) => i.value);
  if (!vendors.length) return;
  window.pywebview.api.start_scrape(vendors);
  startPolling();
};
$("continue").onclick = () => window.pywebview.api.continue_login();
$("skip").onclick = () => window.pywebview.api.skip_vendor();
$("save-settings").onclick = saveSettings;
$("save-csv").onclick = async () => {
  const path = await window.pywebview.api.export_csv();
  if (path) $("action-status").textContent = "Saved: " + path;
};
$("sync").onclick = async () => {
  $("action-status").textContent = "Syncing…";
  const results = await window.pywebview.api.sync();
  $("action-status").textContent = results.map((r) =>
    `${VENDOR_LABELS[r.source] || r.source}: ${r.summary}`).join("\n");
};
$("again").onclick = () => { show("state-setup"); };
window.addEventListener("pywebviewready", init);
```

- [ ] **Step 8: Generate the .ico**

```python
# scripts/make_scraper_icon.py
"""Dev-only: render docs/branding/backlogquest-icon.svg to desktop/assets/backlogquest.ico.
Uses the Playwright browser we already have (no cairosvg dep) + Pillow."""
from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(message)s")
ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "docs" / "branding" / "backlogquest-icon.svg"
OUT = ROOT / "desktop" / "assets" / "backlogquest.ico"
SIZE = 256


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    png_path = OUT.with_suffix(".png")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": SIZE, "height": SIZE})
        page.goto(SVG.as_uri())
        page.locator("svg").screenshot(path=str(png_path), omit_background=True)
        browser.close()
    img = Image.open(png_path).convert("RGBA").resize((SIZE, SIZE))
    img.save(OUT, sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
    png_path.unlink()
    logging.info("wrote %s", OUT)


if __name__ == "__main__":
    main()
```

Run: `uv run python scripts/make_scraper_icon.py` → `desktop/assets/backlogquest.ico` exists. Commit the .ico.

- [ ] **Step 9: Manual dev smoke**

Run: `uv run python -m desktop.main`
Verify: window opens dark-themed with sword mark; vendor checkboxes render; "CSV only" chip; open Server settings, paste any string as token, Save → chip flips to "✓ Sync configured". Close. (Full scrape smoke happens in Task 11 against real vendors.)

- [ ] **Step 10: Run everything** — `uv run python -m pytest -n auto -q` → all pass; `uv run ruff check .` → clean.

- [ ] **Step 11: Commit**

```bash
git add desktop/ scripts/make_scraper_icon.py pyproject.toml uv.lock tests/test_desktop_bridge.py
git commit -m "feat(desktop): pywebview GUI (bridge, window, themed UI, icon)"
git push
```

---

### Task 8: Server — personalized download + version routes

**Files:**
- Modify: `app.py` (`_PUBLIC_PATHS` ~line 68; new routes near `/api/import/scrape`)
- Test: `tests/test_scraper_download.py`

**Interfaces:**
- Consumes: `auth` gate (`_PUBLIC_PATHS`, existing `before_request`), `BACKLOGQUEST_IMPORT_TOKEN` env.
- Produces:
  - `GET /download/scraper?flavor=portable` — gated; streams `backlogquest-scraper-portable.zip` with `<zip-root>/backlogquest.json` injected (`{"server_url": <request root>, "token": <import token>}`).
  - `GET /download/scraper?flavor=installer` — gated; serves the installer exe as-is.
  - `GET /api/scraper/version` — public; `{"version": "<contents of version.txt>"}` or 404.
  - Artifacts dir: env `BACKLOGQUEST_SCRAPER_DIR` (default `/opt/backlogquest/scraper`). Files: `backlogquest-scraper-portable.zip`, `BacklogQuest-Scraper-Setup.exe`, `version.txt`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_scraper_download.py
"""Personalized scraper downloads + public version endpoint."""
import io
import json
import zipfile
from pathlib import Path

import pytest

import app as app_module


@pytest.fixture
def artifacts(tmp_path: Path, monkeypatch) -> Path:
    src = io.BytesIO()
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("BacklogQuest Scraper/app.exe", b"fake-exe")
    (tmp_path / "backlogquest-scraper-portable.zip").write_bytes(src.getvalue())
    (tmp_path / "BacklogQuest-Scraper-Setup.exe").write_bytes(b"fake-installer")
    (tmp_path / "version.txt").write_text("0.1.0", encoding="utf-8")
    monkeypatch.setenv("BACKLOGQUEST_SCRAPER_DIR", str(tmp_path))
    monkeypatch.setenv("BACKLOGQUEST_IMPORT_TOKEN", "sekrit")
    return tmp_path


@pytest.fixture
def client(artifacts):
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def test_portable_zip_gets_sidecar_with_token(client) -> None:
    resp = client.get("/download/scraper?flavor=portable")
    assert resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(resp.data)) as z:
        names = z.namelist()
        assert "BacklogQuest Scraper/app.exe" in names
        sidecar = json.loads(z.read("BacklogQuest Scraper/backlogquest.json"))
    assert sidecar["token"] == "sekrit"
    assert sidecar["server_url"].startswith("http")


def test_installer_served_as_is(client) -> None:
    resp = client.get("/download/scraper?flavor=installer")
    assert resp.status_code == 200 and resp.data == b"fake-installer"


def test_unknown_flavor_400_and_missing_artifact_404(client, artifacts: Path) -> None:
    assert client.get("/download/scraper?flavor=weird").status_code == 400
    (artifacts / "backlogquest-scraper-portable.zip").unlink()
    assert client.get("/download/scraper?flavor=portable").status_code == 404


def test_version_endpoint_public(client, monkeypatch) -> None:
    # Auth ON: /api/scraper/version must still answer (it's in _PUBLIC_PATHS).
    monkeypatch.setenv("BACKLOGQUEST_PASSWORD_HASH", "pbkdf2:sha256:x$y$z")
    assert client.get("/api/scraper/version").get_json() == {"version": "0.1.0"}
    # ...while the downloads are gated.
    assert client.get("/download/scraper?flavor=portable").status_code in (302, 401)
```

- [ ] **Step 2: Run to verify failure** — `uv run python -m pytest tests/test_scraper_download.py -q` → FAIL (404s: routes missing).

- [ ] **Step 3: Implement in app.py**

Add `"/api/scraper/version"` to `_PUBLIC_PATHS`:

```python
_PUBLIC_PATHS = frozenset({"/login", "/logout", "/healthz", "/api/scraper/version"})
```

Add routes (module imports: `io`, `zipfile` join app.py's existing import block; `send_file` from flask):

```python
# --- desktop scraper distribution -------------------------------------------
_SCRAPER_DIR_ENV = "BACKLOGQUEST_SCRAPER_DIR"
_SCRAPER_DIR_DEFAULT = "/opt/backlogquest/scraper"
_PORTABLE_ZIP = "backlogquest-scraper-portable.zip"
_INSTALLER_EXE = "BacklogQuest-Scraper-Setup.exe"
_FLAVOR_PORTABLE = "portable"
_FLAVOR_INSTALLER = "installer"


def _scraper_dir() -> Path:
    return Path(os.environ.get(_SCRAPER_DIR_ENV) or _SCRAPER_DIR_DEFAULT)


def _personalized_zip(src: Path, server_url: str, token: str) -> io.BytesIO:
    """Copy the portable zip, adding backlogquest.json beside the exe (zip root dir)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        names = zin.namelist()
        for item in zin.infolist():
            zout.writestr(item, zin.read(item.filename))
        root = names[0].split("/")[0] + "/" if names and "/" in names[0] else ""
        sidecar = json.dumps({"server_url": server_url, "token": token})
        zout.writestr(f"{root}backlogquest.json", sidecar)
    buf.seek(0)
    return buf


@app.route("/download/scraper")
def download_scraper():
    """Personalized (portable) or generic (installer) desktop-app download."""
    flavor = request.args.get("flavor", _FLAVOR_PORTABLE)
    if flavor not in (_FLAVOR_PORTABLE, _FLAVOR_INSTALLER):
        return jsonify({"error": "unknown flavor"}), 400
    if flavor == _FLAVOR_INSTALLER:
        path = _scraper_dir() / _INSTALLER_EXE
        if not path.exists():
            return jsonify({"error": "installer not available"}), 404
        return send_file(path, as_attachment=True, download_name=_INSTALLER_EXE)
    src = _scraper_dir() / _PORTABLE_ZIP
    if not src.exists():
        return jsonify({"error": "portable build not available"}), 404
    token = os.environ.get("BACKLOGQUEST_IMPORT_TOKEN", "")
    buf = _personalized_zip(src, request.url_root.rstrip("/"), token)
    return send_file(buf, as_attachment=True, download_name=_PORTABLE_ZIP,
                     mimetype="application/zip")


@app.route("/api/scraper/version")
def scraper_version():
    """Public: latest desktop-app version (read from the artifacts dir)."""
    path = _scraper_dir() / "version.txt"
    if not path.exists():
        return jsonify({"error": "no version published"}), 404
    return jsonify({"version": path.read_text(encoding="utf-8").strip()})
```

If `app.py` is near its size limit, put `_personalized_zip` + helpers in a new `scraper_dist.py` and import; keep routes in `app.py` like every other route.

- [ ] **Step 4: Run tests** — `uv run python -m pytest tests/test_scraper_download.py -q` → PASS. Full suite `-n auto` green (the gate tests assert exemptions). `uv run ruff check .` → clean.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_scraper_download.py
git commit -m "feat(web): personalized scraper download + public version endpoint"
git push
```

---

### Task 9: Settings page — "Get the scraper" section

**Files:**
- Modify: `templates/settings.html`
- Test: extend `tests/test_scraper_download.py`

**Interfaces:**
- Consumes: routes from Task 8.
- Produces: a visible card on `/settings` with both download buttons.

- [ ] **Step 1: Write the failing test** (append to `tests/test_scraper_download.py`)

```python
def test_settings_page_offers_both_downloads(client) -> None:
    html = client.get("/settings").data.decode()
    assert "Get the scraper" in html
    assert "/download/scraper?flavor=installer" in html
    assert "/download/scraper?flavor=portable" in html
```

- [ ] **Step 2: Run to verify failure** — `uv run python -m pytest tests/test_scraper_download.py::test_settings_page_offers_both_downloads -q` → FAIL.

- [ ] **Step 3: Add the section**

Append a card to `templates/settings.html`, matching the page's existing card markup (copy the class names of a sibling section — the page uses Tailwind-style `bg-*/rounded/p-*` cards; mirror the nearest existing card's classes exactly):

```html
<!-- Get the scraper -->
<div class="bg-surface rounded-lg p-6 mt-6">
    <h2 class="text-lg font-semibold mb-2">Get the scraper</h2>
    <p class="text-sm text-gray-400 mb-4">
        Scrape your PlayStation, Xbox, Nintendo, and Steam libraries on your own PC.
        Export a CSV to use anywhere — and it syncs here automatically when it has
        your token.
    </p>
    <div class="flex gap-3">
        <a href="/download/scraper?flavor=installer"
           class="px-4 py-2 rounded-md bg-accent text-black font-medium">
            Installer <span class="text-xs opacity-75">(paste your token on first run)</span>
        </a>
        <a href="/download/scraper?flavor=portable"
           class="px-4 py-2 rounded-md bg-surface-lighter">
            Portable <span class="text-xs opacity-75">(your token comes baked in)</span>
        </a>
    </div>
</div>
```

- [ ] **Step 4: Run tests** — `uv run python -m pytest tests/test_scraper_download.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add templates/settings.html tests/test_scraper_download.py
git commit -m "feat(web): scraper download section on settings page"
git push
```

---

### Task 10: Packaging — PyInstaller spec, Inno script, release script

**Files:**
- Create: `desktop/build.spec`, `installer/backlogquest-scraper.iss`, `release_scraper.ps1`

**Interfaces:**
- Consumes: `desktop/main.py` entry, `desktop/assets/backlogquest.ico`, `desktop/versioncheck.APP_VERSION`.
- Produces: `dist/BacklogQuest Scraper/` (onedir) → `dist/backlogquest-scraper-portable.zip` + `dist/BacklogQuest-Scraper-Setup.exe`, scp'd to the droplet's `/opt/backlogquest/scraper/` with `version.txt`.

- [ ] **Step 1: PyInstaller spec**

```python
# desktop/build.spec
"""PyInstaller onedir build. Run from repo root:
uv run pyinstaller desktop/build.spec --noconfirm
Playwright's node driver is bundled via collect_all; browsers are NOT bundled
(the app uses the user's installed Chrome/Edge via channels)."""
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
for pkg in ("playwright", "webview"):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h
datas += [("desktop/ui", "ui")]

a = Analysis(["desktop/main.py"], pathex=["."], datas=datas, binaries=binaries,
             hiddenimports=hiddenimports, noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, exclude_binaries=True, name="BacklogQuest Scraper",
          icon="desktop/assets/backlogquest.ico", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="BacklogQuest Scraper")
```

Note: PyInstaller must NOT trigger Playwright's browser download; `collect_all("playwright")` only bundles the pip package + driver. If the build machine has `PLAYWRIGHT_BROWSERS_PATH` set, unset it for the build.

- [ ] **Step 2: Inno Setup script**

```ini
; installer/backlogquest-scraper.iss — compile with:
;   iscc /DAppVersion=<version> installer\backlogquest-scraper.iss
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
[Setup]
AppName=BacklogQuest Scraper
AppVersion={#AppVersion}
AppPublisher=BacklogQuest
DefaultDirName={localappdata}\Programs\BacklogQuest Scraper
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputBaseFilename=BacklogQuest-Scraper-Setup
OutputDir=..\dist
SetupIconFile=..\desktop\assets\backlogquest.ico
UninstallDisplayIcon={app}\BacklogQuest Scraper.exe

[Files]
Source: "..\dist\BacklogQuest Scraper\*"; DestDir: "{app}"; Flags: recursesubdirs

[Icons]
Name: "{userprograms}\BacklogQuest Scraper"; Filename: "{app}\BacklogQuest Scraper.exe"

[Run]
; Bootstrap WebView2 if missing (no-op when present).
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; \
  Flags: skipifdoesntexist; Check: not IsWebView2Installed

[Code]
function IsWebView2Installed: Boolean;
begin
  Result := RegKeyExists(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}')
    or RegKeyExists(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}');
end;
```

(The WebView2 bootstrapper download step is in the release script; when absent the `skipifdoesntexist` flag makes it a no-op and `main.py`'s MessageBox fallback still covers the edge case.)

- [ ] **Step 3: Release script**

```powershell
# release_scraper.ps1 — build both flavors and publish to the droplet.
$ErrorActionPreference = "Stop"
$version = (uv run python -c "from desktop.versioncheck import APP_VERSION; print(APP_VERSION)").Trim()
Write-Host "Building BacklogQuest Scraper v$version"

uv run pyinstaller desktop/build.spec --noconfirm
if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }

# Portable zip (folder as zip root).
$portable = "dist/backlogquest-scraper-portable.zip"
if (Test-Path $portable) { Remove-Item $portable }
Compress-Archive -Path "dist/BacklogQuest Scraper" -DestinationPath $portable

# Optional: fetch the tiny WebView2 bootstrapper for the installer to carry.
$bootstrapper = "installer/MicrosoftEdgeWebview2Setup.exe"
if (-not (Test-Path $bootstrapper)) {
  Invoke-WebRequest "https://go.microsoft.com/fwlink/p/?LinkId=2124703" -OutFile $bootstrapper
}

& iscc /DAppVersion=$version installer\backlogquest-scraper.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed (is iscc on PATH?)" }

Set-Content -Path "dist/version.txt" -Value $version -NoNewline
ssh gametracker "mkdir -p /opt/backlogquest/scraper"
scp $portable "dist/BacklogQuest-Scraper-Setup.exe" dist/version.txt gametracker:/opt/backlogquest/scraper/
Write-Host "Published v$version to the droplet."
```

- [ ] **Step 4: Build + smoke locally**

Run: `uv run pyinstaller desktop/build.spec --noconfirm` → `dist/BacklogQuest Scraper/BacklogQuest Scraper.exe` exists.
Launch it: window opens (frozen paths resolve; log at `%APPDATA%\BacklogQuest\scraper.log`).
Sidecar check: drop `backlogquest.json` (`{"server_url": "https://backlogquest.xyz", "token": "test"}`) next to the exe, delete `%APPDATA%\BacklogQuest\config.json`, relaunch → chip shows "✓ Sync configured".
Inno: `iscc /DAppVersion=0.1.0 installer\backlogquest-scraper.iss` → Setup exe builds (requires Inno Setup 6 installed; `winget install JRSoftware.InnoSetup` if missing). Install → Start-menu entry launches → uninstall cleanly.

- [ ] **Step 5: Real-vendor smoke (owner present)**

From the frozen exe: Start scraping with one vendor (owner logs in) → Continue → results show a real count → Save CSV opens in Excel → Sync (owner token) reports the server summary.

- [ ] **Step 6: Commit**

```bash
git add desktop/build.spec installer/backlogquest-scraper.iss release_scraper.ps1
git commit -m "feat(desktop): PyInstaller + Inno Setup packaging and release script"
git push
```

---

### Task 11: Publish v0.1.0 + deploy server routes

**Files:** none new (operational task)

- [ ] **Step 1: Publish artifacts** — Run `./release_scraper.ps1` (needs owner approval for the ssh/scp step).
- [ ] **Step 2: Deploy the web app** — on the droplet: `cd /opt/backlogquest/app && sudo -u gametracker git pull --ff-only && systemctl restart backlogquest` (owner approval).
- [ ] **Step 3: Verify live** — `curl https://backlogquest.xyz/api/scraper/version` → `{"version": "0.1.0"}`; logged-in browser: Settings shows "Get the scraper"; download portable zip → contains `backlogquest.json` with the real token; download installer → runs.
- [ ] **Step 4: Update memory session-state** with shipped status.

---

## Self-Review (completed)

- **Spec coverage:** seam→T1, config/sidecar→T2, CSV→T3, version check→T4+T8, sync errors→T5, runner/Skip/error-handling→T6, GUI three states + WebView2 fallback + log file→T7, download routes/zip injection/gating→T8, settings section→T9, onedir/installer/portable/release→T10, live publish→T11. No-Chrome error message: covered by existing `_launch_context` fallback logging + runner's skipped-note surfacing (`type: skipped` renders the exception note in results).
- **Placeholder scan:** none — every step has complete code/commands.
- **Type consistency:** `capturing_browser(headless, profile_dir)` (T1) matches T6's factory call; `SyncResult` fields match T7's dict mapping; `write_csv(payloads, out_path)` matches T7's `export_csv`; artifact filenames in T8 tests match T10's release script outputs.
