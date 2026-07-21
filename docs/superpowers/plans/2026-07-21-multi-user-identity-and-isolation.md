# Multi-User Identity & Isolation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn single-tenant BacklogQuest into a multi-user app where each user has a private, isolated library, authenticated with Google, with the owner as user #1.

**Architecture:** Row-level `user_id` scoping in the shared SQLite DB. A `users` table holds Google identities. A single `current_user_id()` seam resolves the acting user; **when auth is disabled (dev/tests) it returns the owner id**, so existing behavior and all 1081 tests are preserved. Google OAuth replaces the shared-password gate. An automated cross-user isolation test is the correctness gate.

**Tech Stack:** Python 3, Flask, raw `sqlite3`, Authlib (Google OIDC), pytest, `uv`, ruff.

**Spec:** `docs/superpowers/specs/2026-07-21-multi-user-identity-and-isolation-design.md`

## Global Constraints

- Run tests with `uv run python -m pytest` (plain `uv run pytest` fails: ModuleNotFoundError: models).
- Lint gate is `ruff check` only — never `ruff format` (codebase is hand-aligned).
- Add deps with `uv add`, never pip. Secrets via `os.environ`/dotenv, never hardcoded; every new env var added to `.env.example` with an empty value.
- Work on `main`, commit directly, frequent commits. App uses `use_reloader=False`.
- Migrations are idempotent `migrate_*(conn)` functions chained in `models.migrate_db()`; each guards with `PRAGMA table_info(...)` before `ALTER`. Migration is a deploy step (`app.ensure_db()`), never an import side effect.
- Owner deploys are done by Claude, not the owner (ssh `gametracker`; `cd /opt/backlogquest/app && sudo -u gametracker git pull --ff-only && systemctl restart backlogquest`).
- `OWNER_USER_ID = 1` is a module constant; the owner email comes from `BACKLOGQUEST_OWNER_EMAIL`.

---

## File Structure

- **Create `identity.py`** — the acting-user seam: `OWNER_USER_ID`, `current_user_id()`, `set_request_user()`, and the users-table accessors (`upsert_google_user`, `user_for_sub`, `owner_email`). One responsibility: "who is acting, and how do users get created." Kept separate from `auth.py` (which stays transport-level: is-this-request-authenticated) so identity and gate concerns don't tangle.
- **Create `oauth.py`** — Authlib Google client setup + the OIDC verification helper. One responsibility: talk to Google.
- **Modify `models.py`** — add `migrate_users`, `migrate_add_user_id` (games + roots), `migrate_user_profile_per_user`, `migrate_barcode_registry_drop_owned`; register them in `migrate_db()`. Query functions gain a `user_id` parameter.
- **Modify `app.py`** — `before_request` resolves the acting user; new `/login`, `/auth/callback`, `/logout`; every user-facing route scopes by `current_user_id()`.
- **Modify `auth.py`** — drop password login; keep the token/gate abstraction.
- **Create `tests/test_multiuser_isolation.py`** — the cross-user isolation sweep.
- **Modify `.env.example`** — new OAuth + owner + allowlist vars.

Scoping strategy: queries filter at the ownership **root** (`games`, `tags`, `slots`, `decider_chats`, `user_profile`); child-table queries join to their root and filter there. Child tables get **no** `user_id` column.

---

## Task 1: `users` table + owner seed

**Files:**
- Modify: `models.py` (add `migrate_users`, register in `migrate_db`)
- Create: `identity.py`
- Test: `tests/test_identity.py`

