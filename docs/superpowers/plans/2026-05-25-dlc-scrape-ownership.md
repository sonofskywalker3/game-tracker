# DLC Scrape-Driven Ownership (Piece 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture owned vendor add-ons during a scrape and flip `owned = 1` on the matching IGDB-sourced `dlc` rows, holding ambiguous matches for review and never inventing rows.

**Architecture:** Scrapers tag add-ons with a new `ScrapedGame.kind="addon"` instead of dropping them. A new pure module `dlc_ownership.py` matches an add-on title to its parent library game (longest normalized-title prefix) and then to a specific DLC row under that parent (normalized equality → containment). `import_scraped` partitions each scrape file into games vs add-ons, imports + IGDB-enriches games first, then runs ownership matching. Matching only ever sets `owned` 0→1 and is idempotent.

**Tech Stack:** Python 3, sqlite3, pytest, `uv` for env/deps, `ruff` for lint/format. No new dependencies.

Spec: `docs/superpowers/specs/2026-05-25-dlc-scrape-ownership-design.md`.

---

## File Structure

- **Create** `dlc_ownership.py` — pure matching helpers (`parent_of`, `match_dlc`, `classify`) + the `mark_ownership` orchestrator + `Match` / `OwnershipReport` dataclasses. One responsibility: turn scraped add-ons into `owned` flips.
- **Modify** `scrapers/base.py` — add `kind` field to `ScrapedGame`.
- **Modify** `scrapers/nintendo.py` — replace `is_game_nsuid` with `classify_nsuid`; emit `7005` items as `kind="addon"`.
- **Modify** `scrapers/xbox.py` — emit `Durable`/`Consumable` items as `kind="addon"`.
- **Modify** `scrapers/playstation.py` — add a no-op `collect_addons` stub (PSN add-on capture is blocked on a live recon; see Task 9).
- **Modify** `import_scraped.py` — partition games vs add-ons; run `mark_ownership` after enrichment; add `--no-ownership` / `--include-flagged`; log an ownership summary.
- **Create/Modify** tests under `tests/` per task.

---

## Task 1: Add `kind` field to `ScrapedGame`

**Files:**
- Modify: `scrapers/base.py:61-74`
- Test: `tests/test_scraper_base.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_scraper_base.py`:

```python
from dataclasses import asdict

from scrapers.base import ScrapedGame


def test_scrapedgame_kind_defaults_to_game():
    g = ScrapedGame(title="X", platform="Switch", source="nintendo")
    assert g.kind == "game"
    assert asdict(g)["kind"] == "game"


def test_scrapedgame_kind_addon_round_trips():
    g = ScrapedGame(title="X - Season Pass", platform="Xbox", source="xbox", kind="addon")
    assert asdict(g)["kind"] == "addon"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scraper_base.py -k kind -v`
Expected: FAIL (`TypeError: __init__() got an unexpected keyword argument 'kind'` / `AttributeError`).

- [ ] **Step 3: Add the field**

In `scrapers/base.py`, add `kind` to the dataclass (after `status_hint`):

```python
@dataclass
class ScrapedGame:
    """One owned title as seen on a vendor library page."""
    title: str
    platform: str                       # canonical short_name, e.g. "PS5", "X360"
    source: str                         # one of VALID_SOURCES
    external_id: Optional[str] = None
    cover_url: Optional[str] = None
    source_title: Optional[str] = None  # exact vendor title; defaults to title
    status_hint: Optional[str] = None
    kind: str = "game"                  # "game" | "addon" (add-ons attach to a game's DLC)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_scraper_base.py -k kind -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scrapers/base.py tests/test_scraper_base.py
git commit -m "feat: ScrapedGame.kind field (game|addon)"
```

---

## Task 2: Nintendo — classify NSUIDs and emit add-ons

**Files:**
- Modify: `scrapers/nintendo.py:46-105`
- Test: `tests/test_parse_nintendo.py`

The fixture already has `7005` add-on NSUID `70050000000003` ("Sample Game - Nintendo Switch 2 Edition Upgrade Pack") and hardware id `120833`.

- [ ] **Step 1: Update the tests to expect add-ons**

In `tests/test_parse_nintendo.py`, replace `test_keeps_games_skips_dlc_hardware_and_malformed` and `test_is_game_nsuid_excludes_dlc_and_hardware` with:

```python
from scrapers.nintendo import classify_nsuid, parse_orders


def test_keeps_games_emits_addons_skips_hardware_and_malformed():
    by_kind = {}
    for g in parse_orders([_body()]):
        by_kind.setdefault(g.kind, set()).add(g.title)
    assert by_kind["game"] == {"Sample Switch Game", "Sample Switch 2 Game", "Sample Collection"}
    assert by_kind["addon"] == {"Sample Game - Nintendo Switch 2 Edition Upgrade Pack"}
    assert "Nintendo GameCube Controller" not in by_kind["game"]


def test_classify_nsuid():
    assert classify_nsuid("70010000000001") == "game"   # base game
    assert classify_nsuid("70070000000004") == "game"    # bundle
    assert classify_nsuid("70050000000003") == "addon"   # DLC (7005)
    assert classify_nsuid("120833") is None              # hardware (short non-NSUID id)
    assert classify_nsuid("") is None
    assert classify_nsuid(None) is None
```

