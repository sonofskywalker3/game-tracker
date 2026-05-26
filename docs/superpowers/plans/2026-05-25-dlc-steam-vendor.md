# DLC Steam Vendor (SP2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Steam as an end-to-end vendor — import owned Steam games, deep-fetch each game's full DLC catalogue from the keyless storefront, and mark owned DLC by Steam-appid set-intersection.

**Architecture:** Hybrid auth — `GetOwnedGames` (Web API key + SteamID64 from `config.json`) for owned games; the logged-in store session's `dynamicstore/userdata` (`rgOwnedApps`) for owned-appid carriers; keyless `appdetails` (cached) for the catalogue. A new `steam_dlc` engine reconciles/creates `dlc` rows (reusing SP1's `dlc`/`dlc_external_ids`) and flips owned by appid. The pipeline routes `source=="steam"` to `steam_dlc` (skipping IGDB enrichment + the title-based `mark_ownership`); IGDB enrichment is taught to skip vendor-catalogue games.

**Tech Stack:** Python 3, SQLite (stdlib `sqlite3`), `requests`, Playwright (live scrape only), pytest. Tests via `uv run python -m pytest`; lint via `uv run ruff check` (NO `ruff format` — hand-aligned).

**Spec:** `docs/superpowers/specs/2026-05-25-dlc-steam-vendor-design.md`

**Conventions:** Work on `main`; conventional commits, NO co-author trailer. Never run the app/live scrapers or touch the real `games.db` — tests use temp DBs + injected fakes only. Never commit `config.json`, `.steam_cache/`.

---

### Task 1: Steam credentials in config

**Files:**
- Modify: `config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_config.py`:

```python
import json

import config


def test_get_steam_credentials_present(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"steam_api_key": "KEY", "steam_id": "76561190000000000"}))
    monkeypatch.setattr(config, "CONFIG_PATH", p)
    assert config.get_steam_credentials() == ("KEY", "76561190000000000")


def test_get_steam_credentials_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nope.json")
    assert config.get_steam_credentials() == (None, None)


def test_get_steam_credentials_blank(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"steam_api_key": "  ", "steam_id": ""}))
    monkeypatch.setattr(config, "CONFIG_PATH", p)
    assert config.get_steam_credentials() == (None, None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_config.py -v`
Expected: FAIL — `AttributeError: module 'config' has no attribute 'get_steam_credentials'`.

- [ ] **Step 3: Add the keys + getter to `config.py`**

Replace the `DEFAULT_CONFIG` dict (`config.py:10-13`) with:

```python
DEFAULT_CONFIG = {
    "twitch_client_id": "",
    "twitch_client_secret": "",
    "steam_api_key": "",
    "steam_id": "",
}
```

Add this function at the end of `config.py`:

```python
def get_steam_credentials():
    """Get Steam Web API key + SteamID64 if configured."""
    config = load_config()
    api_key = config.get("steam_api_key", "").strip()
    steam_id = config.get("steam_id", "").strip()

    if api_key and steam_id:
        return api_key, steam_id
    return None, None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_config.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add config.py tests/test_config.py
git commit -m "feat: steam_api_key + steam_id config credentials"
```

---

### Task 2: Steam scraper module (pure parsers)

**Files:**
- Create: `scrapers/steam.py`
- Modify: `scrapers/base.py:29` (add `"steam"` to `VALID_SOURCES`)
- Create: `tests/test_parse_steam.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_parse_steam.py`:

```python
from scrapers import steam
from scrapers.base import VALID_SOURCES


def test_parse_owned_games():
    payload = {"response": {"games": [
        {"appid": 620, "name": "Portal 2"},
        {"appid": 0, "name": "bad"},     # no appid -> skipped
        {"appid": 440, "name": ""},      # no name -> skipped
    ]}}
    games = steam.parse_owned_games(payload)
    assert len(games) == 1
    g = games[0]
    assert g.title == "Portal 2" and g.source == "steam" and g.platform == "Steam"
    assert g.external_id == "620" and g.kind == "game"
    assert "620" in (g.cover_url or "")


def test_parse_userdata_makes_id_only_addon_carriers():
    carriers = steam.parse_userdata({"rgOwnedApps": [620, 730, 12345]})
    assert [c.external_id for c in carriers] == ["620", "730", "12345"]
    assert all(c.kind == "addon" and c.source == "steam" for c in carriers)


def test_parsers_handle_empty():
    assert steam.parse_owned_games({}) == []
    assert steam.parse_userdata({}) == []


def test_steam_is_a_valid_source():
    assert "steam" in VALID_SOURCES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_parse_steam.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scrapers.steam'`.

