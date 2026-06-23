# UPC Enrichment — Plan B (Phase 2: Pluggable resolve + Wikidata) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `barcode.resolve()`'s product-lookup step a pluggable, ordered multi-source chain, and add Wikidata GTIN as the first extra free source — so scans for games UPCitemdb misses can still resolve, and new free sources slot in trivially.

**Architecture:** Extract the single `product = lookup_product_title(upc)` call inside `resolve()` into a chain iterating a module-level `PRODUCT_SOURCES` tuple, each source `(upc) -> str | None` (a product/game title). The first non-empty result feeds the rest of `resolve()` unchanged. Add `barcode.lookup_wikidata_gtin(upc) -> str | None` querying the keyless Wikidata SPARQL endpoint for a video game whose GTIN equals the UPC (or a zero-padded variant), returning its English label.

**Tech Stack:** Python 3 / `requests` / Wikidata SPARQL (keyless, no rate limit). No new dependencies. Tests via `uv run python -m pytest`; lint `ruff check` only.

## Global Constraints

- **Verified pre-flight (controller, 2026-06-23):** Wikidata property for GTIN is **`P3962`** ("Global Trade Item Number"); the video-game class is **`Q7889`**. The query `?item wdt:P3962 ?gtin . ?item wdt:P31/wdt:P279* wd:Q7889` returns video games by GTIN (confirmed live: Crash Bandicoot, Sonic Generations, RollerCoaster Tycoon 2). **Wikidata zero-pads GTINs** — a 12-digit UPC `711719490029` is stored as `00711719490029`. So the lookup MUST try the raw UPC plus `"0"+upc` and `"00"+upc`. SPARQL endpoint: `https://query.wikidata.org/sparql` (send a descriptive `User-Agent`; request `application/sparql-results+json`).
- **Coverage is sparse** for games — this source hits rarely. That is expected and acceptable; the deliverable is the extensible seam, not the hit rate.
- **Backward compatibility:** Plan A is shipped. The chain's FIRST source stays `lookup_product_title`, so default behavior is byte-identical when UPCitemdb returns a product. Only the previously-`source:'none'` (UPCitemdb miss) path changes — it now falls through to Wikidata before giving up.
- **Tests:** `uv run python -m pytest` (NEVER plain `pytest`). Lint `ruff check` ONLY (never `ruff format`). All external calls mocked in pytest. **Subagents never touch the network / live DB / running server / device** — the controller does the one live Wikidata verification.
- **Error pattern:** every external lookup degrades to `None`/`[]` and logs; never raises out to the caller (matches `barcode.py`). Specific exceptions only (`requests.RequestException`, `ValueError`). Module-scope named constants for the endpoint URL, timeout, property/class ids.
- **`use_reloader=False`** — the `resolve()` change needs a manual `:5000` restart to go live (controller, gate task).
- **Work on `main` + push.** Commit trailers on every commit:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: <session URL>
  ```

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `barcode.py` | `lookup_wikidata_gtin`; `PRODUCT_SOURCES` tuple + `_product_via_sources` chain helper; rewire `resolve()`'s product-lookup step | Modify |
| `tests/test_wikidata_gtin.py` | parse mocked SPARQL → label; miss → None; failure → None; padded-variant query | Create |
| `tests/test_product_source_chain.py` | chain order: first hit wins, fall-through, all-miss → None | Create |
| existing `tests/test_barcode*.py` / `tests/test_api_barcode.py` | neutralize the new Wikidata fall-through on no-product paths | Modify (gate) |

### Reference (verbatim, from Plan A reconnaissance)

```python
# barcode.py resolve() today — the line to refactor (~line 199):
    product = lookup_product_title(upc)          # <-- becomes the source chain
    if not product:
        return {"upc": upc, "source": "none", "candidates": [], "scanned_platform": None}
# ... everything after consumes `product` (clean -> parse platform -> IGDB -> registry).
UPC_LOOKUP_TIMEOUT = 8
log = logging.getLogger(...)   # existing module logger
import requests                 # already imported
```

---

## Task 1: `barcode.lookup_wikidata_gtin`

**Files:**
- Modify: `barcode.py` (add constants near `UPCITEMDB_SEARCH_URL`; add the function near `lookup_product_title`)
- Test: `tests/test_wikidata_gtin.py`

**Interfaces:**
- Produces:
  - `barcode.WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"`
  - `barcode.WIKIDATA_GTIN_PROPERTY = "P3962"`, `barcode.WIKIDATA_VIDEO_GAME_CLASS = "Q7889"`
  - `barcode.lookup_wikidata_gtin(upc: str, *, url=WIKIDATA_SPARQL_URL, timeout=UPC_LOOKUP_TIMEOUT) -> str | None`
    → the first matching video game's English label, or `None` (miss / any failure). Tries the raw UPC + `"0"+upc` + `"00"+upc` (Wikidata zero-pads GTINs).

