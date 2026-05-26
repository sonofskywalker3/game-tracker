# DLC Steam vendor — end-to-end (SP2) — design

**Date:** 2026-05-25
**Branch:** main (work directly on main, per repo workflow)
**Status:** approach approved; ready for implementation plan

This is **SP2** of the DLC source-of-truth rework. SP1 (the vendor-source
foundation + Nintendo/Xbox owned-add-on fix) has landed on `main`. SP2 adds
**Steam** as the first vendor with a *complete* DLC catalogue, proving the
**deep-fetch** path (catalogue from the store + ownership by id set-intersection)
and populating the data that the later missing-DLC view (SP7) will surface.

Foundation spec: `docs/superpowers/specs/2026-05-25-dlc-vendor-source-foundation-design.md`.

## Goal

Sync the user's **Steam** library end-to-end: import owned Steam games, pull each
game's **full DLC catalogue** from Steam, and mark exactly the DLC the user owns —
all keyed on Steam **appid**, so ownership is a pure id set-intersection (no name
heuristics). After SP2, Steam games show owned **and** unowned (missing) DLC; the
hero `owned/total DLC` tile and the post-scrape result list reflect Steam.

## Decisions (approved in brainstorming)

- **Source of truth = the Steam store** (per the rework): catalogue via the keyless
  storefront `appdetails`, ownership via the user's logged-in session. IGDB is not
  used for Steam games.
- **Hybrid auth:** a free **Steam Web API key + SteamID64** in `config.json` (like
  the existing Twitch creds) for a clean one-call owned-games list, **plus** the
  logged-in browser session for owned-DLC (Steam has no DLC-ownership API).
- **No new schema:** reuse SP1's `dlc` + `dlc_external_ids` tables.
- The missing-DLC **view** stays SP7; SP2 only populates the data.

## Background (verified facts)

### Steam mechanics (researched)

- **Owned games:** `GET https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/?key=<key>&steamid=<id64>&include_appinfo=true&include_played_free_games=true`
  → `response.games[]` with `appid`, `name`, `playtime_forever`, `img_icon_url`.
  Querying the **key owner's own** `steamid` works regardless of profile privacy.
- **Owned DLC — no API.** `GetOwnedGames` returns base games only; there is no
  official owned-DLC method. The reliable source is the **logged-in session**:
  `GET https://store.steampowered.com/dynamicstore/userdata/` → `rgOwnedApps`, a
  flat array of **every owned appid (games and DLC)**. Auth = session cookies (no
  key, no public-profile requirement). Server-cached (can lag right after a
  purchase — acceptable, eventual consistency).
- **DLC catalogue (keyless):** `GET https://store.steampowered.com/api/appdetails?appids=<appid>`
  → `<appid>.data` with `type` (`"game"`/`"dlc"`), `name`, `dlc` (array of DLC
  appids = the catalogue), and for a DLC `fullgame.appid` (string) back-pointer.
  **One appid per call** (multi-appid only works with `filters=price_overview`).
  Rate limit ~**200 requests / 5 min / IP** → results must be **cached** and
  throttled.
- **appid is the universal join key** (owned games, `rgOwnedApps`, catalogue
  `data.dlc`), so ownership = `set(rgOwnedApps) ∩ set(game.data.dlc)`. Cast
  `fullgame.appid` (string) to int when joining.

### Existing structures this builds on

- **Vendor plug-in interface** (verified): a vendor module exposes `VENDOR_URL`,
  `SOURCE`, and `collect(page, captured) -> list[ScrapedGame]`. Registries to
  update: `scrapers/base.py` `VALID_SOURCES` (`:29`), `scrape_libraries.py`
  `SCRAPERS` dict + import, `scrape_service.py` `VENDORS` tuple (`:26`),
  `templates/base.html` vendor buttons (~`:150`), `app.py` `/api/scrape/start`
  validates against `scrape_service.VENDORS` (~`:1543`), `models.py` default
  platforms (~`:203`).
- `ScrapedGame` (`scrapers/base.py:62`): `title, platform, source, external_id,
  cover_url, source_title, status_hint, kind ("game"|"addon")`.
- The web pipeline `scrape_service._run_pipeline(conn, vendor, games)` runs import →
  enrich → ownership and returns a `summary`; `_run(vendor, ...)` drives
  `mod.VENDOR_URL` + `collect` with the manual-login handshake.
- **DLC tables (SP1):** `dlc(id, game_id, name, igdb_id, kind, owned, source,
  created_at, UNIQUE(game_id,name))` and `dlc_external_ids(id, dlc_id, source,
  external_id, source_title, UNIQUE(source, external_id), FK→dlc ON DELETE
  CASCADE)`. SP1's `dlc_ownership` reconciles by `(source, external_id)` →
  name-equality → else creates a row.
- `config.get_twitch_credentials()` reads `config.json` — the pattern to mirror for
  Steam creds.