- [ ] **Step 3: Add `"steam"` to `VALID_SOURCES`**

In `scrapers/base.py:29`, change:

```python
VALID_SOURCES = frozenset({"playstation", "xbox", "nintendo"})
```

to:

```python
VALID_SOURCES = frozenset({"playstation", "xbox", "nintendo", "steam"})
```

- [ ] **Step 4: Create `scrapers/steam.py`**

```python
"""Steam library scraper (hybrid: Web API key for owned games + session for owned DLC).

Owned games come from IPlayerService/GetOwnedGames (key + SteamID64 from config).
Owned-DLC ownership comes from the logged-in store session's dynamicstore/userdata
(`rgOwnedApps` — every owned appid incl. DLC), carried as id-only kind="addon" rows.
The DLC catalogue itself is fetched later by steam_dlc (keyless appdetails). The pure
parsers are unit-tested; `collect` drives the live calls and is verified manually.
"""
from __future__ import annotations

import logging

import requests

import config
from scrapers.base import ScrapedGame

logger = logging.getLogger(__name__)

VENDOR_URL = "https://store.steampowered.com/account/licenses/"
SOURCE = "steam"
PLATFORM = "Steam"

OWNED_GAMES_URL = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
USERDATA_URL = "https://store.steampowered.com/dynamicstore/userdata/"
CAPSULE_URL = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"


def parse_owned_games(payload: dict) -> list[ScrapedGame]:
    """Map a GetOwnedGames response to game ScrapedGames (kind='game')."""
    games = ((payload or {}).get("response") or {}).get("games") or []
    out: list[ScrapedGame] = []
    for g in games:
        appid = g.get("appid")
        name = (g.get("name") or "").strip()
        if not appid or not name:
            continue
        out.append(ScrapedGame(
            title=name, platform=PLATFORM, source=SOURCE,
            external_id=str(appid), cover_url=CAPSULE_URL.format(appid=appid),
            source_title=name))
    return out


def parse_userdata(payload: dict) -> list[ScrapedGame]:
    """Map dynamicstore/userdata rgOwnedApps to id-only owned-appid carriers
    (kind='addon'). These ride the scrape payload so the catalogue/ownership step
    knows which appids the user owns; the title is just the appid placeholder."""
    owned = (payload or {}).get("rgOwnedApps") or []
    return [ScrapedGame(title=str(appid), platform=PLATFORM, source=SOURCE,
                        external_id=str(appid), kind="addon")
            for appid in owned]


def collect(page, captured: list | None = None) -> list[ScrapedGame]:
    """Owned Steam games (via Web API key) + owned-appid carriers (via session).

    GetOwnedGames needs the key + SteamID64 from config; if absent, no games are
    returned (logged, not fatal). rgOwnedApps is read from the logged-in store
    session via page.request (cookies carry auth).
    """
    api_key, steam_id = config.get_steam_credentials()
    games: list[ScrapedGame] = []
    if api_key and steam_id:
        params = {"key": api_key, "steamid": steam_id, "include_appinfo": "true",
                  "include_played_free_games": "true", "format": "json"}
        resp = requests.get(OWNED_GAMES_URL, params=params, timeout=30)
        resp.raise_for_status()
        games = parse_owned_games(resp.json())
        logger.info("steam: %d owned games via GetOwnedGames", len(games))
    else:
        logger.warning("steam: no API key / SteamID in config.json; skipping owned-games fetch")

    owned: list[ScrapedGame] = []
    resp = page.request.get(USERDATA_URL)
    if resp.ok:
        owned = parse_userdata(resp.json())
        logger.info("steam: %d owned appids (games+DLC) via userdata", len(owned))
    else:
        logger.warning("steam: userdata fetch failed (%s); owned DLC will be empty", resp.status)
    return games + owned
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_parse_steam.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
git add scrapers/steam.py scrapers/base.py tests/test_parse_steam.py
git commit -m "feat: steam scraper (owned games + owned-appid carriers)"
```

---

### Task 3: `steam_dlc` catalogue + id-ownership engine

**Files:**
- Create: `steam_dlc.py`
- Modify: `igdb_dlc.py` (`enrich_missing` skips vendor-catalogue games)
- Create: `tests/test_steam_dlc.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_steam_dlc.py`:

