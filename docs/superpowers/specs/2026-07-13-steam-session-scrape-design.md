# Steam Session-Token Scrape — Design

**Date:** 2026-07-13
**Status:** Approved (owner, in-session)

## Purpose

Make the desktop app's Steam scrape work with ZERO configuration — no Steam
Web API key, no SteamID entry, nothing sent to any server. Privacy-first:
users who won't hand credentials to a server get the same "log in and click
Continue" flow as the other three vendors.

## Feasibility (proven live 2026-07-13 on the owner's session)

- `GET https://store.steampowered.com/pointssummary/ajaxgetasyncconfig` with a
  logged-in browser session returns `{"data": {"webapi_token": "<JWT>"}}`.
- The JWT payload's `sub` claim is the user's SteamID64 (base64 JSON decode,
  no signature verification needed — we only read our own token).
- `GET https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/?access_token=<token>&steamid=<sub>&include_appinfo=true&include_played_free_games=true&format=json`
  returned 1,038 named games. Official API, official token, no API key.
- `store.steampowered.com/dynamicstore/userdata/` returned 1,956 owned appids
  (games+DLC) on the same session. The owner's earlier in-app run got 0 —
  timing/caching right after login; fetching after the token step with one
  cache-busted retry mitigates.

## Design

All changes in `scrapers/steam.py` (shared by CLI, web, desktop — per the
scraper-fixes-shared rule). No UI changes: the desktop walkthrough already
says "Login to your Steam account... then Continue".

New module constants:
- `TOKEN_CONFIG_URL = "https://store.steampowered.com/pointssummary/ajaxgetasyncconfig"`

New pure helpers (unit-tested):
- `parse_webapi_token(payload: dict) -> str` — extracts `data.webapi_token`,
  `""` when absent/malformed.
- `steamid_from_token(token: str) -> str` — splits the JWT, base64-decodes the
  payload (with `=` padding fix-up), returns `sub` or `""` on any parse issue.

`collect(page, captured)` becomes a three-tier ladder:
1. **Config creds path (unchanged):** `config.get_steam_credentials()` present
   → keyed `GetOwnedGames` via `requests` exactly as today (CLI/back-compat).
2. **Session-token path (new):** `page.request.get(TOKEN_CONFIG_URL)` →
   `parse_webapi_token` → `steamid_from_token` → `page.request.get`
   GetOwnedGames with `access_token=` → `parse_owned_games` (existing parser;
   response shape is identical).
3. **Honest failure:** no creds AND no session token → `RuntimeError("Log into
   Steam in the browser window first, then press Continue")` (shown by the
   desktop results row).

userdata fix (applies to all paths): fetch `USERDATA_URL` AFTER the
games step; if `rgOwnedApps` comes back empty, wait ~2s and retry once with a
`?v=<monotonic counter>` cache-buster. Still best-effort (empty on second
miss, logged).

## Error handling

- Token endpoint non-200 / malformed JSON / missing token → tier 3 error.
- GetOwnedGames non-200 on the session path → tier 3 error (message includes
  the status).
- userdata failures stay non-fatal (owned-DLC carriers empty, logged).

## Testing

- Pure: `parse_webapi_token` (present/absent/malformed), `steamid_from_token`
  (valid JWT, padding-needed payload, garbage, missing sub).
- `collect` wiring with a fake `page.request` object: session path returns
  games; empty-then-retry userdata; no-creds-no-session raises the exact
  message; config-creds path still short-circuits (monkeypatched requests).
- Live smoke: owner (or the already-logged-in profile on this PC) runs the
  desktop Steam scrape end-to-end.

## Out of scope

Server-side Steam sync (website users who accept storing creds server-side)
stays a separate backlog item. Desktop UI changes: none.
