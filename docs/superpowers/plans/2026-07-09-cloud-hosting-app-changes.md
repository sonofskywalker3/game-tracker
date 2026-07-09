# Cloud Hosting — App-Side Changes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the Flask app-side pieces needed to run Game Tracker as a public single-user cloud service — a password/token auth gate, login/health routes, a cloud-mode switch that disables the in-app scraper, and an authenticated endpoint that ingests scrape JSON pushed from the home machine — plus a `--push` client on the scraper side.

**Architecture:** The app stays a single Flask app. A new `auth.py` holds pure, env-driven auth helpers. A `before_request` gate enforces auth **only when a password hash is configured in the environment** (so the existing test suite and un-configured local dev are unaffected; the cloud sets the env vars and the gate turns on). Scraping stays on the home PC; a new `POST /api/import/scrape` runs the existing import pipeline cloud-side with vendor store resolvers disabled (v1 fidelity — name-based DLC ownership), and `scrape_libraries.py --push` POSTs the scrape JSON to it.

**Tech Stack:** Python 3.11+, Flask, Werkzeug (password hashing — already a Flask dependency), `requests` (already used), `python-dotenv` (new, optional `.env` loading), gunicorn (added on the server in the infra plan; here we only add a `wsgi.py` entrypoint). Tests: pytest with the existing `client`/`temp_db` fixtures in `tests/conftest.py`.

## Global Constraints

- `uv` for all package/env management (`uv add`, never `pip install`). — from CLAUDE.md
- Run tests with `uv run python -m pytest` (plain `uv run pytest` fails: `ModuleNotFoundError: models`). — from memory
- **Test runner:** single-test steps run plain (fast). **Full-suite** steps use `-n auto` (pytest-xdist; ~35s vs ~134s serial). Never add `-n auto` to a single-test run — spawning workers costs ~8s for one test. — build-speed decision 2026-07-09
- Lint gate is `ruff check` only; never run `ruff format` (codebase is hand-aligned). — from memory
- Type hints on all function signatures (params and return). — from CLAUDE.md
- Use `logging`, not `print()`, for operational output. — from CLAUDE.md
- Named constants instead of magic strings in conditions; extract literals in conditions. — from CLAUDE.md
- Secrets via `os.environ` / `python-dotenv`; never hardcode. `.env` must be gitignored (it already is: `.gitignore` lines 4-6). — from CLAUDE.md
- Every `except` must log, re-raise, or return a typed error; use specific exceptions. — from CLAUDE.md
- Auth is enforced **only** when `GAMETRACKER_PASSWORD_HASH` is set; with it unset the app behaves exactly as today (keeps all 972 existing tests green). This is a hard requirement of every task below.
- Commit after every task. Work directly on `main` (per project convention).

---

## Environment variables (single source of truth for this plan)

| Var | Meaning | Consumed by |
|-----|---------|-------------|
| `GAMETRACKER_SESSION_SECRET` | Flask session cookie signing key | app startup (`app.secret_key`) |
| `GAMETRACKER_PASSWORD_HASH` | Werkzeug hash of the one app password; **presence toggles the auth gate on** | `auth.py` |
| `GAMETRACKER_API_TOKEN` | Bearer token the Android app stores + sends | `auth.py` |
| `GAMETRACKER_IMPORT_TOKEN` | Bearer token the home scrape-push uses | `auth.py`, `scrape_libraries.py` |
| `GAMETRACKER_CLOUD` | `"1"` → cloud mode: in-app scrape routes disabled | `auth.py` |

---

## File Structure

- **Create `auth.py`** — pure env-driven helpers: `auth_enabled()`, `check_password()`, `is_authenticated()`, `is_import_authorized()`, `cloud_mode()`, plus token/hash accessors. One responsibility: "who is allowed."
- **Create `templates/login.html`** — minimal login form.
- **Create `wsgi.py`** — gunicorn entrypoint (`ensure_db()` + `app`).
- **Create `config.example.json`** — committed template of `config.json` with empty values.
- **Create `.env.example`** — committed template listing the env var names above with empty values.
- **Modify `app.py`** — import `session, redirect, url_for`; set `app.secret_key`; add the `before_request` gate; add `/login`, `/logout`, `/healthz`; guard the four `/api/scrape/*` routes with `cloud_mode()`; add `POST /api/import/scrape`.
- **Modify `scrape_service.py`** — add `store_resolvers: bool = True` to `_run_pipeline`; add `import_pushed(source, games) -> dict`.
- **Modify `scrape_libraries.py`** — add `push_scrape(payload, url, token)`, a `--push URL` flag, and wire it into `run_scrape`.
- **Create tests** — `tests/test_auth.py`, `tests/test_auth_gate.py`, `tests/test_api_import_scrape.py`, `tests/test_scrape_push.py`; extend `tests/test_api_scrape.py` for cloud-mode.
- **Modify `pyproject.toml`** — `uv add python-dotenv`.

