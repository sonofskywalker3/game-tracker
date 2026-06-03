# Bundle-aware IGDB Identity Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop bundle constituents (and other games) from matching the wrong IGDB version (mobile ports) by resolving IGDB identity bundle-first (IGDB reverse `bundles` lookup) with a platform-aware, mobile-penalized scorer as fallback — plus a flag-only re-audit and a disambiguation modal.

**Architecture:** A new `igdb_match.py` owns all IGDB candidate fetching + scoring + bundle resolution. `fetch_covers.py` and `igdb_dlc.py` delegate identity resolution to it. Two new `games` columns (`igdb_locked`, `needs_igdb_review`) gate a flag-only audit; a game-modal candidate grid lets the owner pick the right version (which locks the game).

**Tech Stack:** Python 3.11, `requests` (IGDB v4 Apicalypse via existing helpers), Flask, sqlite3, `uv`, `pytest`, vanilla-JS/Jinja templates.

**Spec:** `docs/superpowers/specs/2026-06-03-bundle-aware-igdb-matching-design.md`

---

## File Structure

- **Create:** `igdb_match.py` — platform map, pure scorer (`score_candidates`), and the IGDB-querying resolvers (`fetch_candidates`, `resolve_bundle`, `bundle_constituents`, `resolve_identity`, `candidates_for`, `audit_igdb_matches`). One responsibility: turn a game (title + platforms + bundle context) into ranked IGDB identity candidates, bundle-first.
- **Create:** `tests/test_igdb_match.py` — unit tests with the IGDB query function monkeypatched (no live calls); DB-touching tests use the `temp_db` fixture.
- **Modify:** `models.py` (add `migrate_igdb_review`, register in `migrate_db`), `tests/conftest.py` (mirror the migration), `fetch_covers.py` (`search_game` delegates; loop skips locked), `igdb_dlc.py` (`enrich_game` delegates identity), `app.py` (two endpoints + lock on the existing pin), `templates/base.html` (modal "Fix match" grid), `templates/index.html` (Needs-review entry point).

## Conventions (owner rules — non-negotiable)

- Tests: `uv run python -m pytest` (plain `uv run pytest` fails: ModuleNotFoundError: models).
- Lint: `uv run ruff check .` ONLY — never `ruff format`.
- Subagents (Phase 1–4 code): pytest temp DB + monkeypatched IGDB only. **Never** run the app, touch the live `games.db`, make a live IGDB call, or `git push`.
- Work directly on `main` (no branch). End commit messages with the `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` trailer.
- The live re-audit over the real library is a **controller** operation (Phase 5), not a subagent task.

## Verified IGDB facts (controller-probed 2026-06-03)

- `game_type == 3` = bundle (`category` is deprecated, returns `None`).
- Reverse lookup `fields ...; where bundles = (<id>);` returns a bundle's constituents. Confirmed: Mega Man Legacy Collection 2 = id `28323`; `where bundles = (28323)` → Mega Man 7/8/9/10 (ids 1720/1721/1722/1723).
- Constituents inherit the bundle platform + `collection_name` (`import_scraped._constituent_game`, import_scraped.py:316).
- Platform ids: Switch 130, PS4 48, PS5 167, Xbox One 49, Xbox Series 169, PC 6; mobile iOS 39, Android 34.

---

## Phase 1 — `igdb_match.py` core (subagent TDD)

### Task 1: Platform map + `score_candidates` (pure)

**Files:**
- Create: `igdb_match.py`
- Test: `tests/test_igdb_match.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_igdb_match.py
import igdb_match


def _cand(name, plats, *, cover=True, rating=0, year=2000, igdb_id=1):
    return {"id": igdb_id, "name": name, "platforms": list(plats),
            "cover": {"url": "//x/t_thumb/a.jpg"} if cover else None,
            "total_rating_count": rating, "first_release_date": year}


def test_score_prefers_console_over_mobile_for_retro_constituent():
    # game is a Switch bundle constituent; IGDB has the canonical NES entry (no
    # Switch) and a mobile-only port. NES must win because mobile is penalized.
    NES, IOS, SWITCH = 18, igdb_match.IOS_ID, 130
    cands = [
        _cand("Mega Man 2", [IOS], igdb_id=999),       # mobile-only port
        _cand("Mega Man 2", [NES], rating=80, igdb_id=1711),  # canonical
    ]
    ranked = igdb_match.score_candidates(cands, game_platform_ids={SWITCH})
    assert ranked[0]["id"] == 1711
    assert ranked[0]["_mobile_only"] is False


def test_score_uses_platform_overlap_for_standalone():
    PS5 = 167
    cands = [
        _cand("Returnal", [18], rating=10, igdb_id=1),       # wrong platform
        _cand("Returnal", [PS5], rating=10, igdb_id=2),      # overlaps game
    ]
    ranked = igdb_match.score_candidates(cands, game_platform_ids={PS5})
    assert ranked[0]["id"] == 2


def test_score_drops_non_title_matches():
    cands = [_cand("Totally Different Game", [18], igdb_id=9)]
    assert igdb_match.score_candidates(cands, game_platform_ids=set(),
                                       title="Celeste") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_igdb_match.py -v`