(Leave `test_maps_fields_and_nsuid_id`, `test_switch_2_folds_into_switch`, `test_cover_url_always_none`, and `test_dedups_nsuid_across_overlapping_orders` as-is — they exercise the `game` rows and still hold.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_parse_nintendo.py -v`
Expected: FAIL (`ImportError: cannot import name 'classify_nsuid'`).

- [ ] **Step 3: Implement `classify_nsuid` and emit add-ons**

In `scrapers/nintendo.py`, replace the `SKIP_NSUID_PREFIXES` block and `is_game_nsuid` with:

```python
# NSUID prefix -> content type. 7005 is add-on content (DLC / upgrade packs /
# soundtracks): kept as kind="addon" so it can mark DLC ownership. 7001 (base
# games) and 7007 (bundles/collections) are kind="game". classify_nsuid is the
# prefix gate; is_non_game is the downstream name-based backstop for games.
ADDON_NSUID_PREFIXES = frozenset({"7005"})
NSUID_PREFIX_LEN = 4
# Real software NSUIDs are 14-digit ids starting "700". Physical hardware/merch
# (GameCube controller, dock, Virtual Boy headset, etc.) use short non-NSUID
# product ids (e.g. 6-digit), so requiring a real NSUID skips them.
NSUID_LEN = 14
NSUID_GAME_PREFIX = "700"


def classify_nsuid(nsuid: str | None) -> str | None:
    """Classify a Nintendo product id: "game" (base/bundle), "addon" (7005 DLC),
    or None for non-game hardware/merch (short non-NSUID product ids)."""
    if not nsuid or len(nsuid) != NSUID_LEN or not nsuid.isdigit():
        return None
    if not nsuid.startswith(NSUID_GAME_PREFIX):
        return None
    return "addon" if nsuid[:NSUID_PREFIX_LEN] in ADDON_NSUID_PREFIXES else "game"
```

Then update `parse_orders` (the per-item loop):

```python
            for item in order.get("items") or []:
                nsuid = item.get("id")
                product = item.get("product") or {}
                name = product.get("name")
                kind = classify_nsuid(nsuid)
                if not name or kind is None:
                    continue
                if nsuid in seen:
                    continue
                seen.add(nsuid)
                # cover_url is left None: Nintendo's productImage is wide hero art
                # (~1920x1080), the wrong aspect for box art. The IGDB pipeline
                # (fetch_covers.py) supplies covers; see docs cover-art-igdb spec.
                games.append(ScrapedGame(
                    title=name,
                    platform=PLATFORM,
                    source=SOURCE,
                    external_id=nsuid,
                    cover_url=None,
                    source_title=name,
                    kind=kind,
                ))
```

Also update the `parse_orders` docstring line "Skips DLC (NSUID prefix 7005)..." to "Emits `7005` add-on NSUIDs as `kind='addon'`; skips hardware/merch (non-NSUID ids), items missing a name or NSUID, and duplicate NSUIDs."

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_parse_nintendo.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add scrapers/nintendo.py tests/test_parse_nintendo.py
git commit -m "feat: nintendo scraper emits 7005 add-ons as kind=addon"
```

---

## Task 3: Xbox — emit Durable/Consumable add-ons

**Files:**
- Modify: `scrapers/xbox.py:25-63`
- Test: `tests/test_parse_xbox.py`

The fixture already has a `Durable` add-on `9PDLC0001` ("Sample Quest - Season Pass") and a `Subscription` ("Game Pass Ultimate").

- [ ] **Step 1: Update the tests to expect add-ons**

In `tests/test_parse_xbox.py`, replace `test_extracts_only_games` with:

```python
def test_extracts_games_and_addons_skips_subscriptions():
    by_kind = {}
    for g in parse_orders([_body()]):
        by_kind.setdefault(g.kind, set()).add(g.title)
    assert by_kind["game"] == {"Sample Quest", "Another Game"}
    assert by_kind["addon"] == {"Sample Quest - Season Pass"}
    assert "Game Pass Ultimate" not in by_kind.get("game", set())
    assert "Game Pass Ultimate" not in by_kind.get("addon", set())
```

(Leave `test_maps_fields` and `test_dedups_across_overlapping_responses` as-is — they exercise `game` rows.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_parse_xbox.py -v`
Expected: FAIL (`KeyError: 'addon'` — add-ons not emitted yet).

- [ ] **Step 3: Implement add-on item types**

In `scrapers/xbox.py`, replace the `GAME_ITEM_TYPE = "Game"` line with:

```python
GAME_ITEM_TYPE = "Game"
# Purchasable add-on content types we attach to a game's DLC ownership. Other
# types (e.g. "Subscription" for Game Pass) are skipped entirely.
ADDON_ITEM_TYPES = frozenset({"Durable", "Consumable"})


def _kind_for(item_type: str | None) -> str | None:
    """Map an order item's itemTypeName to "game", "addon", or None (skip)."""
    if item_type == GAME_ITEM_TYPE:
        return "game"
    if item_type in ADDON_ITEM_TYPES:
        return "addon"
    return None
```

Then update `parse_orders` (the per-item loop):

```python
            for item in order.get("items", []):
                kind = _kind_for(item.get("itemTypeName"))
                if kind is None:
                    continue
                title = item.get("localTitle")
                product_id = item.get("productId")
                if not title or not product_id or product_id in seen:
                    continue
                seen.add(product_id)
                games.append(ScrapedGame(
                    title=title,
                    platform=PLATFORM,
                    source=SOURCE,
                    external_id=product_id,
                    cover_url=item.get("logoLink"),
                    source_title=title,
                    kind=kind,
                ))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_parse_xbox.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scrapers/xbox.py tests/test_parse_xbox.py
git commit -m "feat: xbox scraper emits Durable/Consumable add-ons as kind=addon"
```

---

## Task 4: `dlc_ownership.parent_of` — resolve the parent game

**Files:**
- Create: `dlc_ownership.py`
- Test: `tests/test_dlc_ownership.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_dlc_ownership.py`:

```python
import dlc_ownership as own
import models


def _lib(*titles):
    """Build a [(game_id, normalized_title)] library from display titles."""
    return [(i + 1, models.normalize_title(t)) for i, t in enumerate(titles)]


def test_parent_of_exact_prefix():
    lib = _lib("The Witcher 3: Wild Hunt", "Other Game")
    assert own.parent_of("The Witcher 3: Wild Hunt - Hearts of Stone", lib) == 1


def test_parent_of_longest_prefix_wins():
    lib = _lib("Final Fantasy", "Final Fantasy XV")
    # game_id 2 ("final fantasy xv") is the longer prefix of the add-on
    assert own.parent_of("Final Fantasy XV - Episode Ardyn", lib) == 2


def test_parent_of_no_prefix_is_none():
    lib = _lib("Hades", "Celeste")
    assert own.parent_of("Stardew Valley - Some Pack", lib) is None


def test_parent_of_cross_game_tie_is_ambiguous():
    # Two different games normalize to the same prefix string.
    lib = [(1, "spirit"), (2, "spirit")]
    assert own.parent_of("Spirit Extra Pack", lib) is own.AMBIGUOUS


def test_parent_of_exact_title_match():
    lib = _lib("Celeste")
    assert own.parent_of("Celeste", lib) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_dlc_ownership.py -k parent_of -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'dlc_ownership'`).

- [ ] **Step 3: Create the module with `_norm` and `parent_of`**

Create `dlc_ownership.py`:

```python
"""Match scraped owned add-ons to existing IGDB-sourced dlc rows and flip owned.

Pure helpers (parent_of, match_dlc, classify) are unit-tested; mark_ownership
orchestrates against a (temp or live) connection. Ownership is only ever set
0 -> 1, never 1 -> 0; the pass is idempotent. Add-ons that match no existing dlc
row are reported, never inserted (the dlc list stays IGDB-curated). See
docs/superpowers/specs/2026-05-25-dlc-scrape-ownership-design.md.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field

import models

logger = logging.getLogger(__name__)

# Sentinel: more than one equally-plausible match (parent or dlc). Distinct from
# None ("no match at all").
AMBIGUOUS = "__ambiguous__"


def _norm(title: str | None) -> str:
    """Normalized match key, matching how games.normalized_title is stored
    (normalize_title(clean_title(...)))."""
    return models.normalize_title(models.clean_title(title or ""))


def parent_of(addon_title: str, library: list[tuple[int, str]]):
    """Resolve an add-on's parent game by longest normalized-title prefix.

    `library` is [(game_id, normalized_title)]. Returns the game_id, None (no
    prefix matched), or AMBIGUOUS (the longest match is a tie across different
    game_ids). A normalized game title matches when it equals the normalized
    add-on title or is a whole-word prefix of it.
    """
    addon = _norm(addon_title)
    if not addon:
        return None
    best_len = 0
    winners: set[int] = set()
    for game_id, gnorm in library:
        if not gnorm:
            continue
        if addon == gnorm or addon.startswith(gnorm + " "):
            if len(gnorm) > best_len:
                best_len, winners = len(gnorm), {game_id}
            elif len(gnorm) == best_len:
                winners.add(game_id)
    if not winners:
        return None
    if len(winners) > 1:
        return AMBIGUOUS
    return next(iter(winners))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_dlc_ownership.py -k parent_of -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dlc_ownership.py tests/test_dlc_ownership.py
git commit -m "feat: dlc_ownership.parent_of resolves add-on parent by prefix"
```

---

## Task 5: `dlc_ownership.match_dlc` and `_remainder`

**Files:**
- Modify: `dlc_ownership.py`
- Test: `tests/test_dlc_ownership.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_dlc_ownership.py`:

```python
def test_remainder_strips_parent_prefix():
    assert own._remainder("The Witcher 3 - Hearts of Stone", "the witcher 3") == "hearts of stone"
    assert own._remainder("Celeste", "celeste") == ""


def test_match_dlc_equality():
    rows = [(10, "Hearts of Stone"), (11, "Blood and Wine")]
    assert own.match_dlc("hearts of stone", rows) == (10, "equality")


def test_match_dlc_containment():
    rows = [(10, "Hearts of Stone")]
    # add-on remainder carries extra words around the dlc name
    assert own.match_dlc("hearts of stone expansion", rows) == (10, "containment")


def test_match_dlc_multiple_equality_is_ambiguous():
    rows = [(10, "Season Pass"), (11, "Season Pass")]
    assert own.match_dlc("season pass", rows) == (own.AMBIGUOUS, "equality")


def test_match_dlc_none():
    rows = [(10, "Hearts of Stone")]
    assert own.match_dlc("totally different", rows) == (None, None)


def test_match_dlc_empty_remainder():
    assert own.match_dlc("", [(10, "X")]) == (None, None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_dlc_ownership.py -k "remainder or match_dlc" -v`
Expected: FAIL (`AttributeError: module 'dlc_ownership' has no attribute '_remainder'`).

- [ ] **Step 3: Implement `_remainder`, `_contains_words`, `match_dlc`**

Append to `dlc_ownership.py` (after `parent_of`):

```python
def _remainder(addon_title: str, parent_norm: str) -> str:
    """The normalized add-on title with the parent's normalized prefix removed."""
    addon = _norm(addon_title)
    if addon == parent_norm:
        return ""
    prefix = parent_norm + " "
    if addon.startswith(prefix):
        return addon[len(prefix):]
    return addon


def _contains_words(haystack: str, needle: str) -> bool:
    """True if `needle` occurs as a whole-word run inside `haystack`.

    Both args are already normalized (lowercase, single-spaced)."""
    if not needle or not haystack:
        return False
    return f" {needle} " in f" {haystack} "


def match_dlc(remainder: str, dlc_rows: list[tuple[int, str]]):
    """Match an add-on remainder to one of a parent's dlc rows.

    Returns (result, method): result is a dlc_id, None, or AMBIGUOUS; method is
    "equality", "containment", or None. Equality on normalized names is tried
    first; then whole-word containment in either direction. `dlc_rows` is
    [(dlc_id, name)].
    """
    rem = (remainder or "").strip()
    if not rem:
        return None, None
    norm = [(dlc_id, models.normalize_title(name)) for dlc_id, name in dlc_rows]
    equal = [dlc_id for dlc_id, n in norm if n == rem]
    if len(equal) == 1:
        return equal[0], "equality"
    if len(equal) > 1:
        return AMBIGUOUS, "equality"
    contained = [dlc_id for dlc_id, n in norm
                 if _contains_words(rem, n) or _contains_words(n, rem)]
    if len(contained) == 1:
        return contained[0], "containment"
    if len(contained) > 1:
        return AMBIGUOUS, "containment"
    return None, None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_dlc_ownership.py -k "remainder or match_dlc" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dlc_ownership.py tests/test_dlc_ownership.py
git commit -m "feat: dlc_ownership.match_dlc (equality then containment)"
```

---

## Task 6: `dlc_ownership.classify` and the `Match` dataclass

**Files:**
- Modify: `dlc_ownership.py`
- Test: `tests/test_dlc_ownership.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_dlc_ownership.py`:

```python
def test_classify_apply_on_unique_parent_and_equality():
    lib = _lib("The Witcher 3: Wild Hunt")
    dlc_by_game = {1: [(10, "Hearts of Stone")]}
    m = own.classify("The Witcher 3: Wild Hunt - Hearts of Stone", lib, dlc_by_game)
    assert m.action == "apply" and m.game_id == 1 and m.dlc_id == 10


def test_classify_hold_on_containment_only():
    lib = _lib("The Witcher 3: Wild Hunt")
    dlc_by_game = {1: [(10, "Hearts of Stone")]}
    m = own.classify("The Witcher 3: Wild Hunt - Hearts of Stone Expansion", lib, dlc_by_game)
    assert m.action == "hold" and m.dlc_id == 10


def test_classify_hold_on_ambiguous_parent():
    lib = [(1, "spirit"), (2, "spirit")]
    m = own.classify("Spirit Extra Pack", lib, {1: [(10, "extra pack")]})
    assert m.action == "hold" and m.game_id is None


def test_classify_unmatched_no_parent():
    m = own.classify("Stardew Valley - Pack", _lib("Hades"), {})
    assert m.action == "unmatched" and m.game_id is None


def test_classify_unmatched_parent_without_dlc():
    lib = _lib("Hades")
    m = own.classify("Hades - Soundtrack", lib, {})
    assert m.action == "unmatched" and m.game_id == 1


def test_classify_unmatched_no_dlc_name_match():
    lib = _lib("Hades")
    m = own.classify("Hades - Soundtrack", lib, {1: [(10, "Cosmetic Pack")]})
    assert m.action == "unmatched" and m.game_id == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_dlc_ownership.py -k classify -v`
Expected: FAIL (`AttributeError: module 'dlc_ownership' has no attribute 'classify'`).

- [ ] **Step 3: Implement `Match` and `classify`**

Append to `dlc_ownership.py` (after `match_dlc`):

```python
@dataclass
class Match:
    """The matching verdict for one scraped add-on."""
    action: str               # "apply" | "hold" | "unmatched"
    addon_title: str
    game_id: int | None = None
    dlc_id: int | None = None
    reason: str = ""


def classify(addon_title: str, library: list[tuple[int, str]],
             dlc_by_game: dict[int, list[tuple[int, str]]]) -> Match:
    """Decide whether an add-on should apply, hold, or be reported unmatched.

    apply  = parent resolves uniquely AND a dlc name matches by equality.
    hold   = ambiguous parent/dlc, or a containment-only dlc match (plausible,
             not certain) — never auto-applied without include_flagged.
    unmatched = no parent, or parent has no matching dlc row.
    """
    parent = parent_of(addon_title, library)
    if parent is None:
        return Match("unmatched", addon_title, reason="no parent game")
    if parent is AMBIGUOUS:
        return Match("hold", addon_title, reason="ambiguous parent")
    rows = dlc_by_game.get(parent) or []
    if not rows:
        return Match("unmatched", addon_title, game_id=parent, reason="parent has no dlc")
    parent_norm = next(gnorm for gid, gnorm in library if gid == parent)
    result, method = match_dlc(_remainder(addon_title, parent_norm), rows)
    if result is None:
        return Match("unmatched", addon_title, game_id=parent, reason="no dlc name match")
    if result is AMBIGUOUS:
        return Match("hold", addon_title, game_id=parent, reason="ambiguous dlc")
    if method == "equality":
        return Match("apply", addon_title, game_id=parent, dlc_id=result)
    return Match("hold", addon_title, game_id=parent, dlc_id=result, reason="containment only")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_dlc_ownership.py -k classify -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dlc_ownership.py tests/test_dlc_ownership.py
git commit -m "feat: dlc_ownership.classify (apply/hold/unmatched verdicts)"
```

---

## Task 7: `mark_ownership` orchestrator + `OwnershipReport`

**Files:**
- Modify: `dlc_ownership.py`
- Test: `tests/test_dlc_ownership.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_dlc_ownership.py`:

```python
def _seed(conn, title="The Witcher 3: Wild Hunt", dlc_names=("Hearts of Stone",)):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT id FROM games WHERE title=?", (title,)).fetchone()[0]
    for name in dlc_names:
        conn.execute("INSERT INTO dlc (game_id, name, source) VALUES (?, ?, 'igdb')", (gid, name))
    return gid


def test_mark_ownership_applies_equality_match(temp_db):
    conn = models.get_db()
    gid = _seed(conn)
    conn.commit()
    rep = own.mark_ownership(conn, [{"title": "The Witcher 3: Wild Hunt - Hearts of Stone"}])
    conn.commit()
    assert rep.marked == 1 and not rep.held and not rep.unmatched
    owned = conn.execute("SELECT owned FROM dlc WHERE game_id=? AND name='Hearts of Stone'",
                         (gid,)).fetchone()[0]
    assert owned == 1
    conn.close()


def test_mark_ownership_holds_unless_include_flagged(temp_db):
    conn = models.get_db()
    _seed(conn)
    conn.commit()
    addon = {"title": "The Witcher 3: Wild Hunt - Hearts of Stone Expansion"}  # containment only
    rep = own.mark_ownership(conn, [addon])
    assert rep.marked == 0 and len(rep.held) == 1
    assert conn.execute("SELECT owned FROM dlc").fetchone()[0] == 0
    rep2 = own.mark_ownership(conn, [addon], include_flagged=True)
    conn.commit()
    assert rep2.marked == 1
    assert conn.execute("SELECT owned FROM dlc").fetchone()[0] == 1
    conn.close()


def test_mark_ownership_reports_unmatched_creates_nothing(temp_db):
    conn = models.get_db()
    _seed(conn)
    conn.commit()
    rep = own.mark_ownership(conn, [{"title": "The Witcher 3: Wild Hunt - Soundtrack"}])
    assert rep.marked == 0 and len(rep.unmatched) == 1
    assert conn.execute("SELECT COUNT(*) FROM dlc").fetchone()[0] == 1  # nothing inserted
    conn.close()


def test_mark_ownership_dry_run_writes_nothing(temp_db):
    conn = models.get_db()
    _seed(conn)
    conn.commit()
    rep = own.mark_ownership(conn, [{"title": "The Witcher 3: Wild Hunt - Hearts of Stone"}],
                             dry_run=True)
    assert rep.marked == 1
    assert conn.execute("SELECT owned FROM dlc").fetchone()[0] == 0  # not written
    conn.close()


def test_mark_ownership_idempotent(temp_db):
    conn = models.get_db()
    _seed(conn)
    conn.commit()
    addon = {"title": "The Witcher 3: Wild Hunt - Hearts of Stone"}
    own.mark_ownership(conn, [addon]); conn.commit()
    rep = own.mark_ownership(conn, [addon]); conn.commit()
    assert rep.marked == 0 and rep.already_owned == 1
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_dlc_ownership.py -k mark_ownership -v`
Expected: FAIL (`AttributeError: module 'dlc_ownership' has no attribute 'mark_ownership'`).

- [ ] **Step 3: Implement `OwnershipReport` and `mark_ownership`**

Append to `dlc_ownership.py`:

```python
@dataclass
class OwnershipReport:
    """Outcome counts + the held/unmatched lists for manual review."""
    marked: int = 0
    already_owned: int = 0
    held: list[Match] = field(default_factory=list)
    unmatched: list[Match] = field(default_factory=list)


def mark_ownership(conn: sqlite3.Connection, addons, *, dry_run: bool = False,
                   include_flagged: bool = False) -> OwnershipReport:
    """Flip dlc.owned for scraped owned add-ons (0 -> 1 only; idempotent).

    `addons` is an iterable of dicts (scrape rows) or objects with a `.title`.
    Applies "apply" verdicts always, and "hold" verdicts only when
    include_flagged. Reports held/unmatched for review; inserts nothing. Writes
    nothing when dry_run (the caller owns commit).
    """
    library = [(r["id"], r["normalized_title"])
               for r in conn.execute("SELECT id, normalized_title FROM games")]
    dlc_by_game: dict[int, list[tuple[int, str]]] = {}
    for r in conn.execute("SELECT id, game_id, name FROM dlc"):
        dlc_by_game.setdefault(r["game_id"], []).append((r["id"], r["name"]))

    report = OwnershipReport()
    for addon in addons:
        title = addon["title"] if isinstance(addon, dict) else addon.title
        m = classify(title, library, dlc_by_game)
        apply_it = m.action == "apply" or (
            m.action == "hold" and include_flagged and m.dlc_id is not None)
        if apply_it:
            owned = conn.execute("SELECT owned FROM dlc WHERE id = ?", (m.dlc_id,)).fetchone()[0]
            if owned:
                report.already_owned += 1
            else:
                report.marked += 1
                if not dry_run:
                    conn.execute("UPDATE dlc SET owned = 1 WHERE id = ?", (m.dlc_id,))
        elif m.action == "hold":
            report.held.append(m)
        elif m.action == "unmatched":
            report.unmatched.append(m)
    return report
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_dlc_ownership.py -v`
Expected: PASS (whole file).

- [ ] **Step 5: Commit**

```bash
git add dlc_ownership.py tests/test_dlc_ownership.py
git commit -m "feat: dlc_ownership.mark_ownership orchestrator + report"
```

---

## Task 8: Wire ownership into `import_scraped`

**Files:**
- Modify: `import_scraped.py:563-631` (CLI/main), plus a new `_log_ownership` helper
- Test: `tests/test_import_scraped.py`

- [ ] **Step 1: Write the failing integration test**

Add to `tests/test_import_scraped.py` (top-level imports `import import_scraped`, `import models`; add `import dlc_ownership` if not present):

```python
def test_partition_imports_games_and_marks_addon_ownership(temp_db, monkeypatch):
    conn = models.get_db()
    # Existing game with an IGDB-sourced DLC row already present.
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 ("The Witcher 3: Wild Hunt", models.normalize_title("The Witcher 3: Wild Hunt")))
    gid = conn.execute("SELECT id FROM games WHERE title LIKE 'The Witcher%'").fetchone()[0]
    conn.execute("INSERT INTO dlc (game_id, name, source) VALUES (?, 'Hearts of Stone', 'igdb')", (gid,))
    conn.commit()
    conn.close()

    games = [
        {"title": "The Witcher 3: Wild Hunt", "platform": "PS5", "source": "playstation",
         "external_id": "G1", "kind": "game"},
        {"title": "The Witcher 3: Wild Hunt - Hearts of Stone", "platform": "PS5",
         "source": "playstation", "external_id": "A1", "kind": "addon"},
    ]
    games_only = [g for g in games if g.get("kind", "game") == "game"]
    addons = [g for g in games if g.get("kind") == "addon"]

    # import_games must only see the base game (the partition is the unit under test here).
    stats = import_scraped.import_games(conn := models.get_db(), games_only, "playstation",
                                        confirm_fn=import_scraped._auto_confirm)
    report = dlc_ownership.mark_ownership(conn, addons)
    conn.commit()
    assert report.marked == 1
    owned = conn.execute("SELECT owned FROM dlc WHERE name='Hearts of Stone'").fetchone()[0]
    assert owned == 1
    conn.close()
```

Also add a CLI-level test that exercises `main`'s partition + ownership end to end with enrichment mocked:

```python
def test_main_runs_ownership_after_enrichment(temp_db, monkeypatch, tmp_path, caplog):
    # Mock IGDB enrichment so importing the base game populates a DLC row.
    import igdb_dlc

    def fake_enrich_missing(conn, *, client_id, token):
        for (gid,) in conn.execute("SELECT id FROM games WHERE igdb_id IS NULL").fetchall():
            conn.execute("UPDATE games SET igdb_id = 1 WHERE id = ?", (gid,))
            conn.execute("INSERT OR IGNORE INTO dlc (game_id, name, source) "
                         "VALUES (?, 'Hearts of Stone', 'igdb')", (gid,))
        conn.commit()
        return {"games": 1, "matched": 1, "added": 1, "errors": 0}

    monkeypatch.setattr(import_scraped, "get_db", models.get_db, raising=False)
    monkeypatch.setattr(igdb_dlc, "enrich_missing", fake_enrich_missing)
    monkeypatch.setattr("config.get_twitch_credentials", lambda: ("cid", "secret"))
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda c, s: "tok")

    scrape = tmp_path / "playstation_20260525.json"
    scrape.write_text(json.dumps({"source": "playstation", "games": [
        {"title": "The Witcher 3: Wild Hunt", "platform": "PS5", "source": "playstation",
         "external_id": "G1", "kind": "game"},
        {"title": "The Witcher 3: Wild Hunt - Hearts of Stone", "platform": "PS5",
         "source": "playstation", "external_id": "A1", "kind": "addon"},
    ]}), encoding="utf-8")

    import_scraped.main([str(scrape), "--auto-fuzzy"])
    conn = models.get_db()
    owned = conn.execute("SELECT owned FROM dlc WHERE name='Hearts of Stone'").fetchone()[0]
    assert owned == 1
    conn.close()
