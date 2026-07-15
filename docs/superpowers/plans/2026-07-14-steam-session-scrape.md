# Steam Session-Token Scrape Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **This run:** owner directed inline execution (superpowers:executing-plans) — one module + tests, no subagents.

**Goal:** Make the desktop app's Steam scrape work with zero configuration by minting a `webapi_token` from the logged-in browser session, with a 3-tier ladder (config creds → session token → honest error) and a cache-busted retry for the flaky userdata DLC fetch; ship as v0.1.4.

**Architecture:** All scraper changes live in `scrapers/steam.py` (shared by CLI/web/desktop per the scraper-fixes-shared rule). Two new pure helpers parse the token endpoint and the JWT `sub` claim; `collect()` gains a session-token path using `page.request` (Playwright APIRequestContext, cookies carry auth). No UI changes.

**Tech Stack:** Python 3.11+, Playwright sync API (`page.request.get`), `requests` (config-creds path only), pytest with fake page/request doubles.

**Spec:** `docs/superpowers/specs/2026-07-13-steam-session-scrape-design.md` (approved, committed @ be3dea9).

## Global Constraints

- Tests: `uv run python -m pytest -n auto -q` — NEVER plain `pytest` (ModuleNotFoundError: models).
- Lint: `ruff check` only — NEVER `ruff format` (codebase is hand-aligned).
- Commit directly to `main` and push; no branches.
- Never touch the real `games.db` or the running web app.
- `api._window` in `desktop/api.py` stays underscore-private (pywebview#1815 hang; regression test exists) — this plan does not touch desktop bridge code, but any incidental edit there must preserve it.
- Tier-3 error message, verbatim: `Log into Steam in the browser window first, then press Continue`.
- Publish via `./release_scraper.ps1` (full publish authorized) after `$env:PATH = "$env:LOCALAPPDATA\Programs\Inno Setup 6;$env:PATH"`. Droplet-side commands (ssh git pull / service restart) need owner approval — the script's own `ssh mkdir -p` + `scp` publish steps are authorized.
- Live smoke uses the already-logged-in profile `%APPDATA%\BacklogQuest\pw-profile` headlessly; read-only network calls only. Desktop app must not be running (persistent context locks the profile).

---

### Task 1: Pure token helpers (`parse_webapi_token`, `steamid_from_token`)

**Files:**
- Modify: `scrapers/steam.py` (add imports, `TOKEN_CONFIG_URL`, two helpers)
- Test: `tests/test_steam_session_token.py` (create)

**Interfaces:**
- Produces: `parse_webapi_token(payload: dict) -> str` (`""` when absent/malformed); `steamid_from_token(token: str) -> str` (`sub` claim or `""` on any parse issue); module constant `TOKEN_CONFIG_URL`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_steam_session_token.py`:

```python
"""Pure helpers for the Steam session-token path (mint token from logged-in session)."""
import base64
import json

from scrapers import steam


def _jwt(claims: dict) -> str:
    """Build an unsigned JWT-shaped token: header.payload.sig (payload is what matters)."""
    seg = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{seg}.sig"


def test_parse_webapi_token_present():
    assert steam.parse_webapi_token({"data": {"webapi_token": "tok123"}}) == "tok123"


def test_parse_webapi_token_absent_or_malformed():
    assert steam.parse_webapi_token({}) == ""
    assert steam.parse_webapi_token({"data": {}}) == ""
    assert steam.parse_webapi_token({"data": "nope"}) == ""
    assert steam.parse_webapi_token({"data": {"webapi_token": 42}}) == ""
    assert steam.parse_webapi_token(None) == ""


def test_steamid_from_token_valid_jwt():
    token = _jwt({"sub": "76561198012345678", "aud": ["web:store"]})
    assert steam.steamid_from_token(token) == "76561198012345678"


def test_steamid_from_token_payload_needs_padding():
    # A short claims dict whose base64 length is not a multiple of 4 once '=' is stripped.
    token = _jwt({"sub": "7656119"})
    assert steam.steamid_from_token(token) == "7656119"


def test_steamid_from_token_garbage():
    assert steam.steamid_from_token("") == ""
    assert steam.steamid_from_token("not-a-jwt") == ""
    assert steam.steamid_from_token("a.!!!notbase64!!!.c") == ""
    assert steam.steamid_from_token("a.aGVsbG8.c") == ""  # payload not JSON


def test_steamid_from_token_missing_or_nonstring_sub():
    assert steam.steamid_from_token(_jwt({"aud": ["web:store"]})) == ""
    assert steam.steamid_from_token(_jwt({"sub": 123})) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_steam_session_token.py -q`
Expected: FAIL / ERROR with `AttributeError: module 'scrapers.steam' has no attribute 'parse_webapi_token'`

- [ ] **Step 3: Implement the helpers**

In `scrapers/steam.py`, add to the imports block:

```python
import base64
import binascii
import json
```

Add below the existing URL constants:

```python
TOKEN_CONFIG_URL = "https://store.steampowered.com/pointssummary/ajaxgetasyncconfig"
```

Add the helpers after `parse_userdata`:

```python
def parse_webapi_token(payload: dict) -> str:
    """Extract data.webapi_token from the pointssummary config; '' when absent/malformed."""
    data = (payload or {}).get("data") if isinstance(payload, dict) else None
    token = data.get("webapi_token") if isinstance(data, dict) else None
    return token if isinstance(token, str) else ""


def steamid_from_token(token: str) -> str:
    """SteamID64 from the JWT's `sub` claim; '' on any parse issue.

    No signature verification — we only read our own token back."""
    try:
        seg = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))
        sub = claims.get("sub")
    except (IndexError, ValueError, binascii.Error) as exc:
        logger.debug("steam: webapi_token not parseable (%s)", exc)
        return ""
    return sub if isinstance(sub, str) else ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_steam_session_token.py -q`
Expected: 6 passed

- [ ] **Step 5: Lint and commit**

```powershell
uv run ruff check scrapers/steam.py tests/test_steam_session_token.py
git add scrapers/steam.py tests/test_steam_session_token.py
git commit -m "feat(steam): pure helpers to mint session webapi_token + SteamID64"
```

---

### Task 2: `collect()` three-tier ladder + cache-busted userdata retry

**Files:**
- Modify: `scrapers/steam.py:56-87` (rewrite `collect`, add `_games_via_session`, `_fetch_userdata`, constants, module docstring)
- Modify: `tests/test_steam_collect_guard.py` (guard now needs a fake page whose token fetch fails)
- Test: `tests/test_steam_session_collect.py` (create)

**Interfaces:**
- Consumes: `parse_webapi_token`, `steamid_from_token`, `TOKEN_CONFIG_URL` from Task 1; existing `parse_owned_games`, `parse_userdata`, `OWNED_GAMES_URL`, `USERDATA_URL`, `config.get_steam_credentials()`.
- Produces: `collect(page, captured=None, progress=None) -> list[ScrapedGame]` (signature unchanged); `LOGIN_REQUIRED_MSG` constant. `page` double needs `.request.get(url, params=None)` returning an object with `.ok`, `.status`, `.json()`, plus `page.wait_for_timeout(ms)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_steam_session_collect.py`:

```python
"""collect() 3-tier ladder: config creds -> session token -> honest error;
userdata fetched after games with one cache-busted retry when empty."""
import base64
import json