Expected: FAIL (ModuleNotFoundError / no `score_candidates`).

- [ ] **Step 3: Write minimal implementation**

```python
# igdb_match.py
"""Bundle-aware IGDB identity resolution.

Resolves a game to its correct IGDB entry (igdb_id + cover) by, in order:
  1. bundle-first — if the game came from a bundle (collection_name set), resolve
     the bundle on IGDB and pull its canonical constituents via the reverse
     `where bundles = (id)` lookup, then match by normalized_title;
  2. fallback — a platform-aware, mobile-penalized title-search scorer.

All IGDB access goes through igdb_dlc._igdb_query (monkeypatched in tests).
"""
from __future__ import annotations

import igdb_dlc
from models import normalize_title

IOS_ID = 39
ANDROID_ID = 34
MOBILE_PLATFORM_IDS = frozenset({IOS_ID, ANDROID_ID})

# app short_name -> IGDB platform id(s). Extensible; unknown names contribute no
# overlap (safe). Mobile is handled separately via MOBILE_PLATFORM_IDS.
IGDB_PLATFORM_IDS: dict[str, frozenset[int]] = {
    "Switch": frozenset({130}),
    "PS5": frozenset({167}),
    "PS4": frozenset({48}),
    "Xbox": frozenset({49, 169}),   # Xbox One + Series X|S
    "Steam": frozenset({6}),         # PC (Windows)
    "PC": frozenset({6}),
}

_TITLE_EXACT = 100
_TITLE_CONTAINS = 40
_PLATFORM_OVERLAP = 50
_MOBILE_PENALTY = -80
_HAS_COVER = 10


def platform_ids_for(short_names) -> set[int]:
    """Map app platform short_names to the set of IGDB platform ids."""
    out: set[int] = set()
    for sn in short_names or ():
        out |= set(IGDB_PLATFORM_IDS.get(sn, ()))
    return out


def _title_score(cand_name: str, search: str) -> int | None:
    a, b = normalize_title(cand_name), normalize_title(search)
    if not a or not b:
        return None
    if a == b:
        return _TITLE_EXACT
    if b in a or a in b:
        return _TITLE_CONTAINS
    return None


def score_candidates(candidates: list[dict], *, game_platform_ids: set[int],
                     title: str | None = None) -> list[dict]:
    """Return candidates ranked best-first. Drops non-title matches when `title`
    is given. Each returned candidate carries `_score` and `_mobile_only`."""
    ranked = []
    for c in candidates:
        plats = set(c.get("platforms") or [])
        mobile_only = bool(plats) and plats <= MOBILE_PLATFORM_IDS
        score = 0
        if title is not None:
            ts = _title_score(c.get("name", ""), title)
            if ts is None:
                continue
            score += ts
        if plats & game_platform_ids:
            score += _PLATFORM_OVERLAP
        if mobile_only:
            score += _MOBILE_PENALTY
        if c.get("cover"):
            score += _HAS_COVER
        out = dict(c)
        out["_score"] = score
        out["_mobile_only"] = mobile_only
        ranked.append(out)
    ranked.sort(key=lambda c: (c["_score"], c.get("total_rating_count") or 0,
                               -(c.get("first_release_date") or 0)), reverse=True)
    return ranked
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_igdb_match.py -v`
Expected: PASS.

- [ ] **Step 5: Run ruff**

Run: `uv run ruff check igdb_match.py tests/test_igdb_match.py`
Expected: All checks passed.

- [ ] **Step 6: Commit**

```bash
git add igdb_match.py tests/test_igdb_match.py
git commit -m "feat(igdb): platform-aware mobile-penalized candidate scorer

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `fetch_candidates` + `cover_url_of` (IGDB query)

**Files:**
- Modify: `igdb_match.py`
- Test: `tests/test_igdb_match.py`

- [ ] **Step 1: Write the failing test**

```python
def test_fetch_candidates_queries_with_platforms(monkeypatch):
    seen = {}
    def fake(query, cid, tok):
        seen["q"] = query
        return [{"id": 1, "name": "Celeste", "platforms": [6],
                 "cover": {"url": "//x/t_thumb/a.jpg"}}]
    monkeypatch.setattr(igdb_match.igdb_dlc, "_igdb_query", fake)
    out = igdb_match.fetch_candidates("Celeste", "c", "t")
    assert "search \"Celeste\"" in seen["q"]
    assert "platforms" in seen["q"]
    assert out[0]["name"] == "Celeste"


def test_cover_url_of_normalizes_to_big_https():
    c = {"cover": {"url": "//images.igdb.com/igdb/image/upload/t_thumb/abc.jpg"}}
    assert igdb_match.cover_url_of(c) == \
        "https://images.igdb.com/igdb/image/upload/t_cover_big/abc.jpg"
    assert igdb_match.cover_url_of({"cover": None}) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_igdb_match.py::test_fetch_candidates_queries_with_platforms tests/test_igdb_match.py::test_cover_url_of_normalizes_to_big_https -v`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation** (append to `igdb_match.py`)

```python
def _escape(title: str) -> str:
    return title.replace('"', "").replace("\n", " ").strip()