```

(If `test_import_scraped.py` lacks `import json` / `import dlc_ownership` / `import igdb_dlc`, add them at the top.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_import_scraped.py -k "ownership" -v`
Expected: FAIL — the first test fails only if `dlc_ownership` import is missing; the `main` test fails because `main` does not yet partition add-ons or call `mark_ownership` (DLC stays `owned=0`).

- [ ] **Step 3: Implement the partition, flags, call, and summary**

In `import_scraped.py`:

(a) Add the import near the top (with the other first-party imports):

```python
import dlc_ownership
```

(b) Add a logging helper next to `_log_summary`:

```python
def _log_ownership(report: "dlc_ownership.OwnershipReport", *, dry_run: bool) -> None:
    label = "WOULD MARK (dry run)" if dry_run else "MARKED"
    logger.info("--- DLC OWNERSHIP (%s) ---", label)
    logger.info("owned marked:       %d", report.marked)
    logger.info("already owned:      %d", report.already_owned)
    logger.info("held (review):      %d", len(report.held))
    logger.info("unmatched:          %d", len(report.unmatched))
    for m in report.held:
        logger.info("  HOLD       '%s'  [%s]", m.addon_title, m.reason)
    for m in report.unmatched:
        logger.info("  UNMATCHED  '%s'  [%s]", m.addon_title, m.reason)
```