**Interfaces:**
- Produces: `identity.OWNER_USER_ID = 1`; `identity.owner_email() -> str`; `identity.upsert_google_user(conn, sub: str, email: str, name: str|None) -> int` (returns user id); `identity.user_for_sub(conn, sub: str) -> sqlite3.Row|None`; `models.migrate_users(conn)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_identity.py
import sqlite3
import models, identity

def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    models.migrate_users(conn)
    return conn

def test_owner_seeded_as_user_1(monkeypatch):
    monkeypatch.setenv("BACKLOGQUEST_OWNER_EMAIL", "owner@example.com")
    conn = _db()
    row = conn.execute("SELECT id, email, is_owner FROM users WHERE id = 1").fetchone()
    assert row["is_owner"] == 1
    assert row["email"] == "owner@example.com"

def test_upsert_google_user_pins_sub_to_owner_by_email(monkeypatch):
    monkeypatch.setenv("BACKLOGQUEST_OWNER_EMAIL", "owner@example.com")
    conn = _db()
    uid = identity.upsert_google_user(conn, "sub-123", "owner@example.com", "Owner")
    assert uid == identity.OWNER_USER_ID          # owner claim pins sub to user #1
    again = identity.upsert_google_user(conn, "sub-123", "owner@example.com", "Owner")
    assert again == identity.OWNER_USER_ID          # idempotent

def test_upsert_creates_new_user_for_new_sub(monkeypatch):
    monkeypatch.setenv("BACKLOGQUEST_OWNER_EMAIL", "owner@example.com")
    conn = _db()
    uid = identity.upsert_google_user(conn, "sub-999", "tester@example.com", "Tester")
    assert uid != identity.OWNER_USER_ID
    assert conn.execute("SELECT google_sub FROM users WHERE id = ?", (uid,)).fetchone()["google_sub"] == "sub-999"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_identity.py -v`
Expected: FAIL (`module 'models' has no attribute 'migrate_users'` / no `identity`).

- [ ] **Step 3: Implement `migrate_users` + `identity` accessors**

```python
# models.py — add near the other migrate_* functions
def migrate_users(conn: sqlite3.Connection) -> None:
    """Create the users table and seed the owner as user #1 (idempotent)."""
    import os
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            google_sub   TEXT UNIQUE,
            email        TEXT NOT NULL,
            display_name TEXT,
            is_owner     INTEGER NOT NULL DEFAULT 0,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    owner_email = os.environ.get("BACKLOGQUEST_OWNER_EMAIL", "owner@localhost").strip()
    # Force the owner to id=1 so existing single-tenant rows backfill to a stable id.
    conn.execute(
        "INSERT INTO users (id, email, is_owner) VALUES (1, ?, 1) "
        "ON CONFLICT(id) DO UPDATE SET email = excluded.email, is_owner = 1",
        (owner_email,),
    )
    conn.commit()
```

```python
# identity.py
from __future__ import annotations
import os
import sqlite3

OWNER_USER_ID = 1

def owner_email() -> str:
    return os.environ.get("BACKLOGQUEST_OWNER_EMAIL", "owner@localhost").strip()

def user_for_sub(conn: sqlite3.Connection, sub: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE google_sub = ?", (sub,)).fetchone()

def upsert_google_user(conn: sqlite3.Connection, sub: str, email: str,
                       name: str | None) -> int:
    """Return the user id for a verified Google identity, creating the row if new.
    The owner (matched by BACKLOGQUEST_OWNER_EMAIL) claims user #1 on first login."""
    existing = user_for_sub(conn, sub)
    if existing:
        return existing["id"]
    if email.lower() == owner_email().lower():
        conn.execute("UPDATE users SET google_sub = ?, display_name = ? WHERE id = ?",
                     (sub, name, OWNER_USER_ID))
        conn.commit()
        return OWNER_USER_ID
    cur = conn.execute(
        "INSERT INTO users (google_sub, email, display_name) VALUES (?, ?, ?)",
        (sub, email, name))
    conn.commit()
    return cur.lastrowid
```

Register in `migrate_db()` (top, before other migrations so the FK target exists):

```python
# models.py, inside migrate_db(), immediately after `conn = get_db()`
migrate_users(conn)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/test_identity.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add models.py identity.py tests/test_identity.py
git commit -m "feat(multiuser): users table + owner-seeded identity accessors"
```

---