def fetch_candidates(title: str, client_id: str, token: str,
                     limit: int = 10) -> list[dict]:
    """Title-search IGDB, returning candidates WITH platform + ranking signals."""
    query = (
        f'search "{_escape(title)}"; '
        "fields name, cover.url, platforms, first_release_date, "
        "total_rating_count, game_type; "
        f"limit {int(limit)};"
    )
    return igdb_dlc._igdb_query(query, client_id, token) or []


def cover_url_of(candidate: dict) -> str | None:
    """Return a normalized https t_cover_big URL for a candidate, or None."""
    cover = candidate.get("cover") or {}
    url = cover.get("url")
    if not url:
        return None
    url = url.replace("t_thumb", "t_cover_big")
    return url if url.startswith("http") else "https:" + url
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_igdb_match.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add igdb_match.py tests/test_igdb_match.py
git commit -m "feat(igdb): fetch_candidates with platforms + cover_url normalize

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `resolve_bundle` + `bundle_constituents` (IGDB reverse lookup)

**Files:**
- Modify: `igdb_match.py`
- Test: `tests/test_igdb_match.py`

- [ ] **Step 1: Write the failing test**

```python
def test_resolve_bundle_picks_game_type_3_platform_preferred(monkeypatch):
    def fake(query, cid, tok):
        assert 'search "Mega Man Legacy Collection 2"' in query
        return [
            {"id": 1, "name": "Mega Man Legacy Collection 1 + 2",
             "game_type": 3, "platforms": [130]},     # wrong product
            {"id": 28323, "name": "Mega Man Legacy Collection 2",
             "game_type": 3, "platforms": [48, 6, 49, 130]},  # exact + Switch
            {"id": 7, "name": "Mega Man Legacy Collection 2 OST",
             "game_type": 0, "platforms": [130]},     # not a bundle
        ]
    monkeypatch.setattr(igdb_match.igdb_dlc, "_igdb_query", fake)
    bid = igdb_match.resolve_bundle("Mega Man Legacy Collection 2", {130}, "c", "t")
    assert bid == 28323


def test_bundle_constituents_reverse_lookup(monkeypatch):
    def fake(query, cid, tok):
        assert "where bundles = (28323)" in query
        return [
            {"id": 1720, "name": "Mega Man 7", "platforms": [19],
             "cover": {"url": "//x/t_thumb/7.jpg"}},
            {"id": 1721, "name": "Mega Man 8", "platforms": [7],
             "cover": {"url": "//x/t_thumb/8.jpg"}},
        ]
    monkeypatch.setattr(igdb_match.igdb_dlc, "_igdb_query", fake)
    cons = igdb_match.bundle_constituents(28323, "c", "t")
    assert {c["normalized_title"] for c in cons} == {"mega man 7", "mega man 8"}
    assert cons[0]["cover_url"].endswith("t_cover_big/7.jpg")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_igdb_match.py::test_resolve_bundle_picks_game_type_3_platform_preferred tests/test_igdb_match.py::test_bundle_constituents_reverse_lookup -v`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation** (append to `igdb_match.py`)

```python
_BUNDLE_GAME_TYPE = 3


def resolve_bundle(name: str, game_platform_ids: set[int],
                   client_id: str, token: str) -> int | None:
    """Find the IGDB bundle (game_type==3) for `name`, preferring one whose
    platforms overlap the owned bundle's platforms, then exact-title match."""
    query = (
        f'search "{_escape(name)}"; '
        "fields name, game_type, platforms; limit 10;"
    )
    results = igdb_dlc._igdb_query(query, client_id, token) or []
    bundles = [r for r in results if r.get("game_type") == _BUNDLE_GAME_TYPE]
    if not bundles:
        return None
    target = normalize_title(name)

    def rank(r: dict) -> tuple:
        plats = set(r.get("platforms") or [])
        return (bool(plats & game_platform_ids),
                normalize_title(r.get("name", "")) == target)

    best = max(bundles, key=rank)
    return best.get("id")


def bundle_constituents(bundle_id: int, client_id: str, token: str) -> list[dict]:
    """Reverse lookup: the games whose `bundles` array contains bundle_id.
    Returns [{igdb_id, name, normalized_title, cover_url, platforms}]."""
    query = (
        "fields name, cover.url, platforms, first_release_date; "
        f"where bundles = ({int(bundle_id)}); limit 50;"
    )
    rows = igdb_dlc._igdb_query(query, client_id, token) or []
    out = []
    for r in rows:
        out.append({
            "igdb_id": r.get("id"),
            "name": r.get("name"),
            "normalized_title": normalize_title(r.get("name", "")),
            "cover_url": cover_url_of(r),
            "platforms": r.get("platforms") or [],
        })
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_igdb_match.py -v`
Expected: PASS.

- [ ] **Step 5: Run full suite + ruff**

Run: `uv run python -m pytest -q && uv run ruff check .`
Expected: all green, All checks passed.

- [ ] **Step 6: Commit**

