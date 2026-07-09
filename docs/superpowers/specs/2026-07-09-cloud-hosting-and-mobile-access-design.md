# Cloud Hosting & Always-On Mobile Access — Design

**Date:** 2026-07-09
**Status:** Approved (design); pending spec review
**Supersedes:** the WireGuard/Firewalla VPN remote-access model from the Android
companion work.

## Problem

Game Tracker runs on the owner's home Windows PC (Flask, `HOST=0.0.0.0:5150`) and
is only reachable on the LAN or through a WireGuard/Firewalla VPN. The owner
never finished setting up the VPN because it is too much friction, and the home
PC is prone to random shutdowns — so the app is frequently unreachable. The goal:
**open the app from anywhere (phone especially) and have it just work**, without a
VPN client and without depending on the home PC being awake.

Immediate target is **single-user** ("just me, anywhere"). A future **multi-user
public deployment** is a hopeful direction; this design must be a stepping stone
toward it, not a dead end.

## Key constraint: scrapers cannot move to the cloud

The vendor scrapers (`scrapers/base.py`) drive **real installed Chrome** with a
**persistent login profile** (`.pw-profile/`). The first run per vendor opens a
**visible browser window for interactive login + 2FA**, and the code deliberately
strips automation fingerprints because Nintendo/PSN actively block bot-looking
browsers. Running these on a headless datacenter VPS is not viable: there is no
screen for the interactive login, and a datacenter IP maximizes security
challenges.

Fortunately the codebase is already decoupled for this: per `base.py`,
*"Scraping never touches the database — it only produces JSON."* Scrape and import
already communicate through a normalized JSON file. So the split below reuses the
existing pipeline almost verbatim.

## Architecture: cloud app + home scraper push

### Cloud half (always-on)

Runs everything the owner *looks at*, independent of the home PC.

- **Host:** a DigitalOcean droplet — $6/mo, 1 GB RAM, Ubuntu LTS, **static IP**.
  Chosen because full SSH access lets the agent install, configure, read logs, and
  operate it directly with no platform abstraction. (Hetzner is an equivalent
  cheaper alternative if preferred.)
- **DNS:** a free **DuckDNS** subdomain (`<name>.duckdns.org`) pointed at the
  droplet's static IP. No domain purchase. Stable hostname for the Android app.
- **TLS:** **Caddy** as the reverse proxy, auto-issuing a real Let's Encrypt cert
  for the DuckDNS name (HTTP-01 challenge). Proxies `:443` → the app. Proper HTTPS
  so the Android app and login cookie are secure, no browser warnings.
- **App process:** **gunicorn** under a **systemd** service (`--workers 1` to keep
  SQLite writes and any in-process state consistent), replacing the Flask dev
  server. Auto-restarts on crash and on droplet reboot.
- **Database:** **SQLite stays** (single-user, low concurrency — ideal). The
  current `games.db` is copied up once. Backups: nightly `sqlite3 .backup` on a
  cron, retaining N days, plus a weekly copy off-box (DO Spaces or scp to another
  location) so a dead droplet never loses the library.