---

### Task 1: Auth helpers (`auth.py`)

**Files:**
- Create: `auth.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `os.environ`; `werkzeug.security.check_password_hash`.
- Produces:
  - `auth_enabled() -> bool` — True iff `GAMETRACKER_PASSWORD_HASH` is set/non-empty.
  - `check_password(candidate: str) -> bool` — True iff `auth_enabled()` and the candidate matches the hash.
  - `bearer_token(headers: Mapping[str, str]) -> str | None` — extracts the token from an `Authorization: Bearer <t>` header.
  - `is_authenticated(headers: Mapping[str, str], authed_session: bool) -> bool` — True if `authed_session` OR the bearer token equals `GAMETRACKER_API_TOKEN`.
  - `is_import_authorized(headers: Mapping[str, str]) -> bool` — True if the bearer token equals `GAMETRACKER_IMPORT_TOKEN` or `GAMETRACKER_API_TOKEN`.
  - `cloud_mode() -> bool` — True iff `GAMETRACKER_CLOUD == "1"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth.py
import os
import pytest
from werkzeug.security import generate_password_hash
import auth


@pytest.fixture
def env(monkeypatch):
    for var in ("GAMETRACKER_PASSWORD_HASH", "GAMETRACKER_API_TOKEN",
                "GAMETRACKER_IMPORT_TOKEN", "GAMETRACKER_CLOUD"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def test_auth_disabled_when_no_hash(env):
    assert auth.auth_enabled() is False
    assert auth.check_password("anything") is False


def test_check_password_matches_hash(env):
    env.setenv("GAMETRACKER_PASSWORD_HASH", generate_password_hash("hunter2"))
    assert auth.auth_enabled() is True
    assert auth.check_password("hunter2") is True
    assert auth.check_password("wrong") is False


def test_bearer_token_parsing(env):
    assert auth.bearer_token({"Authorization": "Bearer abc123"}) == "abc123"
    assert auth.bearer_token({"Authorization": "Basic abc123"}) is None
    assert auth.bearer_token({}) is None


def test_is_authenticated_session_or_api_token(env):
    env.setenv("GAMETRACKER_API_TOKEN", "tok")
    assert auth.is_authenticated({}, authed_session=True) is True
    assert auth.is_authenticated({"Authorization": "Bearer tok"}, authed_session=False) is True
    assert auth.is_authenticated({"Authorization": "Bearer nope"}, authed_session=False) is False
    assert auth.is_authenticated({}, authed_session=False) is False


def test_is_import_authorized_accepts_import_or_api_token(env):
    env.setenv("GAMETRACKER_IMPORT_TOKEN", "imp")
    env.setenv("GAMETRACKER_API_TOKEN", "api")
    assert auth.is_import_authorized({"Authorization": "Bearer imp"}) is True
    assert auth.is_import_authorized({"Authorization": "Bearer api"}) is True
    assert auth.is_import_authorized({"Authorization": "Bearer x"}) is False


def test_cloud_mode(env):
    assert auth.cloud_mode() is False
    env.setenv("GAMETRACKER_CLOUD", "1")
    assert auth.cloud_mode() is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'auth'`.

- [ ] **Step 3: Write the implementation**

```python
# auth.py
"""Env-driven auth helpers for the single-user cloud deployment.

Auth is enforced ONLY when GAMETRACKER_PASSWORD_HASH is set; with it unset the
app behaves exactly as it did before hosting (keeps the local dev + test suite
unauthenticated). Identity is deliberately abstract here so a future multi-user
"Sign in with Google" (OIDC) path replaces these helpers without touching the
gate or the routes that call them.
"""
from __future__ import annotations

import os
from collections.abc import Mapping

from werkzeug.security import check_password_hash

_BEARER_PREFIX = "Bearer "


def _env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def auth_enabled() -> bool:
    """True when a password hash is configured (turns the whole gate on)."""
    return _env("GAMETRACKER_PASSWORD_HASH") is not None


def check_password(candidate: str) -> bool:
    """True iff auth is enabled and `candidate` matches the configured hash."""
    hashed = _env("GAMETRACKER_PASSWORD_HASH")
    if hashed is None:
        return False
    return check_password_hash(hashed, candidate)


def bearer_token(headers: Mapping[str, str]) -> str | None:
    """Extract the token from an `Authorization: Bearer <token>` header."""
    value = headers.get("Authorization", "")
    if value.startswith(_BEARER_PREFIX):
        token = value[len(_BEARER_PREFIX):].strip()
        return token or None
    return None


def is_authenticated(headers: Mapping[str, str], authed_session: bool) -> bool:
    """True for a logged-in web session or a valid API bearer token."""
    if authed_session:
        return True
    api_token = _env("GAMETRACKER_API_TOKEN")
    return api_token is not None and bearer_token(headers) == api_token


def is_import_authorized(headers: Mapping[str, str]) -> bool:
    """True for the scrape-push import token (API token also accepted)."""
    token = bearer_token(headers)
    if token is None:
        return False
    return token in {t for t in (_env("GAMETRACKER_IMPORT_TOKEN"),
                                 _env("GAMETRACKER_API_TOKEN")) if t is not None}


def cloud_mode() -> bool:
    """True when running as the cloud deployment (disables the in-app scraper)."""
    return _env("GAMETRACKER_CLOUD") == "1"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_auth.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check auth.py tests/test_auth.py
git add auth.py tests/test_auth.py
git commit -m "feat(auth): env-driven auth helpers (gate off unless password hash set)"
```

---

### Task 2: `before_request` gate + login/logout/health routes

**Files:**
- Modify: `app.py` (imports near line 20; add `app.secret_key` after `app = Flask(__name__)` at line 35; add gate + routes)
- Create: `templates/login.html`
- Test: `tests/test_auth_gate.py`

**Interfaces:**
- Consumes: `auth.auth_enabled/check_password/is_authenticated/is_import_authorized` (Task 1).
- Produces:
  - `GET /healthz` → `{"status": "ok"}`, 200, always unauthenticated.
  - `GET /login` → renders the form (200). `POST /login` with form field `password` → on success sets `session["authed"]=True` and redirects to `/`; on failure re-renders with 401. When the request is JSON, `POST /login` returns `{"token": <GAMETRACKER_API_TOKEN>}` on success / `{"error": ...}` 401 on failure (this is how the Android app obtains its bearer token).
  - `POST /logout` → clears the session, redirects to `/login`.
  - A `before_request` handler enforcing auth when `auth.auth_enabled()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth_gate.py
import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def secure_env(monkeypatch):
    monkeypatch.setenv("GAMETRACKER_PASSWORD_HASH", generate_password_hash("pw"))
    monkeypatch.setenv("GAMETRACKER_API_TOKEN", "apitoken")
    monkeypatch.setenv("GAMETRACKER_SESSION_SECRET", "test-secret")
    import app as app_module
    app_module.app.secret_key = "test-secret"
    return monkeypatch


def test_healthz_always_open(client):
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.get_json()["status"] == "ok"


def test_gate_off_when_unconfigured(client):
    # No password hash in env -> app behaves as today (no redirect).
    assert client.get("/api/stats").status_code == 200


def test_api_blocked_without_auth(client, secure_env):
    res = client.get("/api/stats")
    assert res.status_code == 401


def test_html_redirects_to_login(client, secure_env):
    res = client.get("/")
    assert res.status_code == 302
    assert "/login" in res.headers["Location"]


def test_login_with_password_grants_session(client, secure_env):
    res = client.post("/login", data={"password": "pw"}, follow_redirects=False)
    assert res.status_code == 302
    assert client.get("/api/stats").status_code == 200  # session cookie carried


def test_login_json_returns_token(client, secure_env):
    res = client.post("/login", json={"password": "pw"})
    assert res.status_code == 200
    assert res.get_json()["token"] == "apitoken"


def test_login_bad_password_401(client, secure_env):
    res = client.post("/login", json={"password": "nope"})
    assert res.status_code == 401


def test_api_token_grants_access(client, secure_env):
    res = client.get("/api/stats", headers={"Authorization": "Bearer apitoken"})
    assert res.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_auth_gate.py -v`
Expected: FAIL (`/healthz` and `/login` return 404; gate not present).

- [ ] **Step 3: Implement — update imports and secret key**

In `app.py`, change the Flask import line (currently line 20):

```python
from flask import (Flask, Response, render_template, request, jsonify,
                   session, redirect, url_for)
```

Immediately after `app = Flask(__name__)` (line 35), add:

```python
app.secret_key = os.environ.get("GAMETRACKER_SESSION_SECRET", "dev-insecure-secret")

import auth
```

- [ ] **Step 4: Implement — the gate and routes**

Add near the top of the route definitions (e.g. just after the imports/secret block, before the template routes):

```python
# Paths reachable without authentication (login flow, health, static assets).
_PUBLIC_PATHS = frozenset({"/login", "/logout", "/healthz"})


@app.before_request
def _require_auth():
    """Gate every request when auth is configured. No-op when the password hash
    is unset (local dev / tests behave exactly as before hosting)."""
    if not auth.auth_enabled():
        return None
    path = request.path
    if path in _PUBLIC_PATHS or path.startswith("/static/"):
        return None
    if auth.is_authenticated(request.headers, bool(session.get("authed"))):
        return None
    if path == "/api/import/scrape" and auth.is_import_authorized(request.headers):
        return None
    if path.startswith("/api/"):
        return jsonify({"error": "authentication required"}), 401
    return redirect(url_for("login_page"))


@app.route("/healthz")
def healthz():
    """Unauthenticated liveness check for uptime monitors / the proxy."""
    return jsonify({"status": "ok"})


@app.route("/login", methods=["GET", "POST"])
def login_page():
    """Password login. Sets a session cookie for the web; returns the API bearer
    token as JSON for native clients (the Android app stores it)."""
    if request.method == "GET":
        return render_template("login.html")
    password = (request.json or {}).get("password") if request.is_json \
        else request.form.get("password", "")
    if not auth.check_password(password or ""):
        if request.is_json:
            return jsonify({"error": "invalid password"}), 401
        return render_template("login.html", error="Incorrect password"), 401
    session["authed"] = True
    if request.is_json:
        return jsonify({"token": os.environ.get("GAMETRACKER_API_TOKEN", "")})
    return redirect(url_for("index"))


@app.route("/logout", methods=["POST"])
def logout():
    """Clear the session and return to the login page."""
    session.clear()
    return redirect(url_for("login_page"))
```

- [ ] **Step 5: Implement — the login template**

```html
<!-- templates/login.html -->
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Game Tracker — Sign in</title>
  <style>
    body { font-family: system-ui, sans-serif; background: #14141f; color: #eee;
           display: flex; min-height: 100vh; align-items: center; justify-content: center; margin: 0; }
    form { background: #1e1e2e; padding: 32px; border-radius: 12px; width: 280px; }
    h1 { font-size: 18px; margin: 0 0 16px; }
    input { width: 100%; box-sizing: border-box; padding: 10px; margin-bottom: 12px;
            border-radius: 8px; border: 1px solid #444; background: #2a2a3c; color: #fff; }
    button { width: 100%; padding: 10px; border: none; border-radius: 8px;
             background: #7c3aed; color: #fff; font-weight: 600; cursor: pointer; }
    .err { color: #fca5a5; font-size: 13px; margin-bottom: 12px; }
  </style>
</head>
<body>
  <form method="post" action="/login">
    <h1>Game Tracker</h1>
    {% if error %}<div class="err">{{ error }}</div>{% endif %}
    <input type="password" name="password" placeholder="Password" autofocus>
    <button type="submit">Sign in</button>
  </form>
</body>
</html>
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_auth_gate.py -v`
Expected: PASS (8 tests).

- [ ] **Step 7: Verify the full suite is still green (the critical backward-compat check)**

Run: `uv run python -m pytest -q -n auto`
Expected: PASS — all pre-existing tests plus the new ones. If any pre-existing test now fails, the gate is leaking when `auth_enabled()` is False; fix before continuing.

- [ ] **Step 8: Lint + commit**

```bash
uv run ruff check app.py tests/test_auth_gate.py
git add app.py templates/login.html tests/test_auth_gate.py
git commit -m "feat(auth): before_request gate + login/logout/healthz routes"
```

---

### Task 3: Cloud-mode switch disables the in-app scraper

**Files:**
- Modify: `app.py` (the four `/api/scrape/*` routes at lines 2304-2333)
- Test: `tests/test_api_scrape.py` (extend)

**Interfaces:**
- Consumes: `auth.cloud_mode()` (Task 1).
- Produces: when `cloud_mode()` is True, `POST /api/scrape/start|continue|cancel` return **409** `{"error": "scraping runs on the home machine in cloud mode"}`; `GET /api/scrape/status` returns `{"phase": "disabled", ...}`. When False (default), behavior is unchanged.

- [ ] **Step 1: Write the failing test (append to `tests/test_api_scrape.py`)**

```python
def test_scrape_disabled_in_cloud_mode(client, monkeypatch):
    monkeypatch.setenv("GAMETRACKER_CLOUD", "1")
    assert client.post("/api/scrape/start", json={"vendor": "xbox"}).status_code == 409
    assert client.post("/api/scrape/continue").status_code == 409
    assert client.post("/api/scrape/cancel").status_code == 409
    assert client.get("/api/scrape/status").get_json()["phase"] == "disabled"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_api_scrape.py::test_scrape_disabled_in_cloud_mode -v`
Expected: FAIL (start returns 400/200, not 409).

- [ ] **Step 3: Implement — guard the routes**

Add a helper and guards. Insert above `api_scrape_start` (line 2304):

```python
# Message returned by the scrape routes when the app is the cloud deployment
# (scraping needs a real browser + vendor logins, which live on the home PC).
_SCRAPE_CLOUD_DISABLED = "scraping runs on the home machine in cloud mode"
```

At the start of `api_scrape_start`, `api_scrape_continue`, `api_scrape_cancel`, add:

```python
    if auth.cloud_mode():
        return jsonify({"error": _SCRAPE_CLOUD_DISABLED}), 409
```

Replace the body of `api_scrape_status` with:

```python
    if auth.cloud_mode():
        return jsonify({"phase": "disabled", "message": _SCRAPE_CLOUD_DISABLED})
    return jsonify(scrape_service.status())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_api_scrape.py -v`
Expected: PASS (new test + the 5 existing scrape tests still pass — they run with `GAMETRACKER_CLOUD` unset).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check app.py tests/test_api_scrape.py
git add app.py tests/test_api_scrape.py
git commit -m "feat(scrape): disable in-app scrape routes in cloud mode"
```

---

### Task 4: Cloud-side scrape import (`import_pushed`) + `POST /api/import/scrape`

**Files:**
- Modify: `scrape_service.py` (add `store_resolvers` param to `_run_pipeline` at lines 236-376; add `import_pushed`)
- Modify: `app.py` (add the route)
- Test: `tests/test_api_import_scrape.py`

**Interfaces:**
- Consumes: `scrape_service._run_pipeline` (existing), `models.get_db`, `auth.is_import_authorized` (gate, Task 2).
- Produces:
  - `scrape_service.import_pushed(source: str, games: list[dict]) -> dict` — opens a DB connection, runs the pipeline with `store_resolvers=False` (v1: name-based DLC ownership, no datacenter store-page lookups), returns the pipeline summary dict.
  - `POST /api/import/scrape` — body is the scrape file payload `{"source": str, "games": [ ... ]}`; returns `{"success": true, "summary": {...}}` 200, or `{"error": ...}` 400 for a malformed payload / unknown source.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_api_import_scrape.py
import json
import scrape_service


def _payload():
    return {"source": "xbox", "count": 1, "games": [
        {"title": "Halo Infinite", "platform": "Xbox", "source": "xbox",
         "external_id": "9ABC", "kind": "game"}]}


def test_import_pushed_runs_pipeline(client, monkeypatch):
    seen = {}
    def fake_pipeline(conn, vendor, games, *, store_resolvers=True, **kw):
        seen["vendor"] = vendor
        seen["store_resolvers"] = store_resolvers
        seen["n"] = len(games)
        return {"vendor": vendor, "new_games": len(games)}
    monkeypatch.setattr(scrape_service, "_run_pipeline", fake_pipeline)

    res = client.post("/api/import/scrape", json=_payload())
    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is True
    assert body["summary"]["new_games"] == 1
    assert seen == {"vendor": "xbox", "store_resolvers": False, "n": 1}


def test_import_scrape_rejects_unknown_source(client):
    res = client.post("/api/import/scrape", json={"source": "bogus", "games": []})
    assert res.status_code == 400


def test_import_scrape_rejects_missing_games(client):
    res = client.post("/api/import/scrape", json={"source": "xbox"})
    assert res.status_code == 400


def test_import_scrape_requires_import_token_when_secured(client, monkeypatch):
    from werkzeug.security import generate_password_hash
    monkeypatch.setenv("GAMETRACKER_PASSWORD_HASH", generate_password_hash("pw"))
    monkeypatch.setenv("GAMETRACKER_IMPORT_TOKEN", "imp")
    import app as app_module
    app_module.app.secret_key = "s"
    # No token -> blocked by the gate.
    assert client.post("/api/import/scrape", json=_payload()).status_code == 401
    # Correct import token -> allowed (pipeline stubbed to avoid real work).
    monkeypatch.setattr(scrape_service, "_run_pipeline",
                        lambda *a, **k: {"new_games": 0})
    ok = client.post("/api/import/scrape", json=_payload(),
                     headers={"Authorization": "Bearer imp"})
    assert ok.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_api_import_scrape.py -v`
Expected: FAIL (route 404; `import_pushed` missing).

- [ ] **Step 3: Implement — `store_resolvers` on `_run_pipeline`**

In `scrape_service.py`, change the signature (line 236-238) to add the keyword:

```python
def _run_pipeline(conn: sqlite3.Connection, vendor: str, games: list,
                  visited_pids: list[str] | None = None,
                  parent_map: dict | None = None,
                  *, store_resolvers: bool = True) -> dict:
```

In the non-steam branch, where the resolver is chosen (lines 282-288), force name-based ownership when `store_resolvers` is False:

```python
        if not store_resolvers:
            resolver = None            # v1 push path: name-based mark_ownership only
        elif parent_map is not None:
            def resolver(ids):
                return {i: parent_map.get(i) for i in ids}
        else:
            resolver = addon_parent.RESOLVERS.get(vendor)
```

(The existing `if resolver and addons:` block already falls through to `dlc_ownership.mark_ownership` when `resolver is None` — no other change needed.)

- [ ] **Step 4: Implement — `import_pushed`**

Add to `scrape_service.py` (after `_run_pipeline`):

```python
def import_pushed(source: str, games: list[dict]) -> dict:
    """Import a scrape payload pushed from the home machine (v1 fidelity).

    Runs the full pipeline (import + IGDB/Steam DLC enrichment + collections
    sync) but with vendor store resolvers DISABLED, so DLC ownership uses the
    name-based fallback and no store-page lookups run from the cloud IP. Returns
    the pipeline summary. Raises ValueError for an unknown source.
    """
    if source not in VENDORS:
        raise ValueError(f"unknown source: {source}")
    conn = models.get_db()
    try:
        return _run_pipeline(conn, source, games, store_resolvers=False)
    finally:
        conn.close()
```

- [ ] **Step 5: Implement — the route in `app.py`**

Add near the other scrape routes:

```python
@app.route('/api/import/scrape', methods=['POST'])
def api_import_scrape():
    """Ingest a scrape JSON payload pushed from the home machine and run the
    import pipeline cloud-side. Auth: the import token (handled by the gate)."""
    payload = request.get_json(silent=True) or {}
    source = payload.get('source', '')
    games = payload.get('games')
    if not isinstance(games, list):
        return jsonify({'error': 'payload must include a games list'}), 400
    try:
        summary = scrape_service.import_pushed(source, games)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify({'success': True, 'summary': summary})
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_api_import_scrape.py -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Full suite green**

Run: `uv run python -m pytest -q -n auto`
Expected: PASS (existing pipeline tests unaffected — `store_resolvers` defaults to True).

- [ ] **Step 8: Lint + commit**

```bash
uv run ruff check scrape_service.py app.py tests/test_api_import_scrape.py
git add scrape_service.py app.py tests/test_api_import_scrape.py
git commit -m "feat(import): authenticated /api/import/scrape ingest (v1 name-based ownership)"
```

---

### Task 5: Home-side scrape push (`scrape_libraries.py --push`)

**Files:**
- Modify: `scrape_libraries.py` (add `push_scrape`, `--push`, wire into `run_scrape`/`main`)
- Test: `tests/test_scrape_push.py`

**Interfaces:**
- Consumes: `requests` (already a dependency); `scrapers.base.read_scrape` for shape only (not required).
- Produces:
  - `push_scrape(payload: dict, base_url: str, token: str) -> dict` — POSTs `payload` to `{base_url}/api/import/scrape` with `Authorization: Bearer {token}`, raises for HTTP errors, returns the parsed JSON response.
  - `run_scrape(vendor, *, push_url=None, push_token=None)` — after `write_scrape`, if `push_url` is set, read the written file and push it.
  - CLI: `--push URL` (base URL of the cloud app); token read from `GAMETRACKER_IMPORT_TOKEN`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scrape_push.py
import json
import pytest
import scrape_libraries


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
    def raise_for_status(self):
        pass
    def json(self):
        return self._payload


def test_push_scrape_posts_to_import_endpoint(monkeypatch):
    captured = {}
    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, json=json, headers=headers)
        return _FakeResponse({"success": True, "summary": {"new_games": 2}})
    monkeypatch.setattr(scrape_libraries.requests, "post", fake_post)

    payload = {"source": "xbox", "games": [{"title": "A"}]}
    result = scrape_libraries.push_scrape(payload, "https://games.example.org/", "tok")

    assert captured["url"] == "https://games.example.org/api/import/scrape"
    assert captured["headers"]["Authorization"] == "Bearer tok"
    assert captured["json"] == payload
    assert result["summary"]["new_games"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_scrape_push.py -v`
Expected: FAIL (`push_scrape` not defined; possibly `scrape_libraries.requests` missing).

- [ ] **Step 3: Implement**

At the top of `scrape_libraries.py`, ensure `import os`, `import requests` are present (add if missing). Add:

```python
# Timeout for the cloud import POST (the server runs enrichment; give it room).
_PUSH_TIMEOUT_S = 600


def push_scrape(payload: dict, base_url: str, token: str) -> dict:
    """POST a scrape payload to the cloud app's import endpoint. Returns the
    parsed JSON response; raises requests.HTTPError on a non-2xx status."""
    url = base_url.rstrip("/") + "/api/import/scrape"
    resp = requests.post(url, json=payload,
                         headers={"Authorization": f"Bearer {token}"},
                         timeout=_PUSH_TIMEOUT_S)
    resp.raise_for_status()
    return resp.json()
```

Update `run_scrape` (line 76) to optionally push:

```python
def run_scrape(vendor: str, *, push_url: str | None = None,
               push_token: str | None = None) -> None:
    mod = SCRAPERS[vendor]
    if not hasattr(mod, "collect"):
        raise SystemExit(f"{vendor} scraper not implemented yet (no collect()).")
    with capturing_browser(headless=False) as (page, captured):
        page.goto(mod.VENDOR_URL)
        _wait_for_user(page, f"Log in if needed, open your {mod.SOURCE} library / full "
                             f"purchase history, then press Enter here... ")
        games = mod.collect(page, captured)
    out_path = write_scrape(vendor, games)
    logger.info("scraped %d %s games", len(games), vendor)
    if push_url:
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        result = push_scrape(payload, push_url, push_token or "")
        logger.info("pushed %s -> %s", vendor, result.get("summary"))
```

Update `main` (lines 89-97) to add the flag and thread it through:

```python
def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Scrape vendor libraries to normalized JSON")
    parser.add_argument("--vendor", required=True, choices=[*SCRAPERS, "all"])
    parser.add_argument("--recon", action="store_true", help="save raw library HTML to .recon/")
    parser.add_argument("--push", metavar="URL", default=None,
                        help="POST the scrape to a cloud app's /api/import/scrape "
                             "(token from GAMETRACKER_IMPORT_TOKEN)")
    args = parser.parse_args(argv)
    push_token = os.environ.get("GAMETRACKER_IMPORT_TOKEN", "")
    if args.push and not push_token:
        parser.error("--push requires GAMETRACKER_IMPORT_TOKEN in the environment")
    vendors = list(SCRAPERS) if args.vendor == "all" else [args.vendor]
    for vendor in vendors:
        if args.recon:
            run_recon(vendor)
        else:
            run_scrape(vendor, push_url=args.push, push_token=push_token)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_scrape_push.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check scrape_libraries.py tests/test_scrape_push.py
git add scrape_libraries.py tests/test_scrape_push.py
git commit -m "feat(scrape): --push option to POST scrape JSON to the cloud import endpoint"
```

---

### Task 6: Deployment scaffolding — dotenv, wsgi entrypoint, config/env templates, import-portability test

**Files:**
- Modify: `pyproject.toml` (`uv add python-dotenv`)
- Modify: `app.py` (optional `.env` load at startup)
- Create: `wsgi.py`
- Create: `config.example.json`
- Create: `.env.example`
- Test: `tests/test_importable.py`

**Interfaces:**
- Consumes: `python-dotenv` (`load_dotenv`).
- Produces:
  - `.env` (if present) is loaded on startup so env-var secrets work in local dev without exporting.
  - `wsgi.py` exposing `app` (for `gunicorn wsgi:app`) after `ensure_db()`.
  - `import app` succeeds on a machine without Playwright browsers installed (guards the cloud box, which installs the `playwright` package but not the browser binaries).

- [ ] **Step 1: Add the dependency**

Run: `uv add python-dotenv`
Expected: `pyproject.toml` gains `python-dotenv`; `uv.lock` updated.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_importable.py
import importlib


def test_app_imports_without_browser():
    """The cloud box installs the playwright package but not the browser
    binaries; importing the app (and its scrape imports) must not require them."""
    mod = importlib.import_module("app")
    assert hasattr(mod, "app")
    assert hasattr(mod, "ensure_db")


def test_wsgi_exposes_app():
    wsgi = importlib.import_module("wsgi")
    assert wsgi.app is importlib.import_module("app").app
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_importable.py -v`
Expected: FAIL on `test_wsgi_exposes_app` (`No module named 'wsgi'`). `test_app_imports_without_browser` should already pass (documents/guards the invariant).

- [ ] **Step 4: Implement — dotenv load in `app.py`**

Near the top of `app.py` (after `import os`, before other project imports), add:

```python
from dotenv import load_dotenv
load_dotenv()  # load .env if present (no-op in prod where env vars are set directly)
```

- [ ] **Step 5: Implement — `wsgi.py`**

```python
# wsgi.py
"""Gunicorn entrypoint: `gunicorn wsgi:app`. Ensures the DB schema is current
before the app serves, then exposes the Flask app object."""
from app import app, ensure_db

ensure_db()

if __name__ == "__main__":
    app.run()
```

- [ ] **Step 6: Implement — `config.example.json`**

```json
{
  "twitch_client_id": "",
  "twitch_client_secret": "",
  "steam_api_key": "",
  "steam_id": "",
  "anthropic_api_key": "",
  "decider_model": "claude-sonnet-4-6"
}
```

- [ ] **Step 7: Implement — `.env.example`**

```bash
# Auth gate turns ON only when GAMETRACKER_PASSWORD_HASH is set.
# Generate a hash:
#   uv run python -c "from werkzeug.security import generate_password_hash as h; print(h('YOUR_PASSWORD'))"
GAMETRACKER_PASSWORD_HASH=
# Flask session cookie signing key (any long random string).
GAMETRACKER_SESSION_SECRET=
# Bearer token the Android app stores after login (any long random string).
GAMETRACKER_API_TOKEN=
# Bearer token the home scrape-push uses (any long random string).
GAMETRACKER_IMPORT_TOKEN=
# Set to 1 on the cloud deployment to disable the in-app scraper.
GAMETRACKER_CLOUD=
```

- [ ] **Step 8: Run tests + full suite**

Run: `uv run python -m pytest tests/test_importable.py -v && uv run python -m pytest -q -n auto`
Expected: PASS (both new tests; full suite green).

- [ ] **Step 9: Verify `.env`/`config.json` stay gitignored (must NOT be staged)**

Run: `git status --porcelain | grep -E '(^|\s)(\.env|config\.json)$' || echo "clean: no secrets staged"`
Expected: `clean: no secrets staged`.

- [ ] **Step 10: Lint + commit**

```bash
uv run ruff check app.py wsgi.py tests/test_importable.py
git add pyproject.toml uv.lock app.py wsgi.py config.example.json .env.example tests/test_importable.py
git commit -m "chore(deploy): dotenv load, wsgi entrypoint, config/env templates, import guard"
```

---

## Definition of done (Plan 1)

- `uv run python -m pytest -q -n auto` fully green (972 existing + new tests).
- `uv run ruff check .` clean.
- With no auth env vars set, the app runs exactly as before (verified: existing suite passes).
- With `GAMETRACKER_PASSWORD_HASH` set: `/healthz` open; every other route requires a session cookie or bearer token; `/login` issues both.
- With `GAMETRACKER_CLOUD=1`: `/api/scrape/*` are disabled; `/api/import/scrape` accepts a pushed payload (import token) and runs the v1 pipeline.
- `gunicorn wsgi:app` is a valid entrypoint; `import app` needs no browser binaries.
- Follow-on plans (not in scope here): **Plan 2** — provision the droplet, DuckDNS, Caddy, systemd/gunicorn, copy `games.db`, backups; **Plan 3** — Android base URL + login.

## Self-review notes

- **Spec coverage:** auth gate ✓ (T1-2), login/health ✓ (T2), cloud-mode/scrape-disable ✓ (T3), import endpoint v1 ✓ (T4), scrape `--push` ✓ (T5), secrets via env + `python-dotenv` + `.env.example`/`config.example.json` ✓ (T6), gunicorn entrypoint ✓ (T6), portability import guard ✓ (T6). Infra provisioning, Android, and DB migration are explicitly deferred to Plans 2/3 per the spec's sequencing.
- **Backward compatibility:** the gate and cloud-mode are inert unless their env vars are set, which is why the existing suite stays green — asserted directly in T2 step 7 and T4 step 7.
- **Type/name consistency:** `import_pushed(source, games)`, `_run_pipeline(..., store_resolvers=...)`, `push_scrape(payload, base_url, token)`, `is_authenticated(headers, authed_session)`, `is_import_authorized(headers)`, `cloud_mode()`, `auth_enabled()` used identically in their defining and calling tasks.