```bash
git add igdb_match.py tests/test_igdb_match.py
git commit -m "feat(igdb): bundle resolve + reverse-lookup constituents

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `resolve_identity` + `candidates_for` (bundle-first order)

**Files:**
- Modify: `igdb_match.py`
- Test: `tests/test_igdb_match.py`

- [ ] **Step 1: Write the failing test**

```python
def test_resolve_identity_bundle_first(monkeypatch):
    calls = []
    def fake(query, cid, tok):
        calls.append(query)
        if "game_type" in query and "search" in query:           # resolve_bundle
            return [{"id": 28323, "name": "Mega Man Legacy Collection 2",
                     "game_type": 3, "platforms": [130]}]
        if "where bundles = (28323)" in query:                   # constituents
            return [{"id": 1711, "name": "Mega Man 2", "platforms": [18],
                     "cover": {"url": "//x/t_thumb/2.jpg"}}]
        raise AssertionError("should not fall back to search scorer")
    monkeypatch.setattr(igdb_match.igdb_dlc, "_igdb_query", fake)
    got = igdb_match.resolve_identity(
        "Mega Man 2", {130}, "Mega Man Legacy Collection 2", "c", "t")
    assert got["igdb_id"] == 1711
    assert got["source"] == "bundle"
    assert got["cover_url"].endswith("t_cover_big/2.jpg")


def test_resolve_identity_falls_back_to_scorer(monkeypatch):
    def fake(query, cid, tok):
        if "search \"Celeste\"" in query and "game_type" in query:
            return [{"id": 5, "name": "Celeste", "platforms": [6],
                     "cover": {"url": "//x/t_thumb/c.jpg"},
                     "total_rating_count": 50}]
        return []
    monkeypatch.setattr(igdb_match.igdb_dlc, "_igdb_query", fake)
    got = igdb_match.resolve_identity("Celeste", {6}, None, "c", "t")
    assert got["igdb_id"] == 5 and got["source"] == "search"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_igdb_match.py::test_resolve_identity_bundle_first tests/test_igdb_match.py::test_resolve_identity_falls_back_to_scorer -v`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation** (append to `igdb_match.py`)

```python
def _as_identity(igdb_id, name, cover_url, source) -> dict:
    return {"igdb_id": igdb_id, "name": name, "cover_url": cover_url,
            "source": source}


def candidates_for(title: str, game_platform_ids: set[int],
                   collection_name: str | None, client_id: str, token: str
                   ) -> list[dict]:
    """Ranked identity candidates, bundle-derived first then scored search.
    Each: {igdb_id, name, cover_url, platforms, source, score?}."""
    out: list[dict] = []
    seen: set[int] = set()
    target = normalize_title(title)
    if collection_name:
        bid = resolve_bundle(collection_name, game_platform_ids, client_id, token)
        if bid:
            for c in bundle_constituents(bid, client_id, token):
                if normalize_title(c["name"] or "") == target and c["igdb_id"] not in seen:
                    seen.add(c["igdb_id"])
                    out.append({**c, "source": "bundle"})
    for c in score_candidates(fetch_candidates(title, client_id, token),
                              game_platform_ids=game_platform_ids, title=title):
        if c.get("id") in seen:
            continue
        out.append({"igdb_id": c.get("id"), "name": c.get("name"),
                    "cover_url": cover_url_of(c), "platforms": c.get("platforms") or [],
                    "source": "search", "score": c["_score"]})
    return out