```python
import json

import igdb_dlc
import models
import steam_dlc


# --- pure parsers ---

def test_parse_catalogue():
    assert steam_dlc.parse_catalogue({"dlc": [10, 20, 30]}) == [10, 20, 30]
    assert steam_dlc.parse_catalogue({}) == []


def test_parse_name_and_type():
    assert steam_dlc.parse_appdetails_name({"name": "  Season Pass "}) == "Season Pass"
    assert steam_dlc.parse_type({"type": "dlc"}) == "dlc"


# --- fetch_appdetails caching ---

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload
    def raise_for_status(self):
        pass
    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return _FakeResp(self.payload)


def test_fetch_appdetails_caches(tmp_path):
    sess = _FakeSession({"620": {"success": True, "data": {"type": "game", "name": "Portal 2"}}})
    data = steam_dlc.fetch_appdetails(620, cache_dir=tmp_path, session=sess, delay_s=0)
    assert data["name"] == "Portal 2" and sess.calls == 1
    # second call is a cache hit -> no new network call
    data2 = steam_dlc.fetch_appdetails(620, cache_dir=tmp_path, session=sess, delay_s=0)
    assert data2["name"] == "Portal 2" and sess.calls == 1
    assert (tmp_path / "620.json").exists()


def test_fetch_appdetails_failure_returns_none(tmp_path):
    sess = _FakeSession({"99": {"success": False}})
    assert steam_dlc.fetch_appdetails(99, cache_dir=tmp_path, session=sess, delay_s=0) is None


# --- enrich_and_mark (temp DB, injected fetch) ---

def _seed_steam_game(conn, appid="620", title="Portal 2"):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT id FROM games WHERE title=?", (title,)).fetchone()[0]
    conn.execute("INSERT INTO game_external_ids (game_id, source, external_id) "
                 "VALUES (?, 'steam', ?)", (gid, appid))
    return gid


def _fake_fetch(catalogue_map):
    return lambda appid: catalogue_map.get(appid)


def test_enrich_and_mark_creates_catalogue_and_marks_owned(temp_db):
    conn = models.get_db()
    gid = _seed_steam_game(conn)
    conn.commit()
    fetch = _fake_fetch({
        620: {"type": "game", "name": "Portal 2", "dlc": [10, 20]},
        10: {"type": "dlc", "name": "DLC A"},
        20: {"type": "dlc", "name": "DLC B"},
    })
    rep = steam_dlc.enrich_and_mark(conn, {10}, fetch=fetch)
    conn.commit()
    assert rep.games == 1 and rep.catalogue_added == 2 and rep.owned_marked == 1
    rows = {r["name"]: r["owned"] for r in conn.execute(
        "SELECT name, owned FROM dlc WHERE game_id=?", (gid,))}
    assert rows == {"DLC A": 1, "DLC B": 0}
    ext = {r["external_id"] for r in conn.execute(
        "SELECT external_id FROM dlc_external_ids WHERE source='steam'")}
    assert ext == {"10", "20"}
    conn.close()


def test_enrich_and_mark_idempotent(temp_db):
    conn = models.get_db()
    _seed_steam_game(conn)
    conn.commit()
    fetch = _fake_fetch({620: {"type": "game", "dlc": [10]}, 10: {"name": "DLC A"}})
    steam_dlc.enrich_and_mark(conn, {10}, fetch=fetch)
    conn.commit()
    rep = steam_dlc.enrich_and_mark(conn, {10}, fetch=fetch)
    conn.commit()
    assert rep.catalogue_added == 0 and rep.owned_marked == 0
    assert conn.execute("SELECT COUNT(*) FROM dlc").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM dlc_external_ids").fetchone()[0] == 1
    conn.close()


def test_enrich_and_mark_reconciles_existing_row_by_name(temp_db):
    conn = models.get_db()
    gid = _seed_steam_game(conn)
    conn.execute("INSERT INTO dlc (game_id, name, source) VALUES (?, 'DLC A', 'igdb')", (gid,))
    conn.commit()
    fetch = _fake_fetch({620: {"type": "game", "dlc": [10]}, 10: {"name": "DLC A"}})
    rep = steam_dlc.enrich_and_mark(conn, {10}, fetch=fetch)
    conn.commit()
    assert rep.catalogue_added == 0 and rep.owned_marked == 1
    assert conn.execute("SELECT COUNT(*) FROM dlc WHERE game_id=?", (gid,)).fetchone()[0] == 1
    assert conn.execute("SELECT owned FROM dlc WHERE name='DLC A'").fetchone()[0] == 1
    assert conn.execute(
        "SELECT dlc_id FROM dlc_external_ids WHERE source='steam' AND external_id='10'"
    ).fetchone() is not None
    conn.close()


def test_enrich_missing_skips_steam_games(temp_db, monkeypatch):
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('Steamy', 'steamy')")
    sid = conn.execute("SELECT id FROM games WHERE title='Steamy'").fetchone()[0]
    conn.execute("INSERT INTO game_external_ids (game_id, source, external_id) "
                 "VALUES (?, 'steam', '620')", (sid,))
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('Other', 'other')")
    oid = conn.execute("SELECT id FROM games WHERE title='Other'").fetchone()[0]
    conn.commit()

    seen = []

    def fake_enrich_game(c, gid, client_id, token, *, slug=None):
        seen.append(gid)
        return {"matched": False, "cover_set": False, "added": 0, "existing": 0}

    monkeypatch.setattr(igdb_dlc, "enrich_game", fake_enrich_game)
    igdb_dlc.enrich_missing(conn, client_id="c", token="t")
    assert oid in seen and sid not in seen
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_steam_dlc.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'steam_dlc'`.

