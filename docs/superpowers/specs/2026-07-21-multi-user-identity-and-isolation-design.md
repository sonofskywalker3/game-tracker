# Multi-User: Identity & Isolation (Spec 1 of 3) — Design

**Date:** 2026-07-21
**Status:** Approved (design); pending spec review
**Builds on:** [2026-07-09 Cloud Hosting](2026-07-09-cloud-hosting-and-mobile-access-design.md),
which deliberately made `auth.py` identity-pluggable and scoped multi-user as its
forward-looking "B" phase. This is that phase.

## Problem

BacklogQuest runs as a single-tenant app: one `games.db`, every query global, a
single shared-password gate. The owner wants to share it with **beta testers**,
each of whom needs their **own private, isolated backlog**. The owner intends this
as a **stepping stone to a public product**, so the isolation must be built the
*right* way — not a throwaway shortcut.

## Scope: this is Spec 1 of a three-spec decomposition

Multi-user is too large for one spec. It splits into:

1. **Identity & Isolation (this doc)** — the foundation everything depends on:
   `users` table, Google OAuth web login, owner → user #1 + data migration,
   `user_id` scoping across every user-owned table and query, and an automated
   cross-user isolation test. **Web app only.**
2. **Per-user scraper push** *(future)* — each user gets an import token bound to
   their `user_id`; the desktop-scraper download is provisioned per-user; a push
   lands in the right user's data.
3. **Android multi-user** *(future)* — the companion app's zero-touch token login
   becomes per-user Google sign-in.

Onboarding/allowlist is **not** a separate spec: it falls out of Google's
testing-mode allowlist plus first-login user provisioning, both inside this spec.

## Locked decisions

- **Isolation model:** row-level `user_id` scoping in the **shared SQLite DB**
  (not database-per-user). Rationale: for a public-consumer endgame, row-level
  scoping is the correct permanent model; the query-scoping work is
  engine-agnostic and carries forward unchanged to a future Postgres swap. SQLite
  stays for now; Postgres is a **later, contained infra change**, explicitly out
  of scope here (see [hosting design](2026-07-09-cloud-hosting-and-mobile-access-design.md)).
- **Auth:** Google OAuth (OIDC), single path for **everyone including the owner**.
  No password storage. Google's app "testing mode" allowlist is the invite gate,
  backed by our own email allowlist as defense-in-depth.
- **Owner:** becomes **user #1** via Google; the existing library migrates to the
  owner's `user_id`. The shared-password path is **removed** (single auth path;
  the owner dogfoods exactly what testers experience).
- **Isolation test is a first-class deliverable**, not an afterthought — it is the
  gate that makes "built right" verifiable.

## Data model

### New `users` table