import pytest

from scrapers import steam


def _jwt(claims: dict) -> str:
    seg = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{seg}.sig"


TOKEN_PAYLOAD = {"data": {"webapi_token": _jwt({"sub": "76561198012345678"})}}
GAMES_PAYLOAD = {"response": {"games": [{"appid": 620, "name": "Portal 2"}]}}
USERDATA_PAYLOAD = {"rgOwnedApps": [620, 730]}


class FakeResponse:
    def __init__(self, payload=None, status=200):
        self._payload, self.status, self.ok = payload, status, status == 200

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakePage:
    """Doubles page.request.get + wait_for_timeout; responses served per URL prefix, in order."""
    def __init__(self, routes: dict[str, list[FakeResponse]]):
        self._routes, self.calls, self.waits = routes, [], []
        self.request = self

    def get(self, url, params=None):
        self.calls.append((url, params))
        for prefix, queue in self._routes.items():
            if url.startswith(prefix):
                return queue.pop(0) if len(queue) > 1 else queue[0]
        raise AssertionError(f"unexpected URL: {url}")

    def wait_for_timeout(self, ms):
        self.waits.append(ms)


@pytest.fixture
def no_creds(monkeypatch):
    monkeypatch.setattr(steam.config, "get_steam_credentials", lambda: (None, None))