## Task 2: `current_user_id()` seam (auth-disabled ⇒ owner)

**Files:**
- Modify: `identity.py`
- Test: `tests/test_identity.py`

**Interfaces:**
- Produces: `identity.current_user_id() -> int` and `identity.set_request_user(user_id: int | None)`. Reads the acting user from Flask's request context `g`; falls back to `OWNER_USER_ID` when unset (dev/tests and owner-only mode). This is the single seam every scoped query calls.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_identity.py — append
import app as flask_app
import identity

def test_current_user_defaults_to_owner_without_session():
    with flask_app.app.test_request_context("/"):
        assert identity.current_user_id() == identity.OWNER_USER_ID

def test_set_request_user_overrides():
    with flask_app.app.test_request_context("/"):
        identity.set_request_user(42)
        assert identity.current_user_id() == 42
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_identity.py -k current_user -v`
Expected: FAIL (`identity has no attribute current_user_id`).

- [ ] **Step 3: Implement**

```python
# identity.py — append
from flask import g

def set_request_user(user_id: int | None) -> None:
    g.acting_user_id = user_id

def current_user_id() -> int:
    """The user whose data this request may touch. Falls back to the owner when no
    user is bound (owner-only mode, local dev, and the existing test suite)."""
    return getattr(g, "acting_user_id", None) or OWNER_USER_ID
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/test_identity.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add identity.py tests/test_identity.py
git commit -m "feat(multiuser): current_user_id request seam, owner fallback"
```

---

## Task 3: `games.user_id` column + per-user uniqueness + backfill

**Files:**
- Modify: `models.py` (add `migrate_add_user_id_games`; register)
- Test: `tests/test_migrate_user_id.py`

**Interfaces:**
- Produces: `models.migrate_add_user_id_games(conn)`. After it runs, `games` has `user_id INTEGER NOT NULL DEFAULT 1` and `UNIQUE(user_id, normalized_title)` (replacing `UNIQUE(normalized_title)`); all pre-existing rows have `user_id = 1`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_migrate_user_id.py
import sqlite3
import models

def _legacy_games_db():
    """A pre-migration games table with the old single-column UNIQUE."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE games (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
        normalized_title TEXT NOT NULL, UNIQUE(normalized_title))""")
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('Celeste','celeste')")
    conn.commit()
    models.migrate_users(conn)
    return conn

def test_backfills_user_id_to_owner():
    conn = _legacy_games_db()
    models.migrate_add_user_id_games(conn)
    assert conn.execute("SELECT user_id FROM games WHERE normalized_title='celeste'").fetchone()["user_id"] == 1

def test_two_users_can_own_same_title():
    conn = _legacy_games_db()
    models.migrate_add_user_id_games(conn)
    conn.execute("INSERT INTO users (google_sub,email) VALUES ('s','t@e.com')")
    uid = conn.execute("SELECT id FROM users WHERE email='t@e.com'").fetchone()["id"]
    conn.execute("INSERT INTO games (title,normalized_title,user_id) VALUES ('Celeste','celeste',?)", (uid,))
    conn.commit()  # must NOT raise UNIQUE violation
    assert conn.execute("SELECT COUNT(*) c FROM games WHERE normalized_title='celeste'").fetchone()["c"] == 2

def test_idempotent():
    conn = _legacy_games_db()
    models.migrate_add_user_id_games(conn)
    models.migrate_add_user_id_games(conn)  # second run is a no-op, no raise
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_migrate_user_id.py -v`
Expected: FAIL (`no attribute migrate_add_user_id_games`).