- [ ] **Step 3: Create `steam_dlc.py`**

```python
"""Steam DLC catalogue + ownership (vendor store = source of truth).

For each owned Steam game, fetch its full DLC catalogue from the keyless storefront
`appdetails` endpoint, reconcile-or-create each DLC as a dlc row (recording the
Steam appid in dlc_external_ids), and mark owned exactly those whose appid the user
owns -- a pure appid set-intersection (no name heuristics). appdetails responses are
cached on disk. Pure parsers are unit-tested; the network fetch is isolated/cached
and injected into enrich_and_mark for offline tests. See
docs/superpowers/specs/2026-05-25-dlc-steam-vendor-design.md.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

import models

logger = logging.getLogger(__name__)

APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
CACHE_DIR = Path(__file__).parent / ".steam_cache"
REQUEST_DELAY_S = 1.5  # keep under ~200 appdetails / 5 min per IP


def parse_catalogue(data: dict) -> list[int]:
    """The DLC appids listed for a base game (data.dlc), as ints."""
    return [int(x) for x in (data or {}).get("dlc") or []]


def parse_appdetails_name(data: dict) -> str:
    return ((data or {}).get("name") or "").strip()


def parse_type(data: dict) -> str:
    return ((data or {}).get("type") or "").strip()


def fetch_appdetails(appid: int, *, cache_dir: Path = CACHE_DIR,
                     session=requests, delay_s: float = REQUEST_DELAY_S) -> dict | None:
    """Return the appdetails `data` object for an appid (cached on disk), or None.

    A cache hit skips the network entirely. On a miss, GET appdetails, cache the
    `data` object (an empty object for a not-found/failed app), throttle, and
    return it (None when there is no data).
    """
    cache_dir = Path(cache_dir)
    cache_file = cache_dir / f"{appid}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8")) or None
    resp = session.get(APPDETAILS_URL, params={"appids": appid, "l": "english"}, timeout=30)
    resp.raise_for_status()
    entry = (resp.json() or {}).get(str(appid)) or {}
    data = entry.get("data") if entry.get("success") else None
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(data or {}, ensure_ascii=False), encoding="utf-8")
    if delay_s:
        time.sleep(delay_s)
    return data


@dataclass
class SteamReport:
    """Outcome of a Steam catalogue+ownership pass."""
    games: int = 0
    catalogue_added: int = 0
    owned_marked: int = 0
    errors: int = 0
    marked_items: list[int] = field(default_factory=list)  # dlc_ids flipped owned


def _record_ext_id(conn: sqlite3.Connection, dlc_id: int, ext: str, name: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO dlc_external_ids (dlc_id, source, external_id, source_title) "
        "VALUES (?, 'steam', ?, ?)", (dlc_id, ext, name))


def _reconcile_or_create(conn: sqlite3.Connection, game_id: int, name: str,
                         dlc_appid: int) -> tuple[int, bool]:
    """Return (dlc_id, created). Reconcile by Steam appid -> by normalized-name
    equality (recording the appid) -> else create a steam-sourced row (owned=0)."""
    ext = str(dlc_appid)
    row = conn.execute(
        "SELECT dlc_id FROM dlc_external_ids WHERE source='steam' AND external_id=?",
        (ext,)).fetchone()
    if row:
        return row[0], False
    target = models.normalize_title(name)
    for r in conn.execute("SELECT id, name FROM dlc WHERE game_id=?", (game_id,)):
        if models.normalize_title(r["name"]) == target:
            _record_ext_id(conn, r["id"], ext, name)
            return r["id"], False
    try:
        cur = conn.execute(
            "INSERT INTO dlc (game_id, name, kind, owned, source) "
            "VALUES (?, ?, 'dlc', 0, 'steam')", (game_id, name))
    except sqlite3.IntegrityError:
        existing = conn.execute(
            "SELECT id FROM dlc WHERE game_id=? AND name=?", (game_id, name)).fetchone()
        _record_ext_id(conn, existing[0], ext, name)
        return existing[0], False
    new_id = cur.lastrowid
    _record_ext_id(conn, new_id, ext, name)
    return new_id, True


def _mark_owned(conn: sqlite3.Connection, report: SteamReport, dlc_id: int) -> None:
    """Flip a dlc row owned (0 -> 1 only)."""
    owned = conn.execute("SELECT owned FROM dlc WHERE id=?", (dlc_id,)).fetchone()[0]
    if owned:
        return
    conn.execute("UPDATE dlc SET owned=1 WHERE id=?", (dlc_id,))
    report.owned_marked += 1
    report.marked_items.append(dlc_id)


def enrich_and_mark(conn: sqlite3.Connection, owned_app_ids: set[int], *,
                    fetch=fetch_appdetails) -> SteamReport:
    """Populate each owned Steam game's DLC catalogue and mark owned by appid.

    For every game with a `steam` external id: fetch its catalogue, reconcile-or-
    create each catalogue DLC as a dlc row, and set owned=1 for those whose appid is
    in owned_app_ids. 0 -> 1 only, idempotent. A per-app fetch error is logged and
    skipped. `fetch(appid) -> data|None` is injected for offline tests.
    """
    report = SteamReport()
    steam_games = conn.execute(
        "SELECT g.id AS game_id, gx.external_id AS appid "
        "FROM games g JOIN game_external_ids gx ON gx.game_id = g.id "
        "WHERE gx.source = 'steam'").fetchall()
    for grow in steam_games:
        game_id = grow["game_id"]
        try:
            game_appid = int(grow["appid"])
        except (TypeError, ValueError):
            continue
        try:
            data = fetch(game_appid)
        except requests.RequestException as exc:
            report.errors += 1
            logger.warning("steam appdetails failed for game %s (app %s): %s",
                           game_id, game_appid, exc)
            continue
        report.games += 1
        for dlc_appid in parse_catalogue(data):
            try:
                dlc_data = fetch(dlc_appid)
            except requests.RequestException as exc:
                report.errors += 1
                logger.warning("steam appdetails failed for dlc %s: %s", dlc_appid, exc)
                continue
            name = parse_appdetails_name(dlc_data)
            if not name:
                continue
            dlc_id, created = _reconcile_or_create(conn, game_id, name, dlc_appid)
            if created:
                report.catalogue_added += 1
            if dlc_appid in owned_app_ids:
                _mark_owned(conn, report, dlc_id)
    return report
```

