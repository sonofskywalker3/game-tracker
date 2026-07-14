# Stub Installer + One-Click Self-Update — Design

**Date:** 2026-07-14
**Status:** Approved (owner, in-session)

## Purpose

Kill two distribution pains:
1. Chrome deep-scans the 41MB unsigned `Setup.exe` for ~1 minute on every
   download. The browser should only ever see a ~2MB stub.
2. Updating means manually re-downloading and re-running the installer (the
   owner once ran a known-broken 0.1.0 three times). The app should update
   itself from the existing launch-time version banner with one click.

Approach chosen: **A — stub runs the existing full installer; the app runs
that same installer silently for updates.** Rejected: folder-swap updater
(locked-file risk, duplicates the installer), code signing (recurring cost,
doesn't fix updates; the marker-filename scheme already survives a future
signature).

## Design

### 1. Stub installer (`installer/backlogquest-scraper-stub.iss`)

A minimal Inno setup (~2MB): `CreateAppDir=no`, `Uninstallable=no`, no app
payload. `[Code]`:

1. Parse the stub's own filename for the existing `.h-<host>.c-<token>`
   markers (same Pascal routines as `backlogquest-scraper.iss`); fallback
   host `backlogquest.xyz` when the name carries no markers.
2. Native download page (`CreateDownloadPage` + `DownloadTemporaryFile`)
   fetches `https://<host>/download/scraper/payload` with a short
   explanation ("Downloading BacklogQuest Scraper — this skips your
   browser's slow scan of large files").
3. Save the payload in `{tmp}` **named with the same marker suffix the stub
   was launched with**, `Exec` it (forwarding `/SILENT`-family flags when
   present), exit with its result code.

The full installer therefore needs **zero install-flow changes**: it already
decodes markers from `{srcexe}`'s filename and writes the
`backlogquest.json` sidecar. A stub is version-agnostic — it always installs
whatever the server currently publishes.

### 2. Server distribution (`scraper_dist.py` + download routes)

- The settings-page installer link serves the **stub bytes** under the same
  version-stamped, marker-stamped download name users see today
  (`BacklogQuest-Scraper-Setup-<ver>.h-<host>.c-<token>.exe`). When no stub
  artifact is published yet, fall back to serving the full exe (deploy-safe
  ordering; old links keep working mid-deploy).
- New plain route **`/download/scraper/payload`** serves the full
  `BacklogQuest-Scraper-Setup.exe`. Fetched only by machines (stub +
  self-updater), never by a browser. 404 when unpublished, like the
  existing flavors.
- Portable-zip flow unchanged.
- `release_scraper.ps1` compiles the stub .iss alongside and publishes
  `BacklogQuest-Scraper-Stub.exe` with the other artifacts.

### 3. One-click self-update (desktop app)

UI: the existing `#update-banner` ("Update available — vX.Y.Z", populated by
`bridge.py`'s launch-time `check_for_update`) gains an **Update now**
button. States: ticking download progress ("Downloading v0.1.5 —
12.3 / 41.0 MB", per the live-progress rule) → "Installing — the app will
restart itself…".

New `desktop/selfupdate.py`:
- `download_installer(server_url, dest_dir, progress) -> Path` — streams
  `/download/scraper/payload` to
  `%TEMP%\BacklogQuest-Scraper-Setup-<version>.exe` with byte-count progress
  callbacks. Deliberately a **plain** filename (no markers) so the installer
  leaves the existing sidecar/config alone. Overwrites any previous attempt
  rather than accumulating temp files.
- `installer_command(path) -> list[str]` —
  `[exe, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/RELAUNCHAPP=1"]`
  (pure; unit-testable without spawning).
- A launch helper that spawns the installer detached, then asks the app to
  close.

`desktop/bridge.py`: new `start_update()` js_api method — download on a
background thread (progress events to the banner), spawn installer, destroy
the window. **`_window` stays underscore-private** (pywebview#1815 hang;
regression test must stay green).

Full installer (`backlogquest-scraper.iss`) additions:
- `CloseApplications=force` + `RestartApplications=no` — if the app process
  is still tearing down when the silent install begins, Restart Manager
  closes it instead of the copy dying on a locked exe.
- `/RELAUNCHAPP=1` command-line switch → relaunch
  `BacklogQuest Scraper.exe` detached when the install finishes.

## Error handling

- Stub download failure → message box with the attempted URL, non-zero exit.
  No retry loop. A renamed (marker-less) stub still installs; the app falls
  back to the paste-the-token flow.
- Self-update failure (download or spawn) → banner error state ("Update
  failed: … — you can keep using this version"); the running install is
  untouched; retry via the button or next launch.
- Server payload route 404s when artifacts are missing (matches existing
  behavior).

## Testing

- **Unit (pytest):** `scraper_dist` stub-vs-full serving choice + fallback +
  payload route; `selfupdate` streamed download with progress against a fake
  fetcher, plain temp filename, exact installer command; bridge
  `start_update` with selfupdate mocked; `_window` privacy test unchanged.
- **Pascal `[Code]` is not unit-testable** — live smoke instead: build both
  installers (`-SkipPublish`), run the stub locally against the live server,
  verify install + sidecar; then a scratch version bump to verify the
  in-app one-click update end-to-end.
- Full suite via `uv run python -m pytest -n auto -q`; `ruff check` only.

## Rollout note

v0.1.4 installs predate the self-updater, so one final manual download is
unavoidable — but it is already the 2MB stub. Every later version updates
from the banner.

## Out of scope

Code signing (future distribution polish; filename markers already survive
it). Delta/patch updates. Auto-update without a click. Server-side Steam
sync and gamification (separate backlog items).