def test_session_path_returns_games_and_carriers(no_creds):
    page = FakePage({
        steam.TOKEN_CONFIG_URL: [FakeResponse(TOKEN_PAYLOAD)],
        steam.OWNED_GAMES_URL: [FakeResponse(GAMES_PAYLOAD)],
        steam.USERDATA_URL: [FakeResponse(USERDATA_PAYLOAD)],
    })
    got = steam.collect(page, captured=[])
    titles = [g.title for g in got if g.kind == "game"]
    carriers = [g.external_id for g in got if g.kind == "addon"]
    assert titles == ["Portal 2"] and carriers == ["620", "730"]
    owned_call = next(c for c in page.calls if c[0].startswith(steam.OWNED_GAMES_URL))
    assert owned_call[1]["access_token"] == TOKEN_PAYLOAD["data"]["webapi_token"]
    assert owned_call[1]["steamid"] == "76561198012345678"
    assert "key" not in owned_call[1]


def test_no_creds_no_session_raises_login_message(no_creds):
    page = FakePage({steam.TOKEN_CONFIG_URL: [FakeResponse(status=401)]})
    with pytest.raises(RuntimeError, match="Log into Steam in the browser window"):
        steam.collect(page, captured=[])


def test_token_present_but_unparseable_sub_raises(no_creds):
    page = FakePage({steam.TOKEN_CONFIG_URL: [FakeResponse({"data": {"webapi_token": "junk"}})]})
    with pytest.raises(RuntimeError, match="Log into Steam"):
        steam.collect(page, captured=[])


def test_session_owned_games_http_error_raises_with_status(no_creds):
    page = FakePage({
        steam.TOKEN_CONFIG_URL: [FakeResponse(TOKEN_PAYLOAD)],
        steam.OWNED_GAMES_URL: [FakeResponse(status=429)],
    })
    with pytest.raises(RuntimeError, match="429"):
        steam.collect(page, captured=[])


def test_userdata_empty_then_retry_with_cache_buster(no_creds):
    page = FakePage({
        steam.TOKEN_CONFIG_URL: [FakeResponse(TOKEN_PAYLOAD)],
        steam.OWNED_GAMES_URL: [FakeResponse(GAMES_PAYLOAD)],
        steam.USERDATA_URL: [FakeResponse({"rgOwnedApps": []}), FakeResponse(USERDATA_PAYLOAD)],
    })
    got = steam.collect(page, captured=[])
    assert [g.external_id for g in got if g.kind == "addon"] == ["620", "730"]
    userdata_calls = [u for u, _ in page.calls if u.startswith(steam.USERDATA_URL)]
    assert len(userdata_calls) == 2 and "?v=" in userdata_calls[1]
    assert page.waits  # slept between attempts