- [ ] **Step 4: Teach `igdb_dlc.enrich_missing` to skip vendor-catalogue games**

In `igdb_dlc.py`, add this constant just below the `_DLC_RELATIONS` definition (near the top):

```python
# Games whose DLC catalogue comes from a vendor store (not IGDB) are skipped by
# enrich_missing -- IGDB is only the fallback catalogue for games without one.
VENDOR_CATALOGUE_SOURCES = ("steam",)
```

In `enrich_missing`, replace the `ids = [...]` line:

```python
    ids = [r[0] for r in conn.execute("SELECT id FROM games WHERE igdb_id IS NULL")]
```

with:

```python
    placeholders = ",".join("?" * len(VENDOR_CATALOGUE_SOURCES))
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM games WHERE igdb_id IS NULL AND id NOT IN "
        f"(SELECT game_id FROM game_external_ids WHERE source IN ({placeholders}))",
        VENDOR_CATALOGUE_SOURCES)]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_steam_dlc.py -v`
Expected: PASS (8 passed).

- [ ] **Step 6: Run the IGDB suite to confirm no regression**

Run: `uv run python -m pytest tests/test_igdb_dlc.py tests/test_import_scraped.py -v`
Expected: PASS (the exclusion only narrows which games enrich; existing fixtures have no steam external ids).

- [ ] **Step 7: Commit**

```bash
git add steam_dlc.py igdb_dlc.py tests/test_steam_dlc.py
git commit -m "feat: steam_dlc catalogue + appid ownership engine"
```

