# Cover art — IGDB as source of record — design

**Date:** 2026-05-22
**Status:** approach approved; ready for implementation
**Workstream:** 1 of 4 (covers) — see [[post-import-cleanup-workstreams]]

## Goal

Covers should be consistent portrait box art. Today's Nintendo import stored
`assets.nintendo.com` **1920×1080 landscape** hero art on 72 games (the "too
wide" art); IGDB portrait art (`images.igdb.com/.../t_cover_big`, ~264×352) is
the standard, used by 597 games. Make IGDB the cover of record and upgrade the
non-IGDB covers, durably (general pipeline logic, not a one-time DB patch — per
[[cleanup-fixes-must-be-general]]).

Current cover hosts: 597 `images.igdb.com`, 72 `assets.nintendo.com` (wrong), 38
`image.api.playstation.com` (square), 16 other, 3 none.

## Design

### 1. Scrapers stop injecting wrong-shape art

`scrapers/nintendo.py` no longer stores the Cloudinary hero image as
`cover_url` — it sets `cover_url=None` and defers to the IGDB pipeline. (General
rule: scrapers don't populate `cover_url` with art that conflicts with the IGDB
standard.) Remove the `CLOUDINARY_BASE` cover construction; the parser keeps the
NSUID/title/platform mapping.

### 2. `fetch_covers.py` learns to upgrade non-IGDB covers

Today it only fills NULL/empty covers (`skip_existing`), so it skips the 72.
Add an upgrade mode (new flag, e.g. `--upgrade-non-igdb`) that also selects
covers whose host isn't IGDB.

Pure, testable helpers (the durable, table-driven rules):
- `IGDB_HOST = "images.igdb.com"`
- `WIDE_ART_HOSTS = frozenset({"assets.nintendo.com"})` — known-bad (wrong
  aspect); extensible.
- `needs_cover(url, *, upgrade) -> bool` — True if missing, or (upgrade and host
  != IGDB_HOST).
- `cover_host(url) -> str | None`.
- On IGDB **match**: replace. On IGDB **miss**: null the cover only if its host
  is in `WIDE_ART_HOSTS` (the broken ones); otherwise keep the existing cover
  (don't wipe a working PSN square cover that IGDB can't match).

Matching safety: accept an IGDB result only on a confident match (normalized
equality or containment — the existing logic's first pass), not the
"first result with any cover" fallback, to avoid replacing a correct vendor
cover with a wrong game's art.

Existing callers (`skip_existing` default, `--all`) keep working unchanged; the
upgrade behavior is opt-in.

### 3. One-time application

Run the upgraded fetcher over existing rows (no SQL):
`python fetch_covers.py --client-id … --client-secret … --upgrade-non-igdb`
(needs the user's Twitch/IGDB creds, same as the original 597). Re-runnable and
idempotent: once a cover is IGDB it's skipped; a wide-art game IGDB can't match
becomes null and stays so.

## Testing

- `cover_host` parses host from http(s) URLs; `None` for empty.
- `needs_cover`: missing → True; IGDB host → False; non-IGDB + upgrade → True;
  non-IGDB without upgrade → False.
- Miss behavior: wide-art host → nulled; PSN/other host → kept.
- `scrapers/nintendo.py` parse test updated to assert `cover_url is None`.
- IGDB network calls are not unit-tested (live), matching repo convention.
- Existing 39 tests stay green.

## Constraints

- Public repo: never commit `games.db`, `.recon/`, `scraped/`, `config.json`,
  `.igdb_token.json` (verify gitignored).
- Conventional commit style, no co-author trailer.
- Don't break `fetch_covers` callers in `app.py` / `background_tasks.py`.