def test_userdata_empty_twice_is_nonfatal(no_creds):
    page = FakePage({
        steam.TOKEN_CONFIG_URL: [FakeResponse(TOKEN_PAYLOAD)],
        steam.OWNED_GAMES_URL: [FakeResponse(GAMES_PAYLOAD)],
        steam.USERDATA_URL: [FakeResponse({"rgOwnedApps": []}), FakeResponse({"rgOwnedApps": []})],
    })
    got = steam.collect(page, captured=[])
    assert [g.title for g in got] == ["Portal 2"]


def test_config_creds_path_short_circuits_session(no_creds, monkeypatch):
    monkeypatch.setattr(steam.config, "get_steam_credentials", lambda: ("KEY", "7656"))

    def fake_get(url, params=None, timeout=None):
        assert params["key"] == "KEY" and params["steamid"] == "7656"
        return FakeResponse(GAMES_PAYLOAD)

    monkeypatch.setattr(FakeResponse, "raise_for_status", lambda self: None, raising=False)
    monkeypatch.setattr(steam.requests, "get", fake_get)
    page = FakePage({steam.USERDATA_URL: [FakeResponse(USERDATA_PAYLOAD)]})
    got = steam.collect(page, captured=[])
    assert [g.title for g in got if g.kind == "game"] == ["Portal 2"]
    assert not any(u.startswith(steam.TOKEN_CONFIG_URL) for u, _ in page.calls)


def test_progress_reports_base_game_count_only(no_creds):
    page = FakePage({
        steam.TOKEN_CONFIG_URL: [FakeResponse(TOKEN_PAYLOAD)],
        steam.OWNED_GAMES_URL: [FakeResponse(GAMES_PAYLOAD)],
        steam.USERDATA_URL: [FakeResponse(USERDATA_PAYLOAD)],
    })
    seen = []
    steam.collect(page, captured=[], progress=seen.append)
    assert seen == [1]
```

Update `tests/test_steam_collect_guard.py` (guard message changed — no creds now falls through to the session tier):

```python
"""steam.collect fails honestly when there are no config creds AND no session token."""
import pytest

from scrapers import steam


class _Resp:
    ok, status = False, 401


class _Page:
    request = None

    def __init__(self):
        self.request = self

    def get(self, url, params=None):
        return _Resp()


def test_collect_raises_without_credentials_or_session(monkeypatch) -> None:
    monkeypatch.setattr(steam.config, "get_steam_credentials", lambda: (None, None))
    with pytest.raises(RuntimeError, match="Log into Steam in the browser window"):
        steam.collect(page=_Page(), captured=[])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_steam_session_collect.py tests/test_steam_collect_guard.py -q`
Expected: session tests FAIL (old collect raises "Steam Web API key" RuntimeError); updated guard test FAILS (message mismatch).

- [ ] **Step 3: Implement the ladder**

In `scrapers/steam.py`: add `import time` to the stdlib imports. Add constants below `TOKEN_CONFIG_URL`:

```python
LOGIN_REQUIRED_MSG = "Log into Steam in the browser window first, then press Continue"
_USERDATA_RETRY_WAIT_MS = 2000
```

Replace the whole `collect` function (and its old inline userdata block) with:

```python
def _games_via_session(page) -> list[ScrapedGame]:
    """Tier 2: mint a webapi_token from the logged-in store session, then call the
    official GetOwnedGames with access_token= (no Web API key involved)."""
    resp = page.request.get(TOKEN_CONFIG_URL)
    token = ""
    if resp.ok:
        try:
            token = parse_webapi_token(resp.json())
        except ValueError as exc:
            logger.warning("steam: token config not JSON (%s)", exc)
    steam_id = steamid_from_token(token)
    if not (token and steam_id):
        raise RuntimeError(LOGIN_REQUIRED_MSG)
    resp = page.request.get(OWNED_GAMES_URL, params={
        "access_token": token, "steamid": steam_id, "include_appinfo": "true",
        "include_played_free_games": "true", "format": "json"})
    if not resp.ok:
        raise RuntimeError(f"{LOGIN_REQUIRED_MSG} (GetOwnedGames HTTP {resp.status})")
    return parse_owned_games(resp.json())


