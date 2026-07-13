# BacklogQuest Scraper — Desktop App Design

**Date:** 2026-07-13
**Status:** Approved (owner, in-session)

## Purpose

Let anyone scrape their vendor game libraries (PlayStation, Xbox, Nintendo,
Steam) on their own PC with a small branded GUI app, then:
- **always**: export the result as a CSV they can use anywhere, and
- **optionally**: sync it to a BacklogQuest server with an import token.

Scraping must stay on the user's machine: vendor logins need a real browser,
interactive 2FA, and a residential IP (datacenter IPs get blocked).

## Scope

- **In**: the Windows desktop app (installer + portable flavors), a
  personalized-download route on the server, a version-check endpoint,
  a release script.
- **Out**: multi-user accounts / per-user tokens (future project — this app is
  designed so that work changes nothing here), macOS/Linux builds, code
  signing (deferred; SmartScreen warning accepted for v1).

## Decisions (locked with owner)

1. Audience: future multi-user backlogquest.xyz; token is an opaque string so
   per-user tokens later Just Work. CSV export works with no token at all.
2. Scope: desktop app only; server gets only the two small routes below.
3. Token bake-in is a **hard requirement**: satisfied by the personalized
   portable zip. Installer users paste the token once (owner-approved).
4. GUI is BacklogQuest-branded (dark #181A22, indigo #8B93FF accents, sword
   icon) via pywebview; not a plain native toolkit.
5. Approach A: reuse `scrapers/` unchanged; PyInstaller onedir; ship BOTH an
   Inno Setup installer and a portable zip (owner hates bare zips as the only
   option).

## Architecture

```
Game Tracker repo
├── scrapers/            UNCHANGED — shared by CLI, web app, and desktop app
├── scrape_libraries.py  UNCHANGED — CLI keeps working
└── desktop/             NEW
    ├── main.py          pywebview window bootstrap + PyInstaller entry
    ├── bridge.py        JS↔Python API: start_scrape(vendors), continue_login(),
    │                    skip_vendor(), export_csv(path), sync(), config get/save
    ├── runner.py        Worker thread driving scrapers/*.collect(); the CLI's
    │                    Enter-prompt becomes a threading.Event set by Continue
    ├── config.py        Sidecar backlogquest.json (next to exe) SEEDS
    │                    %APPDATA%\BacklogQuest\config.json on first run only;
    │                    after that the appdata config wins (user edits persist)
    ├── ui/              index.html + style.css + app.js (BacklogQuest theme)
    └── build.spec       PyInstaller spec (playwright driver hook, onedir)
```

- `scrapers/` is the contract. Vendor fixes land there once and benefit CLI,
  web scrape, and desktop app together (scraper-fixes-shared rule, structural).
- One seam in existing code: `scrapers/base.py`'s three path constants
  (PROFILE_DIR, RECON_DIR, SCRAPE_DIR) become overridable so the frozen app
  redirects them to `%APPDATA%\BacklogQuest\`.
- Playwright sync API runs on a worker thread (never the GUI thread).
- Browser: the user's installed Chrome/Edge via Playwright channels — the
  existing `_launch_context` logic, unchanged (automation fingerprints
  suppressed; Nintendo requires this). Separate persistent profile in
  `%APPDATA%\BacklogQuest\pw-profile` — never the user's daily profile; vendor
  logins persist across runs. No bundled browser (Windows always has Edge).
- Everything else is bundled by PyInstaller: Python runtime, Playwright lib +
  driver, requests, pywebview. User installs nothing.
- GUI renders via WebView2 (preinstalled on Win10/11). Installer bootstraps it
  if missing (standard Inno recipe); portable shows an "install WebView2" link
  instead of a blank window.

## Packaging: two flavors of one build

PyInstaller **onedir** (onefile trips antivirus heuristics and unpacks slowly):

1. **Installer** — `BacklogQuest Scraper Setup.exe` (Inno Setup, free/CLI-able).
   Installs to `%LOCALAPPDATA%\Programs\BacklogQuest Scraper`, Start-menu
   entry, uninstaller. Generic binary → user pastes token once; it persists in
   `%APPDATA%\BacklogQuest\config.json`.
2. **Portable** — zip of the onedir folder. The server personalizes this one
   (below): `backlogquest.json` sidecar with server URL + token → zero-setup.

## GUI flow (one window, three states)

**Setup**: sword mark + title; four vendor checkboxes (all checked); big
indigo **Start scraping**. Settings row: server URL (prefilled
`https://backlogquest.xyz`) + token. When sidecar/saved config provides them:
collapsed green "✓ Sync configured" chip, expandable to edit. No token:
"CSV only — add a token to sync".

**Scraping** (vendors sequential): Chrome opens beside the app. Window shows
"<Vendor> — log in, open your library / purchase history, then click
**Continue**". After Continue: live ticking progress ("collecting… N titles
found") — never a static frozen message. **Skip** button per vendor; a vendor
error or user-closed browser = automatic Skip with a note, never aborts the
other vendors.

**Results**: per-vendor counts table, then BOTH actions (not either/or):
- **Save CSV** — save dialog; one combined CSV, columns
  `title, platform, source, kind, external_id, source_title, cover_url`.
- **Sync to BacklogQuest** — only when a token is set; POSTs each vendor's
  payload to `/api/import/scrape` (existing endpoint, bearer token); shows the
  server summary per vendor ("added 3, updated 211") + retry on failure.

Scraped JSON always persists in `%APPDATA%\BacklogQuest\scraped\` — a crash or
failed sync never loses a scrape.

## Server additions (behind the existing login gate)

- `GET /download/scraper?flavor=portable` — streams the prebuilt portable zip
  with an injected `backlogquest.json`:
  `{"server_url": "https://backlogquest.xyz", "token": "<import token>"}`.
  Today: the instance's single import token. Multi-user later: the logged-in
  user's token — route signature unchanged.
- `GET /download/scraper?flavor=installer` — serves the generic installer.
- `GET /api/scraper/version` — public; returns the latest version string. App
  checks on launch → soft "Update available" link (vendor pages rot; stale
  scrapers in the wild need a nudge).
- Settings page gets a "Get the scraper" section: two download buttons with
  one-line explanations (installer = paste token once; portable = token baked
  in).

Artifacts live on the droplet (scp'd by the release script), not in git.

## Error handling

- No Chrome/Edge: friendly in-app message, not a crash.
- Vendor page changed → 0 titles: results flag "0 found — vendor may have
  changed their site; check for an update" (never silent success).
- Sync 401: "token rejected — check your token". Timeout/5xx: "server busy,
  your CSV is safe, try again" + retry button.
- Rotating log at `%APPDATA%\BacklogQuest\scraper.log` so users can report
  breakage usefully.

## Testing

- Unit-tested in the existing pytest suite: runner state machine, config
  seeding (sidecar seeds appdata once; appdata wins thereafter), CSV writer,
  version-check parsing.
- Server-side tests: zip injection (config present, token correct), login
  gating of both download routes, version endpoint.
- GUI + real vendor logins: manual smoke per release (inherently interactive).

## Build & release

`release_scraper.ps1`: PyInstaller build → Inno Setup compile → zip portable →
scp both + a `version.txt` to the droplet (the `/api/scraper/version` route
just reads that file). Built on the owner's PC for v1
(no CI dependency). Known accepted costs: ~60–90MB app, unsigned-exe
SmartScreen warning until a code-signing cert is bought.