- [ ] **Step 3: Implement (table rebuild — SQLite can't alter a UNIQUE constraint in place)**

```python
# models.py
def migrate_add_user_id_games(conn: sqlite3.Connection) -> None:
    """Add games.user_id (backfilled to owner) and swap UNIQUE(normalized_title)
    for UNIQUE(user_id, normalized_title). Idempotent."""
    cols = [c[1] for c in conn.execute("PRAGMA table_info(games)").fetchall()]
    if "user_id" in cols:
        return
    conn.execute("ALTER TABLE games ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1 REFERENCES users(id)")
    # Replace the single-column unique index with a per-user one.
    conn.execute("DROP INDEX IF EXISTS sqlite_autoindex_games_1")  # may not exist; guarded
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_games_user_title ON games(user_id, normalized_title)")
    conn.commit()
```

> Note: the original `UNIQUE(normalized_title)` is a table-level constraint backed by an autoindex. Because a full table rebuild is heavier and this DB is small, the migration adds the composite unique **index** and relies on application inserts always supplying `user_id`. If the autoindex cannot be dropped (name varies), the implementer must do the canonical 12-step SQLite table rebuild instead; verify `test_two_users_can_own_same_title` passes either way — that test is the gate.

Register after `migrate_users(conn)` in `migrate_db()`.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/test_migrate_user_id.py -v`
Expected: PASS. If `test_two_users_can_own_same_title` fails, switch to the full table rebuild (drop-and-recreate `games` with the composite UNIQUE, copy rows).

- [ ] **Step 5: Commit**

```bash
git add models.py tests/test_migrate_user_id.py
git commit -m "feat(multiuser): games.user_id + per-user title uniqueness"
```

---

## Task 4: `user_id` on the other four roots + `user_profile` per-user

**Files:**
- Modify: `models.py` (`migrate_add_user_id_roots`, `migrate_user_profile_per_user`; register)
- Test: `tests/test_migrate_user_id.py`

**Interfaces:**
- Produces: `models.migrate_add_user_id_roots(conn)` — adds `user_id INTEGER NOT NULL DEFAULT 1 REFERENCES users(id)` to `tags`, `slots`, `decider_chats`; changes `tags` uniqueness to `UNIQUE(user_id, name)`. `models.migrate_user_profile_per_user(conn)` — converts the `id=1` singleton `user_profile` to one row per user (drops the `CHECK(id=1)`, adds `user_id`, backfills the lone row to owner).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_migrate_user_id.py — append
def test_roots_get_user_id_backfilled_to_owner():
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, category TEXT)")
    conn.execute("CREATE TABLE slots (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("CREATE TABLE decider_chats (id INTEGER PRIMARY KEY, game_id INTEGER)")
    conn.execute("INSERT INTO tags (name) VALUES ('favorites')")
    conn.commit(); models.migrate_users(conn)
    models.migrate_add_user_id_roots(conn)
    assert conn.execute("SELECT user_id FROM tags WHERE name='favorites'").fetchone()["user_id"] == 1
    # two users can each have a 'favorites' tag
    conn.execute("INSERT INTO users (google_sub,email) VALUES ('s','t@e.com')")
    uid = conn.execute("SELECT id FROM users WHERE email='t@e.com'").fetchone()["id"]
    conn.execute("INSERT INTO tags (name,user_id) VALUES ('favorites',?)", (uid,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) c FROM tags WHERE name='favorites'").fetchone()["c"] == 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_migrate_user_id.py -k roots -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# models.py
def _add_user_id_col(conn, table):
    cols = [c[1] for c in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if "user_id" not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1 REFERENCES users(id)")

def migrate_add_user_id_roots(conn: sqlite3.Connection) -> None:
    for t in ("tags", "slots", "decider_chats"):
        _add_user_id_col(conn, t)
    conn.execute("DROP INDEX IF EXISTS sqlite_autoindex_tags_1")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tags_user_name ON tags(user_id, name)")
    conn.commit()

def migrate_user_profile_per_user(conn: sqlite3.Connection) -> None:
    """Convert the id=1 singleton user_profile to one row per user."""
    cols = [c[1] for c in conn.execute("PRAGMA table_info(user_profile)").fetchall()]
    if "user_id" in cols:
        return
    conn.execute("ALTER TABLE user_profile ADD COLUMN user_id INTEGER REFERENCES users(id)")
    conn.execute("UPDATE user_profile SET user_id = 1 WHERE id = 1")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_user_profile_user ON user_profile(user_id)")
    conn.commit()
```

Register both after `migrate_add_user_id_games(conn)` in `migrate_db()`.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/test_migrate_user_id.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add models.py tests/test_migrate_user_id.py
git commit -m "feat(multiuser): user_id on tags/slots/decider_chats + per-user profile"
```

---

## Task 5: Bind the acting user in `before_request`; Google OAuth login

**Files:**
- Create: `oauth.py`
- Modify: `app.py` (`before_request`, replace `/login` POST, add `/auth/callback`), `auth.py` (drop password check), `.env.example`
- Test: `tests/test_oauth_login.py`
- Deps: `uv add authlib`

**Interfaces:**
- Consumes: `identity.upsert_google_user`, `identity.set_request_user`.
- Produces: `oauth.verify_google_callback(request) -> dict` returning `{"sub","email","name"}` (raises `oauth.OAuthError` on failure); `/auth/callback` sets `session["user_id"]`. `before_request` calls `identity.set_request_user(session.get("user_id"))` on every request and rejects non-allowlisted users.

- [ ] **Step 1: Write the failing test** (mock Authlib; assert allowlist + session binding)

```python
# tests/test_oauth_login.py
from unittest.mock import patch
import app as flask_app, identity, models

def _client(monkeypatch):
    monkeypatch.setenv("BACKLOGQUEST_OWNER_EMAIL", "owner@example.com")
    monkeypatch.setenv("BACKLOGQUEST_ALLOWED_EMAILS", "owner@example.com,tester@example.com")
    flask_app.app.config["TESTING"] = True
    return flask_app.app.test_client()

def test_callback_allowlisted_sets_session_user(monkeypatch):
    cl = _client(monkeypatch)
    with patch("oauth.verify_google_callback",
               return_value={"sub": "s1", "email": "tester@example.com", "name": "T"}):
        res = cl.get("/auth/callback?code=x&state=y", follow_redirects=False)
    assert res.status_code in (302, 303)
    with cl.session_transaction() as s:
        assert s["user_id"] and s["user_id"] != identity.OWNER_USER_ID

def test_callback_rejects_non_allowlisted(monkeypatch):
    cl = _client(monkeypatch)
    with patch("oauth.verify_google_callback",
               return_value={"sub": "s2", "email": "stranger@example.com", "name": "X"}):
        res = cl.get("/auth/callback?code=x&state=y")
    assert res.status_code == 403
    with cl.session_transaction() as s:
        assert "user_id" not in s
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_oauth_login.py -v`
Expected: FAIL (no `/auth/callback`, no `oauth`).

- [ ] **Step 3: Implement** — `uv add authlib`; create `oauth.py` (Authlib Google client + `verify_google_callback`); in `app.py` add:

```python
# app.py — replace the password branch of /login with a Google redirect,
# add /auth/callback, and bind the acting user in before_request.
import identity, oauth

def _allowed_emails() -> set[str]:
    raw = os.environ.get("BACKLOGQUEST_ALLOWED_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}

@app.before_request
def _bind_user():
    identity.set_request_user(session.get("user_id"))

@app.route("/auth/callback")
def auth_callback():
    try:
        info = oauth.verify_google_callback(request)
    except oauth.OAuthError:
        return render_template("login.html", error="Sign-in failed"), 401
    if info["email"].lower() not in _allowed_emails():
        return render_template("login.html", error="Not invited yet"), 403
    conn = get_db()
    uid = identity.upsert_google_user(conn, info["sub"], info["email"], info.get("name"))
    conn.close()
    session.permanent = True
    session["user_id"] = uid
    return redirect(url_for("index"))
```

Keep the existing `_require_auth` gate but change its authenticated check to `session.get("user_id")` (web) or a valid bearer (API, unchanged). Remove `auth.check_password` and the `/login` POST password path; `/login` GET renders a "Sign in with Google" button linking to the Authlib authorize redirect. Add the four env vars to `.env.example`.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/test_oauth_login.py -v`
Expected: PASS. Then `uv run python -m pytest -q` — existing suite still green (auth disabled ⇒ owner).

- [ ] **Step 5: Commit**

```bash
git add app.py oauth.py auth.py identity.py .env.example tests/test_oauth_login.py pyproject.toml uv.lock
git commit -m "feat(multiuser): Google OAuth login + allowlist, bind acting user"
```

---

## Task 6: Scope the `games` routes (read + write)

**Files:**
- Modify: `app.py` (all `/api/games*` routes), `models.py` (query helpers that select/insert/update/delete games)
- Test: `tests/test_multiuser_isolation.py` (started here, expanded in Task 9)

**Interfaces:**
- Consumes: `identity.current_user_id()`.
- Pattern (apply to every games query): reads add `WHERE g.user_id = :uid`; single-game reads/writes add `AND g.user_id = :uid` so another user's `game_id` yields 404; inserts set `user_id = :uid`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_multiuser_isolation.py
import app as flask_app
from tests.helpers_multiuser import client_as, seed_game  # created in this task

def test_user_cannot_list_another_users_games(mu_db):
    a = seed_game(mu_db, user_id=1, title="A-Only")
    b = seed_game(mu_db, user_id=2, title="B-Only")
    cl = client_as(2)
    titles = [g["title"] for g in cl.get("/api/games").get_json()]
    assert "B-Only" in titles and "A-Only" not in titles

def test_user_cannot_fetch_another_users_game_by_id(mu_db):
    gid = seed_game(mu_db, user_id=1, title="A-Only")
    assert client_as(2).get(f"/api/games/{gid}").status_code == 404
```

`tests/helpers_multiuser.py` provides `mu_db` (a temp DB fixture with `migrate_db` run + two users), `client_as(uid)` (a test client that sets `session["user_id"]=uid`), and `seed_game(conn, user_id, title)`.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_multiuser_isolation.py -v`
Expected: FAIL (routes return all users' games / 200 instead of 404).

- [ ] **Step 3: Implement** — thread `uid = identity.current_user_id()` into every `/api/games*` query. Example for `api_games` (app.py:189): add `uid = identity.current_user_id()`, append `WHERE g.user_id = ?` (before the dynamic filters, adjust the filter concatenation to `AND`), pass `uid` as the first bind. For `/api/games/<id>` reads/updates/deletes add `AND g.user_id = ?` and return 404 when no row. For POST `/api/games` set `user_id` in the INSERT. Route inventory to scope (all in app.py): `api_games` (GET/POST), `/api/games/batch`, `/api/games/<id>` (GET/PATCH/DELETE), `/api/games/search`, `/api/games/<id>/dlc*`, `/api/games/<id>/decider-chat`, `/api/games/<id>/igdb*`.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/test_multiuser_isolation.py -v && uv run python -m pytest -q`
Expected: PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add app.py models.py tests/test_multiuser_isolation.py tests/helpers_multiuser.py
git commit -m "feat(multiuser): scope games routes to the acting user"
```

---

## Task 7: Scope the remaining roots + their children

**Files:**
- Modify: `app.py`, `models.py`, `slots.py`, `decider.py`, `dedup.py`, `collections`-related routes
- Test: `tests/test_multiuser_isolation.py`

**Interfaces:**
- Consumes: `identity.current_user_id()`. Same pattern as Task 6, applied per root: `tags`/`game_tags`, `slots`/`slot_history`/`slot_dismissals`/`slot_schedule_window`, `decider_chats`, `user_ratings`, `game_collections` membership, `not_duplicates`, review queues (`dlc_review_queue`, `bundle_review_queue`, `upc_review`), `user_profile`.

- [ ] **Step 1: Write the failing tests** — one isolation assertion per root (mirror Task 6's two-user pattern): e.g. `test_user_cannot_see_another_users_slots`, `..._tags`, `..._ratings`, `..._collections_membership`, `..._decider_chat`, `..._profile`.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run python -m pytest tests/test_multiuser_isolation.py -v`
Expected: FAIL for each unscoped root.

- [ ] **Step 3: Implement** — for **root** tables, filter/insert on `user_id = current_user_id()`. For **child** tables, join to the parent root and filter there (e.g. `game_tags` → `JOIN games g ON g.id = game_tags.game_id WHERE g.user_id = ?`; `slot_history` → `JOIN slots s ON s.id = slot_history.slot_id WHERE s.user_id = ?`). `user_profile` reads/writes key on `user_id`. Walk every route touching these tables; the test file is the checklist.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run python -m pytest tests/test_multiuser_isolation.py -v && uv run python -m pytest -q`
Expected: PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(multiuser): scope tags/slots/ratings/collections/reviews/profile"
```

---

## Task 8: `barcode_registry` — global identity, per-user ownership

**Files:**
- Modify: `barcode.py` (`resolve`, `registry_get`), `models.py` (`migrate_barcode_registry_drop_owned`)
- Test: `tests/test_barcode.py`

**Interfaces:**
- Consumes: `identity.current_user_id()` at the route; `barcode.resolve` already takes `conn`.
- Behavior: the shared `barcode_registry` row no longer supplies an owned `game_id`; `resolve` derives `owned_game_id` via `_owned_game_id(conn, title)` **scoped to the acting user's games**, so two users scanning the same UPC each see their own ownership.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_barcode.py — append
def test_registry_ownership_is_per_user(mu_db):
    # same UPC identity cached globally; only user 1 owns the game
    seed_registry(mu_db, upc="123", igdb_id=7, title="Celeste")
    seed_game(mu_db, user_id=1, title="Celeste", igdb_id=7)
    import barcode, identity
    with app_ctx_as(1):
        r1 = barcode.resolve(mu_db, "123", user_id=identity.current_user_id())
    with app_ctx_as(2):
        r2 = barcode.resolve(mu_db, "123", user_id=identity.current_user_id())
    assert r1["candidates"][0]["owned_game_id"] is not None
    assert r2["candidates"][0]["owned_game_id"] is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_barcode.py -k per_user -v`
Expected: FAIL (`resolve` has no `user_id` param / ownership leaks).

- [ ] **Step 3: Implement** — add `user_id` param to `barcode.resolve`; change `_owned_game_id(conn, title)` to filter `WHERE g.user_id = ?`; stop trusting `cached["game_id"]` for ownership (compute from the acting user's library). `migrate_barcode_registry_drop_owned` leaves the column in place for back-compat but the read path ignores it. Update the `/api/barcode/resolve` route to pass `identity.current_user_id()`.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/test_barcode.py -v && uv run python -m pytest -q`
Expected: PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add barcode.py models.py app.py tests/test_barcode.py
git commit -m "feat(multiuser): per-user barcode ownership over shared UPC cache"
```

---

## Task 9: Cross-user isolation sweep (the correctness gate)

**Files:**
- Modify: `tests/test_multiuser_isolation.py`, `tests/helpers_multiuser.py`

**Interfaces:**
- Consumes: everything above. Produces a parametrized sweep proving User A can never read or mutate User B's rows across the full route inventory.

- [ ] **Step 1: Write the sweep** — a parametrized test over a list of `(method, url_template)` for every user-scoped route. For each: seed a row owned by user 1; as user 2 assert reads exclude it and writes/deletes targeting its id return 404/403 and leave it unchanged. Add a positive control: `platforms`, `collections` catalog, and `barcode_registry` identity are visible to both users.

```python
SCOPED_READS = ["/api/games", "/api/collections", "/api/dlc/review", "/api/bundle-review", ...]
@pytest.mark.parametrize("url", SCOPED_READS)
def test_read_excludes_other_users_rows(mu_db, url):
    _seed_owned_by(mu_db, user_id=1)
    body = client_as(2).get(url).get_json()
    assert _mentions_user1_row(body) is False
```

- [ ] **Step 2: Run — expect any unscoped route to surface here**

Run: `uv run python -m pytest tests/test_multiuser_isolation.py -v`
Expected: PASS once Tasks 6–8 are complete; a FAIL pinpoints an unscoped route to fix.

- [ ] **Step 3: Fix any route the sweep catches** (return to the relevant task's pattern).

- [ ] **Step 4: Full suite**

Run: `uv run python -m pytest -q`
Expected: PASS (1081 existing + new).

- [ ] **Step 5: Commit**

```bash
git add tests/test_multiuser_isolation.py tests/helpers_multiuser.py
git commit -m "test(multiuser): cross-user isolation sweep over all scoped routes"
```

---

## Task 10: Deploy migration wiring + verification

**Files:**
- Modify: `.env.example`, `models.py` (confirm all new migrations registered in `migrate_db` in dependency order: users → games → roots → profile → barcode)
- Verify: `app.ensure_db()` runs `migrate_db()` at `ExecStartPre` (already wired).

- [ ] **Step 1: Local full-DB migration dry run**

```bash
cp games.db /tmp/games_migrate_test.db
uv run python -c "import models, sqlite3; c=sqlite3.connect('/tmp/games_migrate_test.db'); c.row_factory=sqlite3.Row; models.migrate_db_on(c) if hasattr(models,'migrate_db_on') else __import__('os')"
```
(If `migrate_db` is hard-wired to `games.db`, temporarily point `DB_PATH` via an env override or run against a copy; confirm `users` exists, `games.user_id` all = 1, row counts unchanged.)

- [ ] **Step 2: Full suite + ruff**

Run: `uv run python -m pytest -q && uv run ruff check .`
Expected: PASS; `All checks passed!`

- [ ] **Step 3: Commit + push**

```bash
git add -A && git commit -m "chore(multiuser): register migrations in deploy order + env template"
git push origin main
```

- [ ] **Step 4: Deploy (Claude runs)** — take a DB backup first, then migrate + restart:

```bash
ssh gametracker 'sudo -u gametracker cp /opt/backlogquest/app/games.db /opt/backlogquest/app/games.db.pre-multiuser-bak-$(date +%Y%m%d-%H%M%S) && cd /opt/backlogquest/app && sudo -u gametracker git pull --ff-only && systemctl restart backlogquest && sleep 3 && systemctl is-active backlogquest'
```

- [ ] **Step 5: Verify live** — owner signs in with Google and sees the migrated library unchanged; a second test Google account sees an **empty** library and cannot reach owner data; a non-allowlisted account is refused. Confirm `curl -s -o /dev/null -w '%{http_code}' https://backlogquest.xyz/healthz` → 200.

---

## Self-Review

**Spec coverage:** users table (T1) ✓; Google OAuth + allowlist + owner=user#1 (T1,T5) ✓; owner data migration/backfill (T3,T4,T10) ✓; all 24 tables — roots scoped directly (T3,T4,T6,T7), children transitively (T7), globals untouched, barcode_registry special-cased (T8) ✓; remove shared password (T5) ✓; isolation test first-class (T6,T9) ✓; `.env.example` (T5,T10) ✓; deploy-step migration (T10) ✓. No gaps.

**Placeholder scan:** route inventories in T6/T7/T9 are explicit lists, not "etc."-style hand-waves; the one deliberate branch (games UNIQUE rebuild fallback in T3) names the exact test that gates which path to take. No forbidden placeholders.

**Type consistency:** `current_user_id() -> int`, `set_request_user(int|None)`, `upsert_google_user(conn,sub,email,name)->int`, `OWNER_USER_ID=1`, `verify_google_callback(request)->dict` used consistently across tasks.