def _fetch_userdata(page) -> list[ScrapedGame]:
    """Owned-appid carriers, fetched AFTER the games step; one cache-busted retry
    when empty (userdata is flaky right after login). Best-effort, never fatal."""
    for attempt, url in enumerate((USERDATA_URL,
                                   f"{USERDATA_URL}?v={time.monotonic_ns()}")):
        if attempt:
            page.wait_for_timeout(_USERDATA_RETRY_WAIT_MS)
        resp = page.request.get(url)
        if not resp.ok:
            logger.warning("steam: userdata fetch failed (%s)", resp.status)
            continue
        owned = parse_userdata(resp.json())
        if owned:
            logger.info("steam: %d owned appids (games+DLC) via userdata", len(owned))
            return owned
    logger.warning("steam: userdata empty after retry; owned DLC will be empty")
    return []


def collect(page, captured: list | None = None,
            progress: Callable[[int], None] | None = None) -> list[ScrapedGame]:
    """Owned Steam games + owned-appid carriers, via a three-tier ladder:

    1. Config creds (Web API key + SteamID64) -> keyed GetOwnedGames (CLI back-compat).
    2. Logged-in session mints its own webapi_token -> GetOwnedGames (zero config).
    3. Neither -> honest RuntimeError telling the user to log into Steam first.
    """
    api_key, steam_id = config.get_steam_credentials()
    if api_key and steam_id:
        params = {"key": api_key, "steamid": steam_id, "include_appinfo": "true",
                  "include_played_free_games": "true", "format": "json"}
        resp = requests.get(OWNED_GAMES_URL, params=params, timeout=30)
        resp.raise_for_status()
        games = parse_owned_games(resp.json())
        logger.info("steam: %d owned games via GetOwnedGames (config creds)", len(games))
    else:
        games = _games_via_session(page)
        logger.info("steam: %d owned games via session token", len(games))

    owned = _fetch_userdata(page)
    if progress:
        progress(len(games))  # owned base games (accurate "N games"; carriers are DLC)
    return games + owned
```

Also update the module docstring (lines 1-8) to describe the ladder:

```python
"""Steam library scraper (3-tier: config Web API creds -> session-minted token -> error).

Owned games come from the official IPlayerService/GetOwnedGames — keyed when the
user configured a Web API key + SteamID64, otherwise via a webapi_token the
logged-in store session mints for itself (pointssummary/ajaxgetasyncconfig; the
JWT's `sub` claim is the SteamID64). Owned-DLC ownership comes from the session's
dynamicstore/userdata `rgOwnedApps`, fetched after the games step with one
cache-busted retry (flaky right after login), carried as id-only kind="addon"
rows. The DLC catalogue itself is fetched later by steam_dlc (keyless appdetails).
Pure parsers are unit-tested; `collect` wiring is tested with a fake page.
"""
```

- [ ] **Step 4: Run the Steam tests, then the full suite**

Run: `uv run python -m pytest tests/test_steam_session_collect.py tests/test_steam_collect_guard.py tests/test_steam_session_token.py tests/test_parse_steam.py -q`
Expected: all pass

Run: `uv run python -m pytest -n auto -q`
Expected: full suite green (1042+ tests, plus the new ones)

- [ ] **Step 5: Lint and commit**

```powershell
uv run ruff check .
git add scrapers/steam.py tests/test_steam_session_collect.py tests/test_steam_collect_guard.py
git commit -m "feat(steam): session-token scrape — 3-tier collect ladder + cache-busted userdata retry"
```

---

### Task 3: Live headless smoke of the session path

**Files:**
- Create: `<scratchpad>/steam_session_smoke.py` (throwaway probe, NOT committed)

**Interfaces:**
- Consumes: `scrapers.base.capturing_browser(headless=True, profile_dir=Path)`, `scrapers.steam.collect`.

- [ ] **Step 1: Confirm the desktop app is not running** (persistent context locks the profile)

Run: `Get-Process | Where-Object { $_.Name -match "BacklogQuest" }`
Expected: no output. If it's running, stop and ask the owner — never kill their app silently.

- [ ] **Step 2: Write the probe script in the scratchpad**

```python
"""Headless probe: session-token Steam scrape against the logged-in pw-profile.
Read-only network calls; touches no DB. Run from the repo root."""
import logging
import os
from pathlib import Path