(c) Add the two CLI flags (next to `--no-dlc`):

```python
    parser.add_argument("--no-ownership", action="store_true",
                        help="skip scrape-driven DLC ownership matching after enrichment")
    parser.add_argument("--apply-flagged-ownership", action="store_true",
                        help="also apply held (ambiguous/containment-only) ownership matches")
```

(d) Replace the import loop + tail of `main` (from `total = ImportStats()` to `conn.close()`) with:

```python
    total = ImportStats()
    all_addons: list[dict] = []
    for path in _iter_json_paths(args.paths):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = data["games"]
        games_only = [g for g in rows if g.get("kind", "game") == "game"]
        all_addons.extend(g for g in rows if g.get("kind") == "addon")
        stats = import_games(conn, games_only, data["source"], dry_run=args.dry_run,
                             skip_non_games=not args.keep_non_games, confirm_fn=confirm)
        total.merge(stats)
        logger.info("%s (%s): +%d new, %d id, %d title, %d fuzzy, %d add-ons",
                    Path(path).name, data["source"], stats.new_games,
                    stats.external_id_matches, stats.title_matches,
                    len(stats.fuzzy_candidates),
                    sum(1 for g in rows if g.get("kind") == "addon"))

    if args.dry_run:
        logger.info("DRY RUN — no changes written.")
    else:
        conn.commit()
    _log_summary(total, dry_run=args.dry_run)
    if not args.dry_run and not args.no_dlc:
        run_dlc_enrichment(conn)
    if not args.no_ownership and all_addons:
        report = dlc_ownership.mark_ownership(
            conn, all_addons, dry_run=args.dry_run,
            include_flagged=args.apply_flagged_ownership)
        if not args.dry_run:
            conn.commit()
        _log_ownership(report, dry_run=args.dry_run)
    conn.close()
```