- `import_scraped` imports only `kind=="game"` rows and routes `kind=="addon"` rows
  to ownership; platforms are auto-created on import from the `platform` short_name
  via `classify_platform` (so `"Steam"` → `pc` category).

## Auth & config

`config.json` gains two keys (the user's own); `config.example` and `.env.example`
get empty placeholders. New getter:

```python
def get_steam_credentials() -> tuple[str | None, str | None]:
    """(steam_api_key, steam_id64) from config.json; (None, None) if unset."""
```

If creds are missing, `collect` logs and skips the GetOwnedGames step (no games
returned) rather than crashing — symmetric with how DLC enrichment skips without
Twitch creds.

## Data flow / pipeline

```
collect (scrapers/steam.py), in the logged-in session:
  1. GetOwnedGames (requests, key+id64 from config) -> owned-game ScrapedGames
     kind="game", source="steam", platform="Steam", external_id=str(appid),
     title=name, cover_url=<Steam header capsule for appid>.
  2. page.request.get(dynamicstore/userdata) -> rgOwnedApps -> one id-only
     ScrapedGame(kind="addon", source="steam", external_id=str(appid)) per owned
     appid (the ownership carrier; title is the appid placeholder, never matched).
  -> returns the combined list.

_run_pipeline / import_scraped, when source == "steam":
  3. import_games(kind="game" rows)  -> Steam games enter the library.
  4. SKIP IGDB enrichment AND skip dlc_ownership.mark_ownership (those are for the
     title-based vendors). Instead:
  5. owned_app_ids = {int(r["external_id"]) for kind=="addon" rows}
  6. steam_dlc.enrich_and_mark(conn, owned_app_ids):
       for each Steam game (its game_external_ids row with source='steam' gives the
       appid): fetch appdetails(appid) -> data.dlc catalogue. For each catalogue
       dlc appid: get its name (appdetails, cached); reconcile-or-create a dlc row
       under the game and record (steam, dlc_appid) in dlc_external_ids; set
       owned=1 iff dlc_appid in owned_app_ids (0->1 only). Catalogue dlc not owned
       stay owned=0 (= missing).
```

**Routing:** the pipeline branches on `source`. Steam → step 4-6 (id-based
deep-fetch). PS/Xbox/Nintendo → the existing IGDB-enrich + `mark_ownership`
(title-based) path, unchanged. This keeps the title matcher from ever seeing
Steam's id-only addon carriers.

## `steam_dlc.py` — the catalogue + id-ownership engine

New module, mirroring `igdb_dlc.py`'s structure (pure parsers unit-tested; the one
network function isolated + cached + mockable).

- **Network (cached):** `fetch_appdetails(appid, *, cache_dir, session=requests) ->
  dict | None` — GET storefront `appdetails?appids=<appid>&l=english`; on success
  cache the `data` object to `<cache_dir>/<appid>.json` (gitignored `.steam_cache/`)
  and return it; on `success=false`/HTTP error return `None`. A small throttle
  (sleep) between live calls keeps under the rate limit; cache hits skip the call
  entirely. (Cache read/write is pure-ish and unit-testable with a temp dir.)
- **Pure parsers:** `parse_catalogue(data) -> list[int]` (`data.get("dlc") or []`);
  `parse_appdetails_name(data) -> str` (`data.get("name","").strip()`);
  `parse_type(data) -> str`.
- **Reconcile/create + ownership** (temp-DB tested):

  ```python
  def enrich_and_mark(conn, owned_app_ids: set[int], *, cache_dir,
                      session=requests) -> SteamReport
  ```

  Loads Steam games (`SELECT g.id, gx.external_id FROM games g JOIN
  game_external_ids gx ON gx.game_id=g.id WHERE gx.source='steam'`). For each:
  fetch catalogue; for each catalogue `dlc_appid`:
  1. **By id:** if `dlc_external_ids` has `(steam, dlc_appid)`, that's the row.
  2. **By name equality:** else if a `dlc` row under the game has a normalized name
     equal to the DLC's appdetails name, reconcile (attach `(steam, dlc_appid)`).
  3. **Create:** else insert a `dlc` row (`name`=appdetails name, `kind='dlc'`,
     `source='steam'`, `owned=0`) + its `dlc_external_ids` row.
  Then set `owned=1` on that row iff `dlc_appid in owned_app_ids` (only 0→1).
  Idempotent; a per-game network error is logged and skipped (never aborts the run),
  matching `igdb_dlc.enrich_missing`.

  `SteamReport` carries counts (`games`, `catalogue_added`, `owned_marked`,
  `errors`) plus `marked_items` (rows flipped owned this run, for the result list).

The name-equality reconcile reuses `models.normalize_title`; this prevents a
double-listed DLC if a row already exists from another source.

## Reporting / summary