```
users(
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  google_sub   TEXT UNIQUE NOT NULL,   -- stable Google account id (OIDC 'sub')
  email        TEXT NOT NULL,
  display_name TEXT,
  is_owner     INTEGER NOT NULL DEFAULT 0,
  created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

Identity keys on `google_sub` (immutable), not `email` (a Google account's email
can change).

### Table classification (all 24 existing tables)

The scoping principle: give a **direct `user_id`** only to *ownership roots* —
tables whose rows belong to a user but cannot reach a user through a foreign key.
Pure child tables scope **transitively** through their parent root, so they need
no column change (and cannot drift out of sync with their parent).

**Ownership roots — add a `user_id` column (FK → `users.id`, NOT NULL):**

| Table | Note |
|---|---|
| `games` | the library root; nearly everything scopes through it |
| `tags` | per-user labels; `UNIQUE(name)` → `UNIQUE(user_id, name)` |
| `slots` | per-user pick-slot configuration |
| `decider_chats` | per-user AI chats (conversational; not always tied to one game) |
| `user_profile` | per-user settings; drops the `CHECK(id = 1)` singleton, keyed by `user_id` |

**Child tables — scope transitively via their parent root (no column change):**

- via `games`: `game_platforms`, `game_external_ids`, `game_tags`, `user_ratings`,
  `dlc`, `not_duplicates`, `game_collections`, `upc_review`, `dlc_review_queue`,
  `bundle_review_queue`
- via `dlc` → `games`: `dlc_external_ids`
- via `slots`: `slot_history`, `slot_dismissals`, `slot_schedule_window`

**Global shared reference — stay global, no `user_id`:**

- `platforms` — the platform catalog (Switch, PS5, …), identical for everyone
- `collections` — the IGDB collection catalog (id/name/slug); only *membership*
  (`game_collections`) is user-owned
- `schema_flags` — migration state
- `upc_enrichment_state` — singleton background-worker state for the shared UPC cache

**Special case — `barcode_registry` (global identity, per-user owned-link):**
The UPC→IGDB *identity* (upc, igdb_id, title, cover, platform) is universal and
stays a **global shared cache** — a huge win, since every user benefits from every
other user's scans. But its stored `game_id` points at one user's owned row and
would leak across users. Resolution: the shared row **no longer stores/serves an
owned `game_id`**; the "do I own this?" link is derived **per-user at read time**
via the existing `_owned_game_id(conn, title)` path in `barcode.resolve` (already
the fallback today). So `barcode_registry.game_id` is dropped from the read path;
identity stays shared, ownership stays private.

## Authentication flow (Google OAuth via Authlib)

- `GET /login` → renders a "Sign in with Google" page (and initiates the redirect).
- `GET /auth/callback` → Authlib verifies the OIDC token, then **upserts** the user
  by `google_sub`. First-ever login for an allowlisted account creates the `users`
  row. Sets `session["user_id"]`.
- **Allowlist:** the account's email must be in a configured allowlist
  (env/`config.json`), enforced server-side even though Google testing mode already
  restricts it — defense-in-depth so a misconfigured Google app never opens signup.
- **The gate:** the existing `before_request` gate and `auth.py` abstraction already
  ask "authenticated, and as whom." It now resolves a real `user_id` from the
  session (or, later, a per-user API token). Unauthenticated → `/login`. `/healthz`,
  `/login`, `/auth/callback`, and static assets stay open.
- **Removed:** `BACKLOGQUEST_PASSWORD_HASH` login and the shared-password `/login`
  POST. Existing cookie hardening (HttpOnly/SameSite/Secure, session-secret
  fail-closed, `hmac.compare_digest` for tokens) is retained.
- **New secrets** (server env, per the project's secrets rule; added to
  `.env.example`): `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`,
  `BACKLOGQUEST_ALLOWED_EMAILS`, and the owner's email (`BACKLOGQUEST_OWNER_EMAIL`).

## Owner data migration (one explicit deploy step)

Per the project rule that **migration is a deploy step, not an import side effect**:

1. Take a fresh DB backup (`games.db.pre-multiuser-bak-<ts>`).
2. Create `users`; insert the owner as **user #1** (`email` from
   `BACKLOGQUEST_OWNER_EMAIL`, `is_owner=1`). `google_sub` is filled on the owner's
   first Google login (matched by email, then pinned to `sub`).
3. Add `user_id` columns to the four non-`games` roots and to `games`, **backfill
   every existing row to user #1**, *then* apply NOT NULL + FK constraints.
4. Convert `user_profile` from the `id=1` singleton to a per-user row for user #1.
5. Rewrite the `barcode_registry` read path to derive ownership per-user.

Runs from the systemd `ExecStartPre` migration hook already established for cloud
deploys. Idempotent and guarded by `schema_flags`.

## Isolation strategy & the isolation test

**Strategy.** Every user-facing query gains a `user_id` predicate at its ownership
root; child-table queries join to their root and filter there. `get_db()` /
request context carries the current `user_id`; helper query builders take it
explicitly rather than reading a global, so the scoping is visible at every call
site. The ~24-table enumeration above is the checklist the implementation walks.

**The test (first-class deliverable).** An automated suite that:

1. Creates **User A** and **User B**, each seeded with distinct games, tags, slots,
   ratings, collections membership, decider chats, and profile settings.
2. **Systematically sweeps every user-facing route** (a parametrized inventory of
   the read and write endpoints), asserting as User A that:
   - reads return **only** A's rows (never B's), and
   - writes/edits/deletes targeting B's row ids **fail** (404/403), never mutate.
3. Asserts shared-reference tables (`platforms`, `collections`,
   `barcode_registry` identity) **are** visible to both.

A new route added later without scoping should make this suite fail. This is the
"built right" gate.

## Error handling

- OAuth verification failure / state mismatch → clean `/login` error, no stack leak.
- Authenticated Google account **not** on the allowlist → rejected with a
  "not invited yet" page; no `users` row created.
- Missing OAuth env at startup with auth enabled → **fail closed** (mirrors the
  existing session-secret fail-closed check).
- A query reaching a route without a resolved `user_id` → treated as unauthenticated
  (never falls back to global/all-users data).

## Out of scope (later specs / phases)

- Per-user scraper import tokens and desktop-scraper provisioning (**Spec 2**).
- Android multi-user auth (**Spec 3**).
- Postgres migration; concurrency beyond SQLite + gunicorn `--workers 1`.
- Open public signup (stays invite-allowlisted), billing, a purchased domain.
- Per-user rate limiting (revisit before opening real signups).

## Testing

- All existing **1081 tests stay green** (owner-as-user-#1 keeps single-user
  behavior identical after migration).
- New: the users/OAuth upsert + allowlist gate; the migration (backfill correctness,
  idempotency, constraint application); the `barcode_registry` per-user ownership
  derivation; and the **cross-user isolation sweep** above.
- Deploy verification: owner signs in with Google and sees the fully migrated
  library unchanged; a second (test) Google account sees an **empty** library and
  cannot reach the owner's data; a non-allowlisted account is refused.

## Sequencing (rough)

1. Schema + migration (users table, `user_id` columns, backfill, constraints,
   `user_profile` per-user, `barcode_registry` read-path change) — tested locally.
2. Query scoping pass across models/routes, walking the 24-table checklist.
3. Google OAuth login/callback + allowlist gate; remove password path.
4. The cross-user isolation test suite (built alongside 2–3, not after).
5. Deploy: backup, migrate via `ExecStartPre`, verify owner + a test account live.