Note the new trailing `%d add-ons` arg in the per-file log line.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_import_scraped.py -k "ownership" -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite + lint/format**

Run: `uv run pytest -q`
Expected: PASS (all tests, including the previously-green suite).
Run: `uv run ruff check .`
Expected: "All checks passed!" (fix any findings, then re-run). NOTE: this repo's
gate is `ruff check` (lint) only — it does NOT use `ruff format` (the codebase
uses a hand-aligned style; `ruff format --check` reports ~34 pre-existing files
and must NOT be run). Match the surrounding hand-aligned style in new code.

- [ ] **Step 6: Commit**

```bash
git add import_scraped.py tests/test_import_scraped.py
git commit -m "feat: import_scraped partitions add-ons and runs DLC ownership matching"
```

---

## Task 9: PlayStation add-ons — no-op stub now, recon-gated follow-up

PSN add-ons are **not** in `getPurchasedGameList` and need a separate GraphQL
operation whose persisted-query hash must come from a fresh live recon. That
capture is interactive (real-Chrome login) and cannot be produced offline, so
this task ships a no-op stub now and documents the follow-up.

**Files:**
- Modify: `scrapers/playstation.py`

- [ ] **Step 1: Add the documented no-op `collect_addons`**

Append to `scrapers/playstation.py`:

```python
def collect_addons(page, captured: list | None = None) -> list[ScrapedGame]:
    """Owned PSN add-ons (kind="addon"), for DLC ownership matching.

    Disabled until the PSN add-on GraphQL operation + persisted-query hash are
    captured from a fresh recon (.recon/playstation.responses.jsonl); returns []
    so base-game scraping is unaffected. Enabling this is the recon-gated
    follow-up in docs/superpowers/specs/2026-05-25-dlc-scrape-ownership-design.md
    (PSN section).
    """
    logger.info("playstation: add-on capture not yet enabled (needs recon hash)")
    return []
```

- [ ] **Step 2: Verify nothing regressed**

Run: `uv run pytest tests/test_parse_playstation.py -q`
Expected: PASS (no behavior change to game parsing).

- [ ] **Step 3: Commit**

```bash
git add scrapers/playstation.py
git commit -m "chore: PSN collect_addons stub (recon-gated follow-up)"
```

- [ ] **Step 4 (follow-up, needs user): capture the PSN add-on recon**

This step is performed once the user provides a fresh PSN recon:
1. User runs the PSN scraper session and navigates to the **add-ons** view in
   their library so the add-on GraphQL request is captured to
   `.recon/playstation.responses.jsonl`.
2. From the capture, record the operation name + `sha256Hash` and a sanitized
   sample of the response into a new `tests/fixtures/playstation_addons_sample.json`.
