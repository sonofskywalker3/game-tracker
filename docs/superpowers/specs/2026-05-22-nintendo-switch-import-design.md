# Nintendo / Switch library import — design

**Date:** 2026-05-22
**Branch:** `feature/library-scraping-import`
**Status:** approach approved; recon complete; ready to implement Phase B

## Goal

Bring Switch into the same scrape → JSON → import pipeline as PlayStation and
Xbox: catch Switch purchases newer than the original CSV import and attach a
rename-proof `nintendo` external id to the existing library.

Current state in `games.db`: **306 Switch games** (short_name `Switch`,
`Nintendo Switch`), **0** `nintendo` external ids. The win is (1) attach ids to
the existing 306 by title match, (2) add genuinely-new purchases.

Downstream is already done and must not be rebuilt: `import_scraped.py` (match
cascade external_id → normalized title → fuzzy → new; idempotent; `is_non_game`
filter; `--dry-run`/`--auto-fuzzy`), `scrapers/base.py` helpers, and
`scrape_libraries.py` (`--vendor`, `--recon`). `nintendo` is already a valid
source; `Switch` already maps to `Nintendo Switch` in `PLATFORM_DISPLAY_NAMES`.
The only missing piece is `scrapers/nintendo.py` `collect()` + a pure parser.

## Decision

Target **true purchase / transaction history** (web-session only), not play
activity. Honest cost: only ~2 years back (fine — "newer purchases"; the old 306
are already imported).

## Phase A — beat the login: RESOLVED (Rung 0)

Adding `ignore_default_args=["--enable-automation"]` to the persistent-context
launch (`scrapers/base.py`) was sufficient. Manual login now succeeds (passkey +
reCAPTCHA pass) and recon captured authenticated traffic. The CDN error page
(`400 invalid params`) is gone. CDP-attach (Rung 1) is not needed.

## Recon findings (`.recon/nintendo.responses.jsonl`, 45 items / 3 pages)

- **Endpoint:** `https://graph.nintendo.com/` GraphQL GET, persisted query.
  - `operationName=CustomerOrderHistory`
  - `extensions.persistedQuery.sha256Hash =
    b77d54b84f1820a9401dd46915771243abafc2f69c1539a9fc34ff46f096d0b7`
    (may change if Nintendo updates the web app; refresh from a new recon if a
    request 400s on persisted-query-not-found — same caveat as PSN's hash)
  - **Pagination:** `variables = {"includeTotals": true, "personalized": false,
    "page": N}`, 15 orders/page; loop `page` 1→N until a page has no orders.
    `totals` reports `digital`/`physical` counts (352 total here).
- **Response path:** `data.customer.orderHistory.orders[]`; each order has
  `items[]` (one item per order in the sample, but parse iterates all).
- **Per item fields:**
  - `id` = **NSUID** (e.g. `70050000073422`) → `external_id` (stable per product)
  - `product.name` → `title` / `source_title`
  - `product.platform.code` → `NINTENDO_SWITCH` or `NINTENDO_SWITCH_2`
  - `product.productImage.publicId` → Cloudinary cover (see below)
- **Auth:** header-carried (`authorization` bearer, `x-access-token`,
  `x-customer-token`, `apollographql-client-name/version`, `x-nintendo-graph`,
  `content-type`, `origin`, `referer`, `locale`). Replay via
  `auth_from_captured(captured, "CustomerOrderHistory")` → `replay_headers`;
  the context cookie jar rides along on `page.request`. Identical to PSN.

## DLC filter — deterministic by NSUID prefix

NSUID prefix perfectly partitions content type in the sample:

| Prefix | n  | Type | Action |
|--------|----|------|--------|
| 7001   | 30 | base game / standalone software | keep |
| 7005   | 10 | DLC / upgrade pack / soundtrack (10/10) | **skip** |
| 7007   | 5  | bundle / collection | keep |

The parser skips NSUIDs whose prefix is `7005` (DLC is out of scope). The
downstream `is_non_game` name filter is the backstop for anything the prefix
misses. `--dry-run` lets the user review filtered/new before committing.

## Platform mapping — fold Switch 2 into Switch

Both `NINTENDO_SWITCH` and `NINTENDO_SWITCH_2` → `platform="Switch"` (user
decision). Keeps one Nintendo platform, matches the existing 306 cleanly, no new
platform row.

## Cover URL

`https://assets.nintendo.com/image/upload/{publicId}` from
`product.productImage.publicId`. Non-critical; `None` if the image block is
absent.

## `scrapers/nintendo.py` shape (mirror `xbox.py`)

- `VENDOR_URL` — a Nintendo entry point so recon/scrape can start (user logs in
  and navigates to Transaction History manually via `_wait_for_user`).
- `parse_orders(responses: list[dict]) -> list[ScrapedGame]` — pure; pull
  `data.customer.orderHistory.orders`, iterate items, skip 7005 / missing name /
  missing id, dedup by NSUID, map fields. Unit-tested against a **sanitized**
  fixture (`tests/fixtures/nintendo_orders_sample.json`).
- `collect(page, captured) -> list[ScrapedGame]` — `auth_from_captured` (else
  `capture_request_headers` on reload), then GET the GraphQL op per page until
  empty; `MAX_PAGES` + `REQUEST_DELAY_MS` guards like xbox/PS. Live shell,
  verified manually, not unit-tested.

`platform="Switch"`, `source="nintendo"` throughout.

## Testing

- `parse_orders` unit-tested on a sanitized fixture: a 7001 base game
  (`NINTENDO_SWITCH`), a 7001 base game (`NINTENDO_SWITCH_2`, folds to `Switch`),
  a 7005 DLC (must be skipped), a 7007 bundle (kept), an item missing name/id
  (skipped), and a duplicate NSUID across orders (deduped). No real account
  data.
- `collect()` verified manually via a live scrape + `import_scraped.py --dry-run`.
- Existing 34 tests stay green.

## Constraints

- Public repo: never commit `games.db`, `games.db.bak*`, `.recon/`, `scraped/`,
  `.pw-profile/`, `config.json` (all gitignored — verify before any push).
- Conventional commit style, no co-author trailer.
- Out of scope: legacy/older consoles, DLC-to-parent association.