`scrape_service._run_pipeline`, for Steam, maps `SteamReport` onto the existing
`summary` keys so the already-shipped UI keeps working unchanged:
`owned_marked` ← `SteamReport.owned_marked`; `created` ← `catalogue_added`;
`added_dlc` from the existing `created_at >= run_started` query (catches the
created catalogue rows with their owned flags); `newly_owned` from `marked_items`;
`review` is `[]` (id-based Steam has no uncertain-parent bucket). The hero
`/api/stats` `dlc_total`/`dlc_owned` tile reflects the new rows automatically.

## Registration checklist (every wiring point)

- `scrapers/base.py:29` — add `"steam"` to `VALID_SOURCES`.
- `scrapers/steam.py` — new module (`VENDOR_URL`, `SOURCE="steam"`, `collect`,
  pure parsers).
- `scrape_libraries.py` — import `steam`; add `"steam": steam` to `SCRAPERS`.
- `scrape_service.py:26` — add `"steam"` to `VENDORS`; add the Steam routing branch
  in `_run_pipeline`.
- `import_scraped.py` — add the same Steam routing branch in `main` (CLI parity).
- `templates/base.html` (~`:150`) — add a **Steam** button (`startScrape('steam')`).
- `models.py` (~`:203`) — add `("Steam", "Steam", "pc")` to default platforms.
- `config.py` — `get_steam_credentials`; `config.example`/`.env.example` placeholders.
- `tests/test_scrape_service.py` — update `test_vendors_constant`.
- `.gitignore` — add `.steam_cache/` (and confirm `config.json` already ignored).

## Data model

**No schema change.** Reuses `dlc` + `dlc_external_ids` (`source='steam'`,
`external_id=str(dlc_appid)`), `games` + `game_external_ids` (`source='steam'`,
`external_id=str(appid)`). The `"Steam"` platform (pc category) is added to
`init_db` defaults (also auto-created on import). `appdetails` responses cached in
gitignored `.steam_cache/`.

## Testing (offline)

Pure / unit (no network):
- `parse_owned_games(GetOwnedGames payload)` → game `ScrapedGame`s with
  `kind="game"`, `external_id=str(appid)`, `platform="Steam"`, cover capsule URL.
- `parse_userdata(userdata payload)` → id-only `kind="addon"` carriers from
  `rgOwnedApps`.
- `steam_dlc.parse_catalogue` / `parse_appdetails_name` / `parse_type` against
  appdetails fixtures (base game with `dlc[]`; a DLC with `fullgame`).

Temp-DB (HTTP mocked via an injected fake `fetch_appdetails`/session):
- `enrich_and_mark`: catalogue rows created (`source='steam'`, `owned=0`); owned
  appids flip the matching rows by id; unowned catalogue rows stay `owned=0`;
  **idempotent** re-run (no dup `dlc`/`dlc_external_ids`); reconciles to a
  pre-existing row by name without duplicating; a fetch error for one game is
  skipped, others still processed.
- `fetch_appdetails` caching: a cache hit does not call the (fake) network; a miss
  writes the cache file.

Pipeline routing (temp DB, mocked):
- `source="steam"` runs `steam_dlc.enrich_and_mark` and does **not** call IGDB
  enrichment or `dlc_ownership.mark_ownership`; `owned_app_ids` are correctly parsed
  from the addon carrier rows; the summary exposes `owned_marked`/`created`/
  `added_dlc`/`newly_owned`.
- A non-Steam vendor still takes the existing path (regression guard).

Registration/config:
- `scrape_service.VENDORS` and `scrapers.base.VALID_SOURCES` include `"steam"`.
- `config.get_steam_credentials` reads the two keys (and returns `(None, None)`
  when absent).

Existing suite stays green. The **live headed Steam scrape** (real login,
`GetOwnedGames`, `userdata`, live `appdetails`) is the owner's **manual**
verification — never run by agents or against the real `games.db`.

## Constraints

- Secrets via `config.json` (gitignored); maintain `config.example`/`.env.example`.
  Never commit `config.json`, `.steam_cache/`, `games.db`/`*.bak`, `.recon/`,
  `scraped/`, `.pw-profile/`, `.igdb_token.json`.
- Conventional commits, no co-author trailer. Work on `main`.
- `uv run python -m pytest` / `uv run ruff check` only (no `ruff format`; match the
  hand-aligned style).
- Network isolated behind the Steam adapter so the suite runs offline; back up
  `games.db` before any live run (the pipeline already does via
  `scrape_service.backup_db`).

## Out of scope (later)

- The **missing-DLC view / page** (catalogue − owned) — SP7.
- **Cross-vendor DLC dedup** (the same DLC owned on Steam *and* a console) — a later
  reconciliation concern; SP2's name-equality reconcile is the minimal guard.
- Rich Steam cover/artwork beyond the header capsule.
- Steam **Web API** owned-DLC probing (`appuserdetails`) — `rgOwnedApps` is
  sufficient; revisit only if the cache staleness proves a problem.