3. Write `parse_addons(items) -> list[ScrapedGame]` (kind="addon") against that
   fixture (unit-tested like `parse_games`), and fill `collect_addons` to page the
   operation symmetrically to `collect`. Wire `collect_addons` into the PSN run
   path that writes the scrape file. (Out of scope for the offline plan.)

---

## Self-Review

**1. Spec coverage:**
- "Capturing add-ons in the scrapers" → Tasks 1 (kind field), 2 (Nintendo), 3 (Xbox), 9 (PSN stub + recon follow-up). ✓
- "The matching engine — `dlc_ownership.py`" (`parent_of`, `match_dlc`, `classify`, `mark_ownership`, 0→1-only/idempotent) → Tasks 4–7. ✓
- "Integration & CLI" (3-step order, `--no-ownership`, `--include-flagged`, `--dry-run`, summary, no backfill) → Task 8. The spec named the apply-held flag `--include-flagged`; this plan uses the clearer `--apply-flagged-ownership` to avoid confusion with the bundle path's `--include-curated` (behavior identical). ✓
- "Data model — no schema change" → no migration task exists, by design. ✓
- "Testing" pure + scraper-parse + integration → Tasks 2/3 (parse), 4–7 (pure + temp-DB), 8 (integration), full-suite gate in Task 8 Step 5. ✓
- "Out of scope" (PSN beyond stub, IGDB-id bridge, Piece 4, GUI) → not planned, correct. ✓

**2. Placeholder scan:** No "TBD/TODO/handle edge cases" in implementation steps. Task 9 Step 4 is an explicit user-gated follow-up (the spec's deferred PSN work), not a code placeholder — its code is intentionally not written until the recon shape is known.

**3. Type consistency:** `AMBIGUOUS` sentinel, `parent_of`/`match_dlc` (returns `(result, method)`), `classify` (returns `Match`), `Match(action, addon_title, game_id, dlc_id, reason)`, `OwnershipReport(marked, already_owned, held, unmatched)`, and `mark_ownership(conn, addons, *, dry_run, include_flagged)` are used identically across Tasks 4–8. `classify_nsuid`/`_kind_for` return `"game"|"addon"|None` consistent with `ScrapedGame.kind`. ✓