---

### Task 4: Pipeline routing (web + CLI)

**Files:**
- Modify: `scrape_service.py` (`_run_pipeline`)
- Modify: `import_scraped.py` (`main` ownership section)
- Modify: `tests/test_scrape_service.py` (add a Steam routing test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scrape_service.py`:

```python
def test_run_pipeline_steam_routes_to_steam_dlc(temp_db, monkeypatch):
    import steam_dlc
    import import_scraped

    # IGDB enrichment and the title matcher must NOT be called for steam.
    def _boom_enrich(conn):
        raise AssertionError("run_dlc_enrichment should not run for steam")

    def _boom_mark(conn, addons, **kw):
        raise AssertionError("mark_ownership should not run for steam")

    monkeypatch.setattr(import_scraped, "run_dlc_enrichment", _boom_enrich)
    import dlc_ownership
    monkeypatch.setattr(dlc_ownership, "mark_ownership", _boom_mark)

    fetch = (lambda appid: {
        620: {"type": "game", "name": "Portal 2", "dlc": [10, 20]},
        10: {"type": "dlc", "name": "DLC A"},
        20: {"type": "dlc", "name": "DLC B"},
    }.get(appid))
    real = steam_dlc.enrich_and_mark
    monkeypatch.setattr(steam_dlc, "enrich_and_mark",
                        lambda conn, owned, **kw: real(conn, owned, fetch=fetch))

    games = [
        ScrapedGame(title="Portal 2", platform="Steam", source="steam", external_id="620"),
        ScrapedGame(title="10", platform="Steam", source="steam", external_id="10", kind="addon"),
    ]
    conn = models.get_db()
    summary = scrape_service._run_pipeline(conn, "steam", games)
    conn.commit()
    assert summary["new_games"] == 1
    assert summary["owned_marked"] == 1     # appid 10 owned
    assert summary["created"] == 2          # DLC A + DLC B catalogue rows
    assert [d["name"] for d in summary["newly_owned"]] == ["DLC A"]
    added = {d["name"]: d["owned"] for d in summary["added_dlc"]}
    assert added == {"DLC A": True, "DLC B": False}
    assert summary["review"] == []
    conn.close()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run python -m pytest tests/test_scrape_service.py::test_run_pipeline_steam_routes_to_steam_dlc -v`
Expected: FAIL — `mark_ownership`/`run_dlc_enrichment` get called (the `AssertionError` boom) because `_run_pipeline` has no steam branch yet.

- [ ] **Step 3: Rewrite `scrape_service._run_pipeline`**

Replace the entire `_run_pipeline` function in `scrape_service.py` with:

```python
def _run_pipeline(conn: sqlite3.Connection, vendor: str, games: list) -> dict:
    """Back up the DB, import games, then populate DLC + ownership per vendor.

    Steam uses the id-based deep-fetch (steam_dlc: catalogue + appid ownership);
    other vendors use IGDB enrichment + the title-based mark_ownership. Returns a
    summary dict (counts + DLC added this run, rows flipped owned this run, and
    add-ons needing review). Fuzzy matches use the safe non-interactive confirmer.
    """
    import dlc_ownership
    import import_scraped
    import steam_dlc

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

    if vendor == "steam":
        _set(phase="matching", message="fetching Steam DLC catalogue...")
        owned_app_ids = {int(r["external_id"]) for r in addons if r.get("external_id")}
        sr = steam_dlc.enrich_and_mark(conn, owned_app_ids)
        conn.commit()
        owned_marked, created, dlc_added = sr.owned_marked, sr.catalogue_added, sr.catalogue_added
        enrich_skipped = True
        marked_dlc_ids = list(sr.marked_items)
        review = []
    else:
        _set(phase="enriching", message="enriching DLC from IGDB...")
        enrich = import_scraped.run_dlc_enrichment(conn)
        _set(phase="matching", message="matching DLC ownership...")
        report = dlc_ownership.mark_ownership(conn, addons)
        conn.commit()
        owned_marked, created = report.marked, report.created
        dlc_added = (enrich or {}).get("added", 0)
        enrich_skipped = enrich is None
        marked_dlc_ids = [m.dlc_id for m in report.marked_items]
        review = [{"title": m.addon_title, "reason": m.reason} for m in report.review]

    added_dlc = [
        {"game": r["title"], "name": r["name"], "kind": r["kind"], "owned": bool(r["owned"])}
        for r in conn.execute(
            "SELECT g.title, d.name, d.kind, d.owned FROM dlc d JOIN games g ON g.id = d.game_id "
            "WHERE d.created_at >= ? ORDER BY g.title, d.name", (run_started,))
    ]
    newly_owned = []
    for dlc_id in marked_dlc_ids:
        row = conn.execute(
            "SELECT g.title, d.name FROM dlc d JOIN games g ON g.id = d.game_id WHERE d.id = ?",
            (dlc_id,)).fetchone()
        if row:
            newly_owned.append({"game": row["title"], "name": row["name"]})

    return {
        "vendor": vendor,
        "scraped": len(rows),
        "new_games": stats.new_games,
        "platform_links": stats.platform_links_added,
        "dlc_added": dlc_added,
        "enrich_skipped": enrich_skipped,
        "owned_marked": owned_marked,
        "created": created,
        "backup_path": backup_path,
        "added_dlc": added_dlc,
        "newly_owned": newly_owned,
        "review": review,
    }
```

- [ ] **Step 4: Add the CLI branch in `import_scraped.main`**

In `import_scraped.py`, find the tail of `main` from the `if not args.dry_run and not args.no_dlc:` enrichment call through the ownership block. Replace this block:

```python
    if not args.dry_run and not args.no_dlc:
        run_dlc_enrichment(conn)
    if not args.no_ownership and all_addons:
        if args.dry_run:
            logger.info("(dry run skipped DLC enrichment, so ownership preview "
                        "omits not-yet-imported games)")
        report = dlc_ownership.mark_ownership(conn, all_addons, dry_run=args.dry_run)
        if not args.dry_run:
            conn.commit()
        _log_ownership(report, dry_run=args.dry_run)
    conn.close()
```

with:

```python
    steam_addons = [a for a in all_addons if a.get("source") == "steam"]
    other_addons = [a for a in all_addons if a.get("source") != "steam"]

    if not args.dry_run and not args.no_dlc:
        run_dlc_enrichment(conn)                       # skips steam (vendor catalogue)
        import steam_dlc
        owned_app_ids = {int(a["external_id"]) for a in steam_addons if a.get("external_id")}
        sr = steam_dlc.enrich_and_mark(conn, owned_app_ids)
        conn.commit()
        if sr.games:
            logger.info("STEAM DLC: %d games, +%d catalogue, %d owned marked, %d errors",
                        sr.games, sr.catalogue_added, sr.owned_marked, sr.errors)
    if not args.no_ownership and other_addons:
        if args.dry_run:
            logger.info("(dry run skipped DLC enrichment, so ownership preview "
                        "omits not-yet-imported games)")
        report = dlc_ownership.mark_ownership(conn, other_addons, dry_run=args.dry_run)
        if not args.dry_run:
            conn.commit()
        _log_ownership(report, dry_run=args.dry_run)
    conn.close()
```

- [ ] **Step 5: Run the routing + regression tests**

Run: `uv run python -m pytest tests/test_scrape_service.py tests/test_import_scraped.py -v`
Expected: PASS — the new steam routing test plus all existing pipeline tests (the non-steam branch is behavior-identical and keeps the same summary keys).

- [ ] **Step 6: Commit**

```bash
git add scrape_service.py import_scraped.py tests/test_scrape_service.py
git commit -m "feat: route steam scrapes to steam_dlc (web + CLI)"
```

---

### Task 5: Registration & wiring

**Files:**
- Modify: `scrape_libraries.py` (import + `SCRAPERS`)
- Modify: `scrape_service.py:26` (`VENDORS`)
- Modify: `models.py` (default platforms)
- Modify: `templates/base.html` (Steam button)
- Modify: `.gitignore` (`.steam_cache/`)
- Modify: `tests/test_scrape_service.py` (`test_vendors_constant`)

- [ ] **Step 1: Update the `test_vendors_constant` test (failing)**

In `tests/test_scrape_service.py`, replace:

```python
def test_vendors_constant():
    assert scrape_service.VENDORS == ("playstation", "xbox", "nintendo")
```

with:

```python
def test_vendors_constant():
    assert scrape_service.VENDORS == ("playstation", "xbox", "nintendo", "steam")


def test_steam_registered_in_scrapers():
    import scrape_libraries
    assert "steam" in scrape_libraries.SCRAPERS
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run python -m pytest tests/test_scrape_service.py::test_vendors_constant tests/test_scrape_service.py::test_steam_registered_in_scrapers -v`
Expected: FAIL (VENDORS lacks `"steam"`; `scrape_libraries.SCRAPERS` lacks `"steam"`).

- [ ] **Step 3: Register the vendor**

In `scrape_libraries.py:18`, change the import:

```python
from scrapers import nintendo, playstation, xbox
```

to:

```python
from scrapers import nintendo, playstation, steam, xbox
```

In `scrape_libraries.py` `SCRAPERS` (lines 28-32), add the steam entry:

```python
SCRAPERS = {
    "playstation": playstation,
    "xbox": xbox,
    "nintendo": nintendo,
    "steam": steam,
}
```

In `scrape_service.py:26`, change:

```python
VENDORS = ("playstation", "xbox", "nintendo")
```

to:

```python
VENDORS = ("playstation", "xbox", "nintendo", "steam")
```

- [ ] **Step 4: Add the Steam platform default**

In `models.py` `init_db`, in the `platforms` list (around lines 191-196), add the Steam row:

```python
    platforms = [
        ("PlayStation", "PS", "modern_console"),
        ("Nintendo Switch", "Switch", "modern_console"),
        ("Xbox", "Xbox", "modern_console"),
        ("PC", "PC", "pc"),
        ("Steam", "Steam", "pc"),
    ]
```

- [ ] **Step 5: Add the Steam UI button**

In `templates/base.html`, in the `#scrape-vendors` div (after the Nintendo button, line 152), add:

```html
                            <button onclick="startScrape('steam')" class="scrape-vendor-btn flex-1 px-3 py-2 bg-surface hover:bg-surface-lighter rounded-lg text-white text-sm transition-colors">Steam</button>
```

- [ ] **Step 6: Ignore the appdetails cache**

In `.gitignore`, under the "Scraper artifacts" section (after `scraped/`, line 28), add:

```
.steam_cache/
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_scrape_service.py -v`
Expected: PASS (incl. `test_vendors_constant`, `test_steam_registered_in_scrapers`).

- [ ] **Step 8: Commit**

```bash
git add scrape_libraries.py scrape_service.py models.py templates/base.html .gitignore tests/test_scrape_service.py
git commit -m "feat: register steam vendor (CLI, web, UI button, platform)"
```

---

### Task 6: Full suite, lint, final review

**Files:** none (verification only)

- [ ] **Step 1: Run the full suite**

Run: `uv run python -m pytest`
Expected: PASS (all green).

- [ ] **Step 2: Lint**

Run: `uv run ruff check`
Expected: no errors. Do NOT run `ruff format`. Fix any findings by hand, matching surrounding style.

- [ ] **Step 3: Import sanity check**

Run: `uv run python -c "import config, steam_dlc, scrape_service, import_scraped, igdb_dlc; from scrapers import steam; print('imports OK')"`
Expected: `imports OK`.

- [ ] **Step 4: Commit any lint fixes (if any)**

```bash
git add -A
git commit -m "chore: lint fixes for steam vendor"
```

- [ ] **Step 5: Manual verification (owner, live — not run by agents)**

1. Add `steam_api_key` + `steam_id` (SteamID64) to `config.json`.
2. Back up first (the web pipeline also auto-backs up).
3. `uv run python app.py`, open the Add Game modal → "Or sync a whole library" → **Steam**, log in to the Steam store in the opened browser, then Continue.
4. Expect owned Steam games imported, each game's DLC catalogue populated, owned DLC marked (✓), unowned DLC present (the future missing-DLC view, SP7); the hero `owned/total DLC` tile rises.
5. Spot-check a game with DLC (e.g., a game where you own some-but-not-all DLC) in its DLC tab.

---

## Notes for the implementer

- **Reuses SP1's tables** (`dlc`, `dlc_external_ids`) and discipline (reconcile by id → name-equality → create; 0→1-only; UNIQUE-collision fallback). No schema change.
- **`dlc.kind`** for created Steam rows is always `'dlc'` (the scrape `kind="addon"` carriers are id-only and never become dlc rows themselves).
- **IGDB stays the fallback** for non-Steam games; the `enrich_missing` exclusion (Task 3 Step 4) keeps it from also cataloguing Steam games (avoids duplicate/competing rows).
- **Offline tests only**: `enrich_and_mark` takes an injected `fetch`; `fetch_appdetails` is tested with a fake session + temp `cache_dir` + `delay_s=0`. Never hit the live Steam API in tests, never touch the real `games.db`, never launch the browser.
- The **live Steam scrape** (real `collect`, `GetOwnedGames`, `userdata`, live `appdetails`) is the owner's manual verification step.