- [ ] **Step 1: Write the failing test**

Create `tests/test_wikidata_gtin.py`:

```python
"""barcode.lookup_wikidata_gtin: parse, padded variants, miss/failure -> None."""
import requests

import barcode


class _Resp:
    def __init__(self, payload, exc=None):
        self._payload = payload
        self._exc = exc

    def raise_for_status(self):
        if self._exc:
            raise self._exc

    def json(self):
        return self._payload


def _bindings(label):
    return {"results": {"bindings": [
        {"item": {"value": "http://www.wikidata.org/entity/Q719176"},
         "itemLabel": {"value": label}}]}}


def test_parses_label_from_sparql(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["query"] = params["query"]
        return _Resp(_bindings("Crash Bandicoot"))
    monkeypatch.setattr(barcode.requests, "get", fake_get)
    assert barcode.lookup_wikidata_gtin("711719490029") == "Crash Bandicoot"
    # The query must include the zero-padded variants (Wikidata stores GTIN-13/14).
    assert "711719490029" in captured["query"]
    assert "0711719490029" in captured["query"]
    assert "00711719490029" in captured["query"]
    assert "P3962" in captured["query"] and "Q7889" in captured["query"]


def test_empty_bindings_returns_none(monkeypatch):
    monkeypatch.setattr(barcode.requests, "get",
                        lambda *a, **k: _Resp({"results": {"bindings": []}}))
    assert barcode.lookup_wikidata_gtin("000000000000") is None


def test_network_failure_returns_none(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(barcode.requests, "get", boom)
    assert barcode.lookup_wikidata_gtin("711719490029") is None


def test_bad_json_returns_none(monkeypatch):
    monkeypatch.setattr(barcode.requests, "get",
                        lambda *a, **k: _Resp(None, exc=ValueError("bad json")))
    assert barcode.lookup_wikidata_gtin("711719490029") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_wikidata_gtin.py -v`
Expected: FAIL — `AttributeError: module 'barcode' has no attribute 'lookup_wikidata_gtin'`.

- [ ] **Step 3: Write minimal implementation**

In `barcode.py`, add constants near `UPCITEMDB_SEARCH_URL`:

```python
# Wikidata SPARQL: free, keyless, no rate limit. GTIN property P3962; video game Q7889.
WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"
WIKIDATA_GTIN_PROPERTY = "P3962"
WIKIDATA_VIDEO_GAME_CLASS = "Q7889"
# Wikidata stores GTIN-13/14 (zero-padded); a 12-digit UPC needs padded variants tried.
_GTIN_PAD_WIDTHS = (0, 1, 2)
_WIKIDATA_USER_AGENT = "GameTracker/1.0 (UPC enrichment; contact via app)"
```

Add the function near `lookup_product_title`:

```python
def lookup_wikidata_gtin(upc: str, *, url: str = WIKIDATA_SPARQL_URL,
                         timeout: int = UPC_LOOKUP_TIMEOUT) -> str | None:
    """Return the English label of a video game whose GTIN equals the UPC, or None.

    Wikidata zero-pads GTINs, so the raw UPC plus 1-2 leading zeros are all tried.
    Free + keyless; degrades to None on any failure (never raises)."""
    variants = {("0" * w) + upc for w in _GTIN_PAD_WIDTHS}
    values = " ".join(f'"{v}"' for v in sorted(variants))
    query = (
        "SELECT ?item ?itemLabel WHERE { "
        f"VALUES ?gtin {{ {values} }} "
        f"?item wdt:{WIKIDATA_GTIN_PROPERTY} ?gtin . "
        f"?item wdt:P31/wdt:P279* wd:{WIKIDATA_VIDEO_GAME_CLASS} . "
        'SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } } LIMIT 1')
    try:
        resp = requests.get(url, params={"query": query, "format": "json"},
                            headers={"Accept": "application/sparql-results+json",
                                     "User-Agent": _WIKIDATA_USER_AGENT},
                            timeout=timeout)
        resp.raise_for_status()
        bindings = (resp.json().get("results") or {}).get("bindings") or []
    except (requests.RequestException, ValueError) as exc:
        log.warning("Wikidata GTIN lookup failed for %s: %s", upc, exc)
        return None
    if not bindings:
        return None
    label = ((bindings[0].get("itemLabel") or {}).get("value") or "").strip()
    return label or None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_wikidata_gtin.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run lint + commit**

```bash
ruff check barcode.py tests/test_wikidata_gtin.py
git add barcode.py tests/test_wikidata_gtin.py
git commit -m "feat(enrichment): barcode.lookup_wikidata_gtin (P3962/Q7889, padded GTIN)"
```

---

## Task 2: `PRODUCT_SOURCES` chain + rewire `resolve()`

**Files:**
- Modify: `barcode.py` (add `PRODUCT_SOURCES` + `_product_via_sources` after `lookup_wikidata_gtin`; change the one product-lookup line in `resolve()`)
- Test: `tests/test_product_source_chain.py`

**Interfaces:**
- Produces:
  - `barcode.PRODUCT_SOURCES = (lookup_product_title, lookup_wikidata_gtin)` — ordered tuple of `(upc) -> str | None` sources; new free sources append here.
  - `barcode._product_via_sources(upc: str) -> str | None` — iterates `PRODUCT_SOURCES`, returns the first non-empty title, else `None`.
- Consumes: nothing new; `resolve()` now calls `_product_via_sources(upc)` where it called `lookup_product_title(upc)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_product_source_chain.py`:

```python
"""barcode product-source chain: order, fall-through, all-miss."""
import barcode


def test_first_source_hit_wins(monkeypatch):
    monkeypatch.setattr(barcode, "PRODUCT_SOURCES",
                        (lambda u: "From UPCitemdb", lambda u: "From Wikidata"))
    assert barcode._product_via_sources("123") == "From UPCitemdb"


def test_falls_through_to_second_source(monkeypatch):
    monkeypatch.setattr(barcode, "PRODUCT_SOURCES",
                        (lambda u: None, lambda u: "From Wikidata"))
    assert barcode._product_via_sources("123") == "From Wikidata"


def test_all_miss_returns_none(monkeypatch):
    monkeypatch.setattr(barcode, "PRODUCT_SOURCES",
                        (lambda u: None, lambda u: None))
    assert barcode._product_via_sources("123") is None


def test_empty_string_is_treated_as_miss(monkeypatch):
    monkeypatch.setattr(barcode, "PRODUCT_SOURCES",
                        (lambda u: "", lambda u: "Real Title"))
    assert barcode._product_via_sources("123") == "Real Title"


def test_resolve_uses_the_chain(monkeypatch, tmp_path):
    import models
    models.DB_PATH = tmp_path / "g.db"
    models.init_db()
    models.migrate_db()
    conn = models.get_db()
    # UPCitemdb miss, Wikidata hit -> resolve must reach the upc_api branch, not 'none'.
    monkeypatch.setattr(barcode, "PRODUCT_SOURCES",
                        (lambda u: None, lambda u: "Crash Bandicoot"))
    # No IGDB creds -> no candidates, but product was found via the chain.
    res = barcode.resolve(conn, "711719490029")
    assert res["source"] == "upc_api"
    assert res["product_title"] == "Crash Bandicoot"
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_product_source_chain.py -v`
Expected: FAIL — `AttributeError: module 'barcode' has no attribute '_product_via_sources'` (and `resolve` still calls `lookup_product_title`).

- [ ] **Step 3: Write minimal implementation**

In `barcode.py`, after `lookup_wikidata_gtin`, add:

```python
# Ordered product-title sources tried by resolve(); first non-empty hit wins.
# Append new free sources here — the chain is the extensibility seam.
PRODUCT_SOURCES = (lookup_product_title, lookup_wikidata_gtin)


def _product_via_sources(upc: str) -> str | None:
    """Try each product source in order; return the first non-empty title, else None."""
    for source in PRODUCT_SOURCES:
        title = source(upc)
        if title:
            return title
    return None
```

In `resolve()`, change the single product-lookup line (~line 199) from:

```python
    product = lookup_product_title(upc)
```

to:

```python
    product = _product_via_sources(upc)