def resolve_identity(title: str, game_platform_ids: set[int],
                     collection_name: str | None, client_id: str, token: str
                     ) -> dict | None:
    """Best identity for a game (bundle-first). Returns an identity dict or None."""
    cands = candidates_for(title, game_platform_ids, collection_name, client_id, token)
    if not cands:
        return None
    best = cands[0]
    return _as_identity(best["igdb_id"], best["name"], best.get("cover_url"),
                        best["source"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_igdb_match.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add igdb_match.py tests/test_igdb_match.py
git commit -m "feat(igdb): resolve_identity + candidates_for (bundle-first order)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 2 — wire enrichment to bundle-first (subagent TDD)

### Task 5: `fetch_covers.search_game` delegates; cover loop skips locked

**Files:**
- Modify: `fetch_covers.py` (`search_game` ~154-199; enrichment loop ~290-324)
- Test: `tests/test_fetch_covers.py`

- [ ] **Step 1: Write the failing test** (add to `tests/test_fetch_covers.py`)

```python
def test_search_game_uses_platform_aware_resolver(monkeypatch):
    import fetch_covers, igdb_match
    monkeypatch.setattr(igdb_match, "resolve_identity",
                        lambda *a, **k: {"igdb_id": 1, "name": "Mega Man 2",
                                         "cover_url": "https://x/t_cover_big/2.jpg",
                                         "source": "bundle"})
    url = fetch_covers.search_game("Mega Man 2", "c", "t",
                                   platform_ids={130}, collection_name="Mega Man Legacy Collection 2")
    assert url == "https://x/t_cover_big/2.jpg"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_fetch_covers.py::test_search_game_uses_platform_aware_resolver -v`
Expected: FAIL (signature has no `platform_ids`).

- [ ] **Step 3: Write minimal implementation**

Change `search_game` (fetch_covers.py:154) to accept and forward platform/bundle context, delegating identity to `igdb_match.resolve_identity`:

```python
def search_game(title, client_id, access_token, strict=False,
                platform_ids=None, collection_name=None):
    """Return the cover URL for the best IGDB identity match (bundle-first,
    platform-aware). `strict` is retained for callers that must not replace an
    existing correct cover with a low-confidence guess."""
    import igdb_match
    identity = igdb_match.resolve_identity(
        title, set(platform_ids or ()), collection_name, client_id, access_token)
    if identity and identity.get("cover_url"):
        return identity["cover_url"]
    return None
```

Update the enrichment loop (fetch_covers.py ~290-316) so it (a) selects `id, title, cover_url, collection_name, igdb_locked` and skips `igdb_locked` rows, and (b) loads each game's platform short_names and passes `platform_ids`/`collection_name`:

```python
    rows = conn.execute(
        "SELECT id, title, cover_url, collection_name, "
        "COALESCE(igdb_locked, 0) AS igdb_locked FROM games ORDER BY title"
    ).fetchall()
    games = [g for g in rows
             if not g["igdb_locked"] and needs_cover(g["cover_url"], upgrade=upgrade_non_igdb)]
    ...
    import igdb_match
    plat_short = [r[0] for r in conn.execute(
        "SELECT p.short_name FROM game_platforms gp JOIN platforms p "
        "ON p.id = gp.platform_id WHERE gp.game_id = ?", (game["id"],))]
    cover_url = search_game(
        title, client_id, access_token, strict=upgrade_non_igdb,
        platform_ids=igdb_match.platform_ids_for(plat_short),
        collection_name=game["collection_name"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_fetch_covers.py -v`
Expected: PASS.

- [ ] **Step 5: Run full suite + ruff** (existing fetch_covers tests must still pass)

Run: `uv run python -m pytest -q && uv run ruff check .`
Expected: all green. If a pre-existing test calls `search_game` with the old signature, the new keyword args are optional so it still works; verify.

- [ ] **Step 6: Commit**

```bash
git add fetch_covers.py tests/test_fetch_covers.py
git commit -m "feat(covers): platform/bundle-aware cover match; skip locked games

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `igdb_dlc.enrich_game` resolves identity bundle-first by title

**Files:**
- Modify: `igdb_dlc.py` (`enrich_game` ~127-155)
- Test: `tests/test_igdb_dlc.py`

- [ ] **Step 1: Write the failing test** (add to `tests/test_igdb_dlc.py`)

```python
def test_enrich_game_title_path_uses_resolver(temp_db, monkeypatch):
    import igdb_dlc, igdb_match
    conn = temp_db
    conn.execute("INSERT INTO games (title, normalized_title, collection_name) "
                 "VALUES ('Mega Man 2', 'mega man 2', 'Mega Man Legacy Collection 2')")
    gid = conn.execute("SELECT id FROM games WHERE title='Mega Man 2'").fetchone()[0]
    monkeypatch.setattr(igdb_match, "resolve_identity",
                        lambda *a, **k: {"igdb_id": 1711, "name": "Mega Man 2",
                                         "cover_url": "https://x/t_cover_big/2.jpg",
                                         "source": "bundle"})
    monkeypatch.setattr(igdb_dlc, "fetch_game_by_id",
                        lambda iid, c, t: {"id": iid, "name": "Mega Man 2", "dlcs": []})
    report = igdb_dlc.enrich_game(conn, gid, "c", "t")
    assert report["matched"]
    assert conn.execute("SELECT igdb_id FROM games WHERE id=?", (gid,)).fetchone()[0] == 1711
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_igdb_dlc.py::test_enrich_game_title_path_uses_resolver -v`
Expected: FAIL (title path still uses `fetch_game_by_title`).

- [ ] **Step 3: Write minimal implementation**

In `enrich_game` (igdb_dlc.py:127), when resolving by **title** (no slug, no stored igdb_id), use the bundle-first resolver to get the igdb_id, then load the full game by id. Load the game's platforms + `collection_name` from the DB row:

```python
    row = conn.execute(
        "SELECT title, igdb_id, cover_url, collection_name FROM games WHERE id = ?",
        (game_id,)).fetchone()
    ...
    elif row["igdb_id"]:
        game = fetch_game_by_id(row["igdb_id"], client_id, token)
    else:
        import igdb_match
        plat_short = [r[0] for r in conn.execute(
            "SELECT p.short_name FROM game_platforms gp JOIN platforms p "
            "ON p.id = gp.platform_id WHERE gp.game_id = ?", (game_id,))]
        identity = igdb_match.resolve_identity(
            row["title"], igdb_match.platform_ids_for(plat_short),
            row["collection_name"], client_id, token)
        game = fetch_game_by_id(identity["igdb_id"], client_id, token) if identity else None
```

Keep the existing `UPDATE games SET igdb_id = ?` + cover write that follows.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_igdb_dlc.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add igdb_dlc.py tests/test_igdb_dlc.py
git commit -m "feat(igdb): enrich_game title path resolves identity bundle-first

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 3 — migration + flag-only audit (subagent TDD)

### Task 7: `migrate_igdb_review` columns

**Files:**
- Modify: `models.py` (new `migrate_igdb_review` near migrate_collection_name ~728; register in `migrate_db` after `migrate_series_source`), `tests/conftest.py` (mirror)
- Test: `tests/test_igdb_match.py`

- [ ] **Step 1: Write the failing test**

```python
def test_migrate_igdb_review_adds_columns(temp_db):
    cols = [c[1] for c in temp_db.execute("PRAGMA table_info(games)")]
    assert "igdb_locked" in cols and "needs_igdb_review" in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_igdb_match.py::test_migrate_igdb_review_adds_columns -v`
Expected: FAIL (columns missing — conftest doesn't create them yet).

- [ ] **Step 3: Write minimal implementation**

Add to `models.py` (mirror `migrate_game_traits`):

```python
def migrate_igdb_review(conn: sqlite3.Connection) -> None:
    """Add games.igdb_locked + games.needs_igdb_review. Idempotent.
    igdb_locked=1 protects a hand-picked IGDB identity from enrichment + audit.
    needs_igdb_review=1 flags a game the audit thinks matched the wrong version.
    """
    cols = [c[1] for c in conn.execute("PRAGMA table_info(games)").fetchall()]
    additions = [("igdb_locked", "INTEGER NOT NULL DEFAULT 0"),
                 ("needs_igdb_review", "INTEGER NOT NULL DEFAULT 0")]
    for name, decl in additions:
        if name not in cols:
            conn.execute(f"ALTER TABLE games ADD COLUMN {name} {decl}")
    conn.commit()
```

Register it in `migrate_db` immediately after `migrate_series_source(conn)`:

```python
    migrate_series_source(conn)
    migrate_igdb_review(conn)
```

Mirror in `tests/conftest.py` after `models.migrate_series_source(conn)`:

```python
    models.migrate_igdb_review(conn)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_igdb_match.py::test_migrate_igdb_review_adds_columns -v`
Expected: PASS.

- [ ] **Step 5: Run full suite + ruff**

Run: `uv run python -m pytest -q && uv run ruff check .`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add models.py tests/conftest.py tests/test_igdb_match.py
git commit -m "feat(db): migrate_igdb_review (igdb_locked + needs_igdb_review)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: `audit_igdb_matches` (flag-only)

**Files:**
- Modify: `igdb_match.py`
- Test: `tests/test_igdb_match.py`

- [ ] **Step 1: Write the failing test**

```python
def test_audit_flags_disagreement_skips_locked_and_agreeing(temp_db, monkeypatch):
    conn = temp_db
    conn.executescript(
        "INSERT INTO games (id,title,normalized_title,cover_url,collection_name,igdb_locked) "
        "VALUES (1,'Mega Man 2','mega man 2','https://x/t_cover_big/MOBILE.jpg','MM LC2',0),"
        "       (2,'Celeste','celeste','https://x/t_cover_big/c.jpg',NULL,0),"
        "       (3,'Locked','locked','https://x/t_cover_big/old.jpg',NULL,1);")
    def fake_resolve(title, plats, coll, c, t):
        if title == 'Mega Man 2':
            return {"igdb_id": 1711, "name": title,
                    "cover_url": "https://x/t_cover_big/RIGHT.jpg", "source": "bundle"}
        if title == 'Celeste':                       # agrees with current cover
            return {"igdb_id": 5, "name": title,
                    "cover_url": "https://x/t_cover_big/c.jpg", "source": "search"}
        raise AssertionError("locked game must be skipped")
    monkeypatch.setattr(igdb_match, "resolve_identity", fake_resolve)
    flagged = igdb_match.audit_igdb_matches(conn, client_id="c", token="t")
    assert flagged == [1]                            # only Mega Man 2 disagrees
    assert conn.execute("SELECT needs_igdb_review FROM games WHERE id=1").fetchone()[0] == 1
    assert conn.execute("SELECT needs_igdb_review FROM games WHERE id=2").fetchone()[0] == 0
    # never mutates cover/igdb_id
    assert conn.execute("SELECT cover_url FROM games WHERE id=1").fetchone()[0].endswith("MOBILE.jpg")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_igdb_match.py::test_audit_flags_disagreement_skips_locked_and_agreeing -v`
Expected: FAIL (no `audit_igdb_matches`).

- [ ] **Step 3: Write minimal implementation** (append to `igdb_match.py`)

```python
def audit_igdb_matches(conn, *, client_id: str, token: str) -> list[int]:
    """Flag (needs_igdb_review=1) every non-locked game whose resolved best
    identity's cover differs from the current cover. Never mutates cover/igdb_id;
    games whose current cover already matches the resolved one are not flagged.
    Returns the list of flagged game ids."""
    rows = conn.execute(
        "SELECT id, title, cover_url, collection_name FROM games "
        "WHERE COALESCE(igdb_locked, 0) = 0 ORDER BY title").fetchall()
    flagged: list[int] = []
    for r in rows:
        plat_short = [x[0] for x in conn.execute(
            "SELECT p.short_name FROM game_platforms gp JOIN platforms p "
            "ON p.id = gp.platform_id WHERE gp.game_id = ?", (r["id"],))]
        identity = resolve_identity(r["title"], platform_ids_for(plat_short),
                                    r["collection_name"], client_id, token)
        if not identity or not identity.get("cover_url"):
            continue
        if identity["cover_url"] != r["cover_url"]:
            conn.execute("UPDATE games SET needs_igdb_review = 1 WHERE id = ?", (r["id"],))
            flagged.append(r["id"])
    conn.commit()
    return flagged
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_igdb_match.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add igdb_match.py tests/test_igdb_match.py
git commit -m "feat(igdb): audit_igdb_matches (flag-only, skips locked/agreeing)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 4 — endpoints + modal (subagent TDD)

### Task 9: candidates + pick endpoints; lock on existing pin

**Files:**
- Modify: `app.py` (new routes near `api_pin_igdb` ~568; add `igdb_locked=1` to `api_pin_igdb`)
- Test: `tests/test_api_games.py`

- [ ] **Step 1: Write the failing test**

```python
def test_igdb_candidates_and_pick(client, temp_db, monkeypatch):
    import igdb_dlc, igdb_match
    temp_db.execute("INSERT INTO games (id,title,normalized_title,collection_name) "
                    "VALUES (1,'Mega Man 2','mega man 2','MM LC2')")
    temp_db.commit()
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda *a, **k: "tok")
    import config
    monkeypatch.setattr(config, "get_twitch_credentials", lambda: ("c", "s"))
    monkeypatch.setattr(igdb_match, "candidates_for", lambda *a, **k: [
        {"igdb_id": 1711, "name": "Mega Man 2", "cover_url": "https://x/t_cover_big/2.jpg",
         "platforms": [18], "source": "bundle"}])

    r = client.get("/api/games/1/igdb-candidates")
    assert r.status_code == 200 and r.get_json()["candidates"][0]["igdb_id"] == 1711

    r = client.post("/api/games/1/igdb-pick", json={"igdb_id": 1711,
                    "cover_url": "https://x/t_cover_big/2.jpg"})
    assert r.status_code == 200
    row = temp_db.execute(
        "SELECT igdb_id, cover_url, igdb_locked, needs_igdb_review FROM games WHERE id=1"
    ).fetchone()
    assert row["igdb_id"] == 1711 and row["igdb_locked"] == 1 and row["needs_igdb_review"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_api_games.py::test_igdb_candidates_and_pick -v`
Expected: FAIL (routes missing).

- [ ] **Step 3: Write minimal implementation** (add near `api_pin_igdb` in `app.py`)

```python
@app.route('/api/games/<int:game_id>/igdb-candidates', methods=['GET'])
def api_igdb_candidates(game_id):
    """Ranked IGDB identity candidates for a game (bundle-first, platform-aware)."""
    import config
    import igdb_match
    conn = get_db()
    row = conn.execute(
        "SELECT title, collection_name FROM games WHERE id = ?", (game_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Game not found'}), 404
    client_id, secret = config.get_twitch_credentials()
    if not client_id:
        conn.close()
        return jsonify({'error': 'IGDB credentials not configured'}), 400
    plat_short = [r[0] for r in conn.execute(
        "SELECT p.short_name FROM game_platforms gp JOIN platforms p "
        "ON p.id = gp.platform_id WHERE gp.game_id = ?", (game_id,))]
    conn.close()
    import igdb_dlc
    token = igdb_dlc.get_access_token(client_id, secret)
    cands = igdb_match.candidates_for(
        row["title"], igdb_match.platform_ids_for(plat_short),
        row["collection_name"], client_id, token)
    return jsonify({'candidates': cands})


@app.route('/api/games/<int:game_id>/igdb-pick', methods=['POST'])
def api_igdb_pick(game_id):
    """Apply a chosen IGDB identity: set igdb_id + cover_url, lock, clear review."""
    data = request.get_json(silent=True) or {}
    igdb_id = data.get('igdb_id')
    cover_url = (data.get('cover_url') or '').strip() or None
    conn = get_db()
    if not conn.execute("SELECT 1 FROM games WHERE id = ?", (game_id,)).fetchone():
        conn.close()
        return jsonify({'error': 'Game not found'}), 404
    conn.execute(
        "UPDATE games SET igdb_id = ?, cover_url = COALESCE(?, cover_url), "
        "igdb_locked = 1, needs_igdb_review = 0, updated_at = CURRENT_TIMESTAMP "
        "WHERE id = ?", (igdb_id, cover_url, game_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True})
```

In `api_pin_igdb` (app.py:588), after the successful `enrich_game` + commit, also lock:

```python
        report = igdb_dlc.enrich_game(conn, game_id, client_id, token, slug=slug)
        conn.execute("UPDATE games SET igdb_locked = 1, needs_igdb_review = 0 WHERE id = ?",
                     (game_id,))
        conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_api_games.py -v`
Expected: PASS.

- [ ] **Step 5: Run full suite + ruff**

Run: `uv run python -m pytest -q && uv run ruff check .`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_api_games.py
git commit -m "feat(api): igdb-candidates + igdb-pick endpoints; lock on pin

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Modal "Fix match" candidate grid

**Files:**
- Modify: `templates/base.html` (game modal, near the Source-link section ~942-970)

This task is UI-only (no unit test; the controller verifies live in Phase 5).

- [ ] **Step 1: Add a "Wrong version? Fix match" control to the modal**

In the modal body, below the Source-link block, add a button + a hidden candidate container:

```html
<div class="mt-2">
  <button onclick="loadIgdbCandidates(${game.id})"
          class="text-xs text-accent hover:underline">Wrong version? Fix match</button>
  <div id="igdb-candidates-${game.id}" class="mt-2 hidden grid grid-cols-2 gap-2"></div>
</div>
```

- [ ] **Step 2: Add the JS (near the modal helpers in base.html)**

```javascript
async function loadIgdbCandidates(gameId) {
    const box = document.getElementById(`igdb-candidates-${gameId}`);
    box.classList.remove('hidden');
    box.innerHTML = '<span class="text-xs text-gray-400 col-span-2">Searching IGDB…</span>';
    const res = await api.get(`/api/games/${gameId}/igdb-candidates`);
    const cands = (res.candidates || []);
    if (!cands.length) { box.innerHTML = '<span class="text-xs text-yellow-400 col-span-2">No candidates found.</span>'; return; }
    box.innerHTML = cands.map(c => `
        <button onclick='pickIgdb(${gameId}, ${c.igdb_id}, ${JSON.stringify(c.cover_url)})'
                class="text-left bg-surface rounded-lg border border-gray-700 hover:border-accent p-2">
          ${c.cover_url ? `<img src="${c.cover_url}" class="w-full h-24 object-cover rounded mb-1">` : ''}
          <div class="text-xs text-white">${escapeHtml(c.name || '')}</div>
          <div class="text-[10px] text-gray-400">${c.source}${(c.platforms||[]).length ? ' · ' + c.platforms.length + ' platforms' : ''}</div>
        </button>`).join('');
}

async function pickIgdb(gameId, igdbId, coverUrl) {
    await api.post(`/api/games/${gameId}/igdb-pick`, {igdb_id: igdbId, cover_url: coverUrl});
    closeModal();
    if (typeof refreshGameList === 'function') refreshGameList();
}
```

(Confirm `api.post` exists in base.html; the codebase uses `api.get`/`api.patch`/`api.put` — add a thin `api.post` if missing, mirroring `api.put`.)

- [ ] **Step 3: Commit**

```bash
git add templates/base.html
git commit -m "feat(ui): modal 'Fix match' IGDB candidate grid

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: "Needs review (N)" entry point

**Files:**
- Modify: `app.py` (`/api/games` list — surface `needs_igdb_review`; add a small `/api/games/needs-igdb-review` count or filter), `templates/index.html` (a "Needs review (N)" link that filters the grid to flagged games)

- [ ] **Step 1: Surface the flag in the games list**

In the `/api/games` row serialization (app.py ~70-166), include `needs_igdb_review` so the front-end can render a badge and filter.

- [ ] **Step 2: Add a filter affordance in `index.html`**

Add a "Needs review (N)" control that, when clicked, shows only games with `needs_igdb_review`, each opening the modal's Fix-match grid. Compute N from the loaded list.

- [ ] **Step 3: Run full suite + ruff**

Run: `uv run python -m pytest -q && uv run ruff check .`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add app.py templates/index.html
git commit -m "feat(ui): Needs-review entry point for flagged IGDB matches

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 5 — controller-run live verification + audit (NOT subagent)

- [ ] Controller live-probes IGDB once to confirm `resolve_bundle("Mega Man Legacy Collection 2", {130})` → 28323 and `bundle_constituents(28323)` → MM7–10.
- [ ] BACKUP the live DB: `games.db.bak-20260603-pre-igdb-audit`.
- [ ] Controller runs `audit_igdb_matches` over the real `games.db` (with live IGDB), reports the flagged count to the owner. Never auto-changes.
- [ ] Controller starts the app; owner walks the "Needs review" list, picks correct versions in the modal (each locks the game). Then stop the app.
- [ ] `uv run python -m pytest -q` green; `uv run ruff check .` clean; push.

---

## Notes for the implementer

- **Phase 1–4 are subagent-implementable** (pure code + monkeypatched IGDB + temp-DB tests). **Phase 5 is controller-only** (live IGDB + real DB).
- Reuse `models.normalize_title`, `igdb_dlc._igdb_query`, `igdb_dlc.get_access_token` — do not reimplement.
- Never make a live IGDB call in a test; always monkeypatch `igdb_match.igdb_dlc._igdb_query` (or the specific `igdb_match`/`igdb_dlc` function under test).
- The platform map is a starting set; an unknown `short_name` simply contributes no overlap (safe, never wrong).
- `igdb_locked` protects manual fixes — every write path that sets a hand-chosen identity (modal pick, Source-link pin) sets it; enrichment + audit skip it.
