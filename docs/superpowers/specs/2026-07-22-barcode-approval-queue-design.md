# Barcode Approval Queue — Design Spec

Date: 2026-07-22
Status: Approved (owner confirmed design + edit-before-approve mechanism 2026-07-22)
Depends on: Multi-user Identity & Isolation (Spec 1, complete + live).

## Problem

Today `POST /api/barcode/link` writes straight into the shared `barcode_registry`
for *any* authenticated user. The registry is a global UPC→identity map read by
every user's barcode scan (`barcode.resolve` cache path). With multi-user now live
(Google OAuth, beta testers via Google's test-user list), a tester can pollute the
shared UPC→game mapping for everyone — wrong title, wrong cover, wrong platform —
with no owner oversight.

We want testers to still get an *immediate, working* scan experience for links they
submit, without letting their unreviewed submissions leak into the shared registry.
The owner curates what becomes globally trusted.

## Requirements (from the brainstorm, owner-confirmed)

1. **Submitter effect = provisional for them only.** A tester's own pending link
   resolves for the tester immediately (a per-user provisional layer), but stays OUT
   of the shared `barcode_registry` until the owner approves. No other user sees it.
2. **Owner curation via an owner-only review API** (list / approve / reject) plus a
   simple review-list view, mirroring the existing bundle-review screen.
3. **Owner links are trusted:** an owner `POST /api/barcode/link` writes the registry
   directly (today's behavior). A non-owner link is queued instead.
4. **Edit-before-approve is IN for v1.** The owner may correct a queued row's identity
   fields (`title`, `igdb_id`, `platform`, `cover_url`) at approval time; the corrected
   values are what get written to the shared registry.
5. **Plain reject for v1.** Reject marks the row rejected and stops the provisional; the
   UPC is resubmittable (no "don't ask again" memory). Deferred as a later enhancement.

## Architecture overview

One new per-user table (`barcode_link_review`), a branch in the existing link route,
a new precedence step in `barcode.resolve`, three owner-only review endpoints, one
idempotent migration, and an owner review-list UI section in `settings.html`. The
design deliberately mirrors the existing `dlc_review_queue` / `bundle_review_queue`
patterns so it drops cleanly into the Task 1–9 isolation model.

### New table: `barcode_link_review` (per-user)

Fresh `CREATE TABLE` with `user_id` inline (a fresh create with the column inline
avoids the FK-off/on ADD-COLUMN caution that retrofits require):

| column       | type / notes                                                    |
|--------------|-----------------------------------------------------------------|
| `id`         | INTEGER PRIMARY KEY AUTOINCREMENT                                |
| `user_id`    | INTEGER NOT NULL REFERENCES users(id) — the submitter           |
| `upc`        | TEXT NOT NULL                                                    |
| `platform`   | TEXT                                                             |
| `igdb_id`    | INTEGER                                                          |
| `title`      | TEXT                                                             |
| `cover_url`  | TEXT                                                             |
| `game_id`    | INTEGER — submitter's own proposed library row (informational)  |
| `status`     | TEXT NOT NULL DEFAULT 'pending' — pending / approved / rejected  |
| `created_at` | TEXT NOT NULL DEFAULT (datetime('now'))                         |
| `resolved_at`| TEXT                                                            |

Constraints / indexes:
- `UNIQUE(user_id, upc)` — a resubmission by the same user upserts the same row.
- Index on `(status)` for the owner's pending-list query, and on `(user_id, upc)` is
  provided by the UNIQUE constraint (used by the provisional resolve lookup).

Because the table carries `user_id`, it fits the isolation model: the review-list GET
is owner-only, and each tester's provisional lookup is scoped to their own rows.

**`game_id` handling:** the submitter's `game_id` points at *their own* library row,
which is meaningless (and a leak) as a shared mapping. It is stored here only as
informational/provisional data for the submitter. It is **never** carried into the
shared `barcode_registry` on approve — registry rows supply identity only
(`title`/`igdb_id`/`platform`/`cover_url`); per-user ownership stays derived via
`barcode._owned_game_id` (established in Task 8), never from a stored `game_id`.

### Migration: `migrate_barcode_link_review(conn)`

New idempotent migration registered in `models.migrate_db()`:
- `CREATE TABLE IF NOT EXISTS barcode_link_review (...)` with `user_id` inline.
- `CREATE INDEX IF NOT EXISTS` for the `status` lookup.
- Touches no existing data. Safe to run repeatedly (mirrors the other
  `CREATE TABLE IF NOT EXISTS` migrations). Register it in `migrate_db()`'s ordered
  sequence after `users` exists (it FK-references `users(id)`).

## Submission — `POST /api/barcode/link` (app.py ~2491)

Branch at the top of the existing handler on `identity.is_owner()`:

- **Owner** → today's behavior unchanged: `barcode.registry_put(...)` straight to the
  shared registry, return `{ok: true}`.
- **Non-owner (tester)** → upsert a `pending` row into `barcode_link_review` for
  `identity.current_user_id()` (`INSERT ... ON CONFLICT(user_id, upc) DO UPDATE` — a
  resubmission refreshes the proposed identity fields, re-sets `status='pending'` and
  `resolved_at=NULL`, and preserves the original `created_at`). Nothing touches the
  shared registry. Return `{ok: true, queued: true}`.

Validation (unchanged): `upc` and `platform` required → 400 otherwise.

## Provisional resolve — `barcode.resolve` (barcode.py ~458–482)

`resolve(conn, upc, *, client_id, token, user_id=identity.OWNER_USER_ID)` currently:
`registry_get(conn, upc)` → if hit, build the cache candidate (deriving ownership via
`_owned_game_id`) → else product/IGDB path.

New precedence — insert one step **between** the registry hit and the product path:

1. **Shared `barcode_registry`** (approved / globally known) → use it (unchanged).
2. **Else the caller's OWN `pending` `barcode_link_review` row** for that UPC
   (`WHERE user_id = ? AND upc = ? AND status = 'pending'`) → build a cache-shaped
   candidate from it (provisional, submitter-only). `source` = `"provisional"` so the
   caller/tests can distinguish it from a shared-registry `"cache"` hit.
3. **Else** the product/IGDB path (unchanged).

The provisional supplies IDENTITY only (`title`/`igdb_id`/`platform`/`cover_url`);
ownership (`owned_game_id`/`owned_platforms`) is derived exactly as in the cache path
via `_owned_game_id(conn, title, user_id)` — no special-casing. Only `status='pending'`
rows resolve provisionally; `rejected` (and `approved`, which by then live in the
shared registry) rows do not.

Isolation: because the lookup filters `user_id = ?`, user B never sees user A's
pending provisional. A non-submitter with no registry entry for that UPC falls through
to the product/IGDB path exactly as before.

## Owner-only review endpoints

All three gate on `identity.is_owner()` → 403 for non-owner (mirrors the existing
enrichment/dlc/bundle owner-gating seam added post-Task-9).

### `GET /api/barcode/review`
List `pending` rows across all submitters: `id, user_id (submitter), upc, platform,
igdb_id, title, cover_url, game_id, created_at`. Returns `{items: [...]}`. Add this
path to the isolation completeness gate's `_OWNER_ONLY` set (see Testing).

### `POST /api/barcode/review/<id>/approve` (edit-before-approve)
Optional JSON body with edited identity fields: `title`, `igdb_id`, `platform`,
`cover_url`. Flow:
1. Load the row; 404 if missing, 409 if not `pending`.
2. Merge supplied overrides over the row's stored values → **final identity**.
3. `barcode.registry_put(conn, upc, igdb_id=final.igdb_id, title=final.title,
   platform=final.platform, cover_url=final.cover_url)` — **no `game_id`**. Now live
   for everyone (COALESCE-guarded upsert, as today).
4. `UPDATE` the row to the final identity values + `status='approved'`,
   `resolved_at=datetime('now')` — so the review list reflects what actually went live.
5. Commit. Return `{ok: true}`.

This single-endpoint "approve-with-overrides" shape matches the existing bundle-review
approve, which already posts edited data (`{constituents: [...]}`). A separate
PATCH-then-approve is deliberately avoided: it adds surface and would let owner edits
leak into the submitter's provisional view before approval.

### `POST /api/barcode/review/<id>/reject`
Load the row; 404 if missing, 409 if not `pending`. Mark `status='rejected'`,
`resolved_at=datetime('now')`.
The submitter's provisional stops applying (only `pending` rows resolve provisionally).
No registry write. The UPC is resubmittable (plain reject — no memory). Return
`{ok: true}`.

## UI — owner review-list section in `settings.html`

Mirror the existing `#bundle-review-section` in `templates/settings.html`:
- A hidden-by-default section that reveals when `GET /api/barcode/review` returns
  pending items (owner-only; non-owner gets 403 and the section stays hidden).
- Each item renders the proposed `title` / `igdb_id` / `platform` / `cover_url` in
  **editable inputs**, an "Approve" button (posts the edited fields to
  `.../approve`), and a "Reject" button (posts to `.../reject`). Shows the submitter
  and UPC read-only.
- On success, re-fetch the list. Reuse the existing settings.html fetch/post helpers
  and styling conventions already used by bundle-review.

No new template file — the barcode review section joins the existing owner surfaces in
`settings.html`.

## Testing

Reuse `tests/helpers_multiuser.py` (`mu_db` / `client_as` / `seed_game`; `app_ctx_as`
for direct `barcode.resolve` calls). All DBs temp/in-memory — never the real games.db.

Behavioral tests:
1. **Tester link → queued.** Non-owner `POST /api/barcode/link` creates a `pending`
   row in `barcode_link_review` and writes **nothing** to `barcode_registry`; returns
   `queued: true`.
2. **Owner link → registry direct.** Owner `POST /api/barcode/link` writes
   `barcode_registry` (today's behavior) and creates no review row.
3. **Resubmission upserts.** A tester resubmitting the same UPC updates the same row
   (UNIQUE(user_id, upc)), not a duplicate; refreshed identity, `status` back to
   `pending`.
4. **Provisional resolve — submitter sees own pending.** After a tester queues a UPC,
   `barcode.resolve(..., user_id=tester)` returns the provisional candidate
   (`source='provisional'`, correct title/igdb_id).
5. **Provisional isolation — other user does NOT.** `barcode.resolve(..., user_id=other)`
   for the same UPC does not see the tester's pending row (falls through to product/
   IGDB / `source='none'` with no client). Owner likewise doesn't see it via resolve.
6. **Approve → registry, edited values win.** Owner approve with overridden `title`
   writes the **edited** title to `barcode_registry`; the UPC now resolves for everyone
   (`source='cache'`); row is `approved` with `resolved_at`; `game_id` not written to
   the registry.
7. **Reject → provisional stops.** Owner reject marks the row `rejected`; the tester's
   subsequent `resolve` no longer returns the provisional; no registry write.
8. **Review endpoints owner-only.** Non-owner `GET /api/barcode/review`,
   `.../approve`, `.../reject` → 403.
9. **Completeness gate.** Add `/api/barcode/review` to `_OWNER_ONLY` in
   `tests/test_multiuser_isolation.py` with a justification; the url_map completeness
   test stays green.

Standing constraints (every subagent): work on `main`; run `uv run python -m pytest`;
`ruff check` only (never `ruff format`); temp/in-memory DBs only; type hints on all
signatures; `logging` not `print()`; secrets via env only.

## Out of scope (v1 — possible later enhancements)

- **Permanent reject / "don't ask again."** Rejected UPCs are not remembered; a tester
  can resubmit. (Owner chose plain reject for v1.)
- **Approving onto a specific shared `game_id`.** The registry stores identity only;
  ownership is per-user. No shared game_id is written.
- **Notifications** to owner on new submissions, or to submitter on approve/reject.
- **Bulk approve/reject.** One row at a time in v1.