```

(Everything after this line is unchanged — `product` still feeds clean → parse platform → IGDB → registry.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_product_source_chain.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Run lint + commit**

```bash
ruff check barcode.py tests/test_product_source_chain.py
git add barcode.py tests/test_product_source_chain.py
git commit -m "feat(enrichment): pluggable PRODUCT_SOURCES chain in resolve()"
```

---

## Task 3: Full gate + regression fix + live Wikidata verification

**Files:** existing `tests/test_api_barcode.py` / `tests/test_barcode.py` (only if the new fall-through breaks them).

- [ ] **Step 1: Full backend suite — catch the fall-through regression**

Run: `uv run python -m pytest`
Expected: investigate any FAIL. The likely break: tests that exercised the "no product" path by mocking `lookup_product_title` → `None` and asserting `source == 'none'`. With the chain, `resolve()` now falls through to `lookup_wikidata_gtin`, which (unmocked) would attempt a REAL network call and/or change the result.

- [ ] **Step 2: Fix any broken no-product test (keep it network-free)**

For each such test, isolate the chain so no real network call happens — patch the whole chain to the single mocked source. Example pattern to apply:

```python
    # Was: monkeypatch.setattr(barcode, "lookup_product_title", lambda u: None)
    # Now also neutralize the Wikidata fall-through so the test stays offline:
    monkeypatch.setattr(barcode, "PRODUCT_SOURCES", (lambda u: None,))
```

(Or, where the test mocks `lookup_product_title` to a real title, add `monkeypatch.setattr(barcode, "lookup_wikidata_gtin", lambda *a, **k: None)` — the first hit wins so Wikidata is never called, but this makes the intent explicit and immune to ordering.)

Run after each fix: `uv run python -m pytest tests/test_api_barcode.py tests/test_barcode.py -v` → PASS.

- [ ] **Step 3: Whole suite + lint green**

Run: `uv run python -m pytest` → all pass.
Run: `ruff check .` → clean.

```bash
git add tests/test_api_barcode.py tests/test_barcode.py
git commit -m "test(enrichment): keep no-product resolve tests offline under the source chain"
```
(Only if files changed.)

- [ ] **Step 4: Controller live Wikidata verification (real network, controller only)**

Confirm `lookup_wikidata_gtin` works against the real endpoint with a known-covered UPC (subagents must NOT do this):

```bash
cd "C:/Users/Jeff/Documents/Projects/Game Tracker"
uv run python -c "import barcode; print(barcode.lookup_wikidata_gtin('711719490029'))"
```
Expected: prints a real video-game label (e.g. `Crash Bandicoot`). Also confirm a miss returns `None`:
```bash
uv run python -c "import barcode; print(barcode.lookup_wikidata_gtin('000000000000'))"
```
Expected: `None`.

- [ ] **Step 5: Controller live server restart (real app)**

Restart `:5000` on the new code so `resolve()` uses the chain (memory `windows-process-and-reloader-hygiene`): kill the python.exe on 5000 via PowerShell `Stop-Process`, then `cd "C:/Users/Jeff/Documents/Projects/Game Tracker" && HOST=0.0.0.0 uv run python app.py` (run_in_background). Optionally spot-check a live `GET /api/barcode/resolve?upc=<a UPCitemdb-miss-but-Wikidata-covered UPC>` now returns a product where it previously returned `source:'none'`.

- [ ] **Step 6: Whole-branch final review**

Dispatch the final whole-branch review (most-capable model) over the Plan-B commit range. Triage Critical/Important; fix before declaring Plan B done. Update `.superpowers/sdd/progress.md`.

---

## Self-Review (against Spec 2 §4)

- **§4.1 source chain (ordered `PRODUCT_SOURCES`, first hit wins, downstream unchanged, module-level tuple, extensible):** Task 2 ✓ — `resolve()` changes by exactly one line; chain is a module-level tuple.
- **§4.2 Wikidata GTIN source (SPARQL, video-game-typed, English label, keyless, None on failure):** Task 1 ✓ — property/class verified live (`P3962`/`Q7889`); zero-pad handling added (real-world correctness beyond the spec's sketch).
- **§7 testing (Wikidata parse → title / failure → None; chain first-hit / fall-through / all-miss):** Tasks 1 + 2 ✓.
- **§9 Wikidata sparsity:** accepted + documented (Global Constraints) — the seam is the deliverable.
- **Backward compatibility:** Task 3 ✓ — the chain preserves default behavior; the only new path is the previously-dead UPCitemdb-miss → Wikidata fallback; offline regression tests fixed.
