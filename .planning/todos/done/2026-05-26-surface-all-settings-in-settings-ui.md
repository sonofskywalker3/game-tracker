---
created: 2026-05-26T14:29:13.867Z
title: Surface all settings (incl. Steam creds) in Settings UI
area: ui
files:
  - templates/settings.html
  - config.py:36 (get_twitch_credentials)
  - config.py:47 (get_steam_credentials)
  - app.py (settings page route + new save endpoint)
  - config.json (gitignored; becomes persistence layer behind the GUI)
---

## Problem

Several user-tunable values live only in `config.json` today and require
hand-editing: Steam credentials (`steam_api_key`, `steam_id` — added in SP2;
referenced from `config.get_steam_credentials()` at `config.py:47`), Twitch /
IGDB credentials (client_id + secret — `config.get_twitch_credentials()` at
`config.py:36`), and any other keys the app reads from the file.

This came up at the close of the SP3 ship: setting up the live Steam scrape
still requires editing `config.json` by hand to drop in the API key + SteamID64.
That's the immediate prompt, but the broader principle the owner stated is:
**stop relegating settings to a file**. The settings page should be the single
surface for user-tunable values; `config.json` can stay as the persistence
layer behind it, but the user should never have to open the file to change a
setting.

`templates/settings.html` already exists — this is an extension of that page,
not a new one.

## Solution

TBD in design, but the broad shape:

1. **Audit every value read from `config.json`** (search for `config.get_*`
   helpers in `config.py` plus any direct reads elsewhere) and list them. Steam
   creds and Twitch creds are the known ones; check for others.
2. **Settings page UI:** add a "Credentials" / "Integrations" section with
   labeled inputs for each credential (password-style inputs where appropriate).
   Steam: `steam_api_key`, `steam_id`. Twitch: `client_id`, `client_secret`.
   Plus whatever else the audit surfaces.
3. **Save endpoint:** `POST /api/settings` (or similar) writes the submitted
   values back to `config.json` atomically (write to tempfile + rename, so a
   partial write can't corrupt the file). Validate shape — never accept a
   non-string for a string field; never blank out a value the user didn't touch.
4. **Read path:** Keep the existing `config.get_*` helpers as the canonical
   read API — the GUI just becomes a writer. No new code reads `config.json`
   directly.
5. **First-run UX:** if a required credential is missing, the relevant feature
   (Steam scrape, IGDB enrichment) should surface a clear inline error and
   link to the settings page, rather than fail with a stack trace.
6. **Security note:** `config.json` is gitignored. Don't leak credential
   values back into the API response when the page reloads — return a masked
   placeholder (e.g. `••••••••` if set, empty otherwise) so the page can show
   "configured" without exposing the secret.