- **Secrets (decided 2026-07-09):** existing vendor/Anthropic creds stay in
  `config.json` (already gitignored; the settings UI writes it) — not churned. The
  **two new auth secrets** (app password hash, import token) plus the Flask session
  secret are read from **environment variables** on the server (the natural
  deployment fit and the project's security rule for new secrets), with optional
  `python-dotenv` loading of a local `.env` for dev. A `config.example.json` and an
  `.env.example` (env var names, empty values) are added to the repo.

### Home half (best-effort, only when the PC is up)

- Scrapers run on the home Windows PC **unchanged** — real Chrome, the owner's
  logins, home IP, interactive 2FA when a session expires.
- **New `--push` step:** after a scrape produces its normalized JSON (which
  `write_scrape` already does), POST that JSON to an authenticated cloud endpoint
  which runs the **existing `import_scraped` pipeline** server-side. No new import
  logic — only the transport changes from "local file read on the same box" to
  "HTTP upload to the cloud box."
- Best-effort by design: if the PC is off for a week, the owner just scrapes next
  time it's on. Owned-library data changes slowly, so lag is harmless.

## Authentication

The app currently has **zero auth** (safe only because it was LAN/VPN-only).
Public hosting requires a gate. Single-user for now, designed as B's first brick.

- **One shared password**, hashed, stored in the server `.env`.
- **Web:** a `/login` page → a long-lived signed session cookie (Flask session).
- **Android / API clients:** the same password once → a stored **bearer token**;
  the app sends it on every request and never prompts again.
- A Flask **`before_request` gate** rejects all routes except `/login`, static
  assets, and a health check unless the request carries a valid session cookie or
  bearer token.
- **Import endpoint auth:** the home scraper-push uses a separate long **import
  token** (also in the server `.env`) so it can post scrape JSON without the
  interactive password flow.
- **Pluggable identity (decided 2026-07-09):** the gate is built around an
  abstract "authenticated identity" — the app asks *"is this request
  authenticated, and as whom,"* and *how* identity is proven sits behind that.
  For now that is the shared password; for **B the intended mechanism is Google
  OAuth / OIDC ("Sign in with Google")**, which avoids password storage, reset,
  and email-verification entirely. Chosen "now" auth is therefore the **plain
  shared password** (a one-row password `users` table would be wasted work since
  Google-authenticated users have no local password).
- **Path to B:** adding Google auth later is a new OAuth login route + a `users`
  table populated from Google identities (email/`sub`) + per-user data scoping —
  an extension of the gate, not a rewrite of it.

## Endpoint changes

- **New:** `POST /api/import/scrape` — authenticated by the import token, accepts
  a normalized scrape JSON payload, runs the import pipeline cloud-side, returns an
  import summary.
  - **v1 fidelity (decided 2026-07-09):** the push carries only the base scrape
    JSON that `write_scrape` already produces (games + add-ons). The cloud runs
    import + IGDB DLC enrichment + Steam DLC + collections sync at full quality,
    but **DLC ownership uses the pipeline's existing name-based `mark_ownership`
    fallback** — the browser-derived PS/Nintendo authoritative parent links
    (`parent_map`/`visited_pids`) are NOT serialized in v1. Implemented by running
    the pipeline with **store resolvers disabled** (no vendor store-page lookups
    from the datacenter IP). A future v2 may serialize the parent links for full
    authoritative-source DLC precision.
- **New:** `GET /login`, `POST /login`, `POST /logout`, and a lightweight
  `GET /healthz` (unauthenticated, for uptime checks / Caddy).
- **Disabled in the cloud deployment:** the in-app scrape controller
  (`/api/scrape/start|continue|cancel|status`) and the web "Scrape now" button —
  the cloud cannot open an interactive login browser. Scraping becomes a home-side
  command that pushes results up. (Routes may remain in code but are gated
  off / hidden when running in cloud mode, e.g. via an env flag.)

## Android app changes

- Point the backend base URL at `https://<name>.duckdns.org` (replacing the LAN
  `http://192.168.228.105:5150`).
- Add a one-time login screen that stores the bearer token; attach it to all
  requests. No further prompts.

## Portability

The Flask **app** must run on Linux. A sweep is needed for Windows-only
assumptions in the *app runtime* (paths, `PowerShell`/`os`-specific calls, the
Windows firewall setup, the `.pw-profile` scrape hooks). The **scrapers stay on
Windows** and are exempt. Package/env management via `uv` on the droplet.

## Division of labor

- **Owner** (needs payment info / 2FA): creates the DO account + droplet, creates
  a DuckDNS subdomain + token, and grants the agent SSH access.
- **Agent:** everything else — droplet setup, Caddy + Let's Encrypt, systemd +
  gunicorn, DB copy-up, backups cron, code changes (auth gate, import endpoint,
  scrape-push `--push`, portability), Android base-URL/login, and verification.

## Testing

- Existing **972 tests must stay green**.
- New tests: the `before_request` auth gate (allow/deny matrix, cookie + bearer),
  the `/api/import/scrape` ingest endpoint (auth required; runs import pipeline),
  and the scrape `--push` client (posts the JSON `write_scrape` produced).
- Deploy verification: HTTPS reachable at the DuckDNS name; login works from a
  fresh browser and from the Android app; a home scrape `--push` lands in the
  cloud DB; app survives a droplet reboot (systemd) and a nightly backup runs.

## Out of scope (future / B)

- Multi-user accounts, **Google OAuth/OIDC login**, signup, per-user data
  isolation. (Google auth is the intended B mechanism — see Authentication — but
  is deliberately not built now; the gate is only made forward-compatible with it.)
- A purchased domain / branded URL.
- Running scrapers in the cloud (residential proxies, remote-desktop login).
- Migrating off SQLite (only needed at real multi-user scale).

## Sequencing (rough)

1. App changes first, tested locally: auth gate, `/login`, `/healthz`,
   `/api/import/scrape`, scrape `--push`, cloud-mode flag to disable in-app scrape,
   `.env.example`, portability fixes.
2. Provision droplet + DuckDNS + Caddy + systemd/gunicorn; deploy; copy `games.db`;
   backups cron.
3. Android base URL + login.
4. End-to-end verification, then decommission the VPN.