logging.basicConfig(level=logging.INFO)

from scrapers import steam
from scrapers.base import capturing_browser

profile = Path(os.environ["APPDATA"]) / "BacklogQuest" / "pw-profile"
assert profile.exists(), f"profile missing: {profile}"

with capturing_browser(headless=True, profile_dir=profile) as (page, captured):
    page.goto(steam.VENDOR_URL)
    rows = steam.collect(page, captured)
games = [r for r in rows if r.kind == "game"]
carriers = [r for r in rows if r.kind == "addon"]
print(f"SMOKE OK: {len(games)} games, {len(carriers)} owned-appid carriers")
print("sample:", [g.title for g in games[:5]])
```

Note: the probe must run WITHOUT Steam config creds so tier 2 is exercised. Check `config.get_steam_credentials()` first; if the local config has creds, monkeypatch them out in the probe (`steam.config.get_steam_credentials = lambda: (None, None)`) rather than editing any config file.

- [ ] **Step 3: Run it**

Run: `uv run python <scratchpad>/steam_session_smoke.py`
Expected: `SMOKE OK: ~1038 games, ~1956 owned-appid carriers` and real titles in the sample. If the token endpoint 401s, the profile's Steam login expired — report to owner instead of retrying.

---

### Task 4: Version bump to 0.1.4, build, publish

**Files:**
- Modify: `desktop/versioncheck.py:12` (`APP_VERSION = "0.1.3"` → `"0.1.4"`)

- [ ] **Step 1: Bump the version**

```python
APP_VERSION = "0.1.4"
```

- [ ] **Step 2: Full suite + lint one last time**

Run: `uv run python -m pytest -n auto -q` then `uv run ruff check .`
Expected: green / clean

- [ ] **Step 3: Commit and push**

```powershell
git add desktop/versioncheck.py
git commit -m "chore(desktop): v0.1.4 — Steam session-token scrape (+ text-select fix from 929d97a rides along)"
git push
```

- [ ] **Step 4: Build + publish**

```powershell
$env:PATH = "$env:LOCALAPPDATA\Programs\Inno Setup 6;$env:PATH"
./release_scraper.ps1
```

Expected: PyInstaller + Compress-Archive + iscc succeed; `Published v0.1.4 to the droplet.` (the script's ssh mkdir/scp publish is authorized; any OTHER droplet command — git pull, service restart — needs owner approval first).

- [ ] **Step 5: Verify the published version**

Run: `Invoke-RestMethod https://backlogquest.xyz/api/scraper/version`
Expected: `{"version": "0.1.4"}` — the version endpoint serves the scp'd `version.txt`; if it still says 0.1.3, report to the owner (do not ssh-restart anything without approval).

---

## Self-Review (done at plan time)

- **Spec coverage:** helpers ✓ (Task 1), 3-tier ladder ✓ (Task 2), userdata after-games + cache-busted retry ✓ (Task 2), error-handling table ✓ (tests: token 401 → tier 3, GetOwnedGames non-200 → status in message, userdata non-fatal), live smoke ✓ (Task 3), out-of-scope respected (no UI, no server-side Steam).
- **Placeholder scan:** none — every step has full code/commands.
- **Type consistency:** `parse_webapi_token(payload: dict) -> str` and `steamid_from_token(token: str) -> str` used identically in Tasks 1/2; `FakePage` shape matches `page.request.get(url, params=...)` + `wait_for_timeout(ms)` used by the implementation.
