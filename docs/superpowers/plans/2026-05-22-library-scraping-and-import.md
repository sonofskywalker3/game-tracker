# Library Scraping + Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Playwright scrapers for the PlayStation/Xbox/Nintendo libraries and a dedicated importer that loads them into Game Tracker, populating the Modern/Legacy views with rename-proof, idempotent identity.

**Architecture:** Three decoupled stages — `scrape` (Playwright reads vendor sites) → `scraped/<vendor>_<date>.json` → `import` (writes `games.db`). Identity rides on a new `game_external_ids` table via a four-step match cascade (exact ID → exact title → fuzzy review → new), so re-scrapes never duplicate renamed games. All DB-facing logic is built and unit-tested with synthetic JSON before any live scraping.

**Tech Stack:** Python 3, Playwright (sync API), BeautifulSoup4, SQLite (`sqlite3`), pytest.

**Spec:** `docs/superpowers/specs/2026-05-22-library-scraping-and-import-design.md`

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `requirements.txt` | Add `playwright`, `beautifulsoup4` | Modify |
| `.gitignore` | Ignore `.pw-profile/`, `.recon/`, `scraped/` (login session + personal HTML/JSON) | Modify |
| `models.py` | `game_external_ids` table in `init_db` + idempotent `migrate_external_ids` hooked into `migrate_db` | Modify |
| `scrapers/__init__.py` | Package marker | Create |
| `scrapers/base.py` | `ScrapedGame` record; JSON writer/reader; `persistent_browser`; `autoscroll`; path constants | Create |
| `scrapers/playstation.py` | `VENDOR_URL`, platform-label map, `parse(html)` | Create |
| `scrapers/xbox.py` | Same, for Xbox | Create |
| `scrapers/nintendo.py` | Same, for Nintendo | Create |
| `scrape_libraries.py` | CLI: `--vendor`, `--recon`; generic navigation + autoscroll → per-vendor `parse` | Create |
| `import_scraped.py` | Match cascade + importer + CLI (`--dry-run`, `--accept-fuzzy`) | Create |
| `tests/test_external_ids_migration.py` | Migration creates table; idempotent | Create |
| `tests/test_scraper_base.py` | `ScrapedGame` defaults; JSON round-trip; source validation | Create |
| `tests/test_import_scraped.py` | Match cascade, legacy row creation, curation preservation, idempotency, cross-vendor, dry-run, fuzzy | Create |
| `tests/fixtures/<vendor>_library_sample.html` | **Synthetic** sample pages (fake titles, real DOM structure) — committed | Create |
| `tests/test_parse_<vendor>.py` | Per-vendor `parse` extracts the synthetic games | Create |

**Conventions:** Run all commands from the project root. Tests: `python -m pytest`. New code uses `logging` (not `print`), type hints, named constants (per `CLAUDE.md`). Work on a **feature branch**, not `main`. Commit after every task. **Never commit** `.recon/`, `scraped/`, or `.pw-profile/`; only synthetic fixtures are committed.

---

### Task 1: Dependencies + gitignore

**Files:**
- Modify: `requirements.txt`
- Modify: `.gitignore`

- [ ] **Step 1: Add dependencies to `requirements.txt`**

Append:

```
playwright>=1.44.0
beautifulsoup4>=4.12.0
```

- [ ] **Step 2: Install them + the Chromium browser**

Run: `python -m pip install -r requirements.txt`
Expected: playwright + beautifulsoup4 install (or "already satisfied").

Run: `python -m playwright install chromium`
Expected: Chromium downloads (or "is already installed").

- [ ] **Step 3: Add scraper artifacts to `.gitignore`**

In `.gitignore`, after the `# ---- Personal library data (kept local-only) ----` block, add:

```
# ---- Scraper artifacts (local-only; contain login session + personal library) ----
.pw-profile/
.recon/
scraped/
```

- [ ] **Step 4: Verify the ignores work**

Run: `git check-ignore .pw-profile/x .recon/x scraped/x`
Expected: all three paths echo back (meaning they are ignored).

- [ ] **Step 5: Commit**

```bash
git add requirements.txt .gitignore
git commit -m "chore: add playwright + beautifulsoup deps and ignore scraper artifacts"
```

---

### Task 2: `game_external_ids` schema + idempotent migration

**Files:**
- Modify: `models.py`
- Create: `tests/test_external_ids_migration.py`

- [ ] **Step 1: Write the failing migration test**

Create `tests/test_external_ids_migration.py`:

```python
import sqlite3

import pytest

from models import migrate_external_ids


def _conn_without_table():
    """A DB with games but no game_external_ids (pre-migration shape)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE games (id INTEGER PRIMARY KEY, title TEXT)")
    return conn


def test_migration_creates_table():
    conn = _conn_without_table()
    migrate_external_ids(conn)
    cols = {c[1] for c in conn.execute("PRAGMA table_info(game_external_ids)").fetchall()}
    assert cols == {"game_id", "source", "external_id", "source_title", "created_at"}


def test_migration_is_idempotent():
    conn = _conn_without_table()
    migrate_external_ids(conn)
    migrate_external_ids(conn)  # second run must not raise


def test_source_external_id_is_unique():
    conn = _conn_without_table()
    migrate_external_ids(conn)
    conn.execute("INSERT INTO games (id, title) VALUES (1, 'X')")
    conn.execute(
        "INSERT INTO game_external_ids (game_id, source, external_id) VALUES (1, 'playstation', 'C1')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO game_external_ids (game_id, source, external_id) VALUES (1, 'playstation', 'C1')"
        )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_external_ids_migration.py -v`
Expected: FAIL with `ImportError: cannot import name 'migrate_external_ids'`.

- [ ] **Step 3: Implement the migration helper**

In `models.py`, add this function just above `def migrate_db():`:

```python
def migrate_external_ids(conn):
    """Create the game_external_ids identity table if missing. Idempotent.

    One game carries many rows here (one per vendor, even per edition); identity
    is (source, external_id), so re-scrapes match by stable vendor id and never
    duplicate a game the user has renamed.
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS game_external_ids (
            game_id      INTEGER NOT NULL,
            source       TEXT    NOT NULL,
            external_id  TEXT    NOT NULL,
            source_title TEXT,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (source, external_id),
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_game_external_ids_game
            ON game_external_ids(game_id);
    """)
    conn.commit()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_external_ids_migration.py -v`
Expected: 3 passed.

- [ ] **Step 5: Add the table to the fresh-DB schema + hook the migration**

In `models.py` `init_db()`, inside the `conn.executescript("""...""")` block, add this table definition immediately after the `game_platforms` table:

```sql
        -- External vendor IDs (rename-proof identity for scraped imports)
        CREATE TABLE IF NOT EXISTS game_external_ids (
            game_id      INTEGER NOT NULL,
            source       TEXT    NOT NULL,
            external_id  TEXT    NOT NULL,
            source_title TEXT,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (source, external_id),
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
        );
```

In `models.py` `migrate_db()`, immediately after the `migrate_platform_category(conn)` line, add:

```python
    # Add the external-ids identity table
    migrate_external_ids(conn)
```

- [ ] **Step 6: Run the full suite + apply to the real DB**

Run: `python -m pytest -v`
Expected: all tests pass.

Run: `python -c "import models; models.migrate_db()"`
Expected: runs without error against the live `games.db`.

Run: `python -c "import models; c=models.get_db(); print([r[1] for r in c.execute('PRAGMA table_info(game_external_ids)')])"`
Expected: `['game_id', 'source', 'external_id', 'source_title', 'created_at']`.

- [ ] **Step 7: Commit**

```bash
git add models.py tests/test_external_ids_migration.py
git commit -m "feat: add game_external_ids identity table + idempotent migration"
```

---

### Task 3: Scraper foundation — `scrapers/base.py`

**Files:**
- Create: `scrapers/__init__.py`
- Create: `scrapers/base.py`
- Create: `tests/test_scraper_base.py`

- [ ] **Step 1: Create the package marker**

Create `scrapers/__init__.py` (empty file):

```python
```

- [ ] **Step 2: Write the failing base test**

Create `tests/test_scraper_base.py`:

```python
import pytest

from scrapers.base import ScrapedGame, write_scrape, read_scrape


def test_scraped_game_defaults_source_title_to_title():
    g = ScrapedGame(title="Hades", platform="PS5", source="playstation")
    assert g.source_title == "Hades"


def test_write_and_read_roundtrip(tmp_path):
    games = [
        ScrapedGame("Hades", "PS5", "playstation", external_id="P1", cover_url="u"),
        ScrapedGame("Celeste", "Switch", "playstation"),
    ]
    out = write_scrape("playstation", games, out_dir=tmp_path)
    assert out.exists()
    rows = read_scrape(out)
    assert [r["title"] for r in rows] == ["Hades", "Celeste"]
    assert rows[0]["external_id"] == "P1"
    assert rows[1]["source_title"] == "Celeste"


def test_write_rejects_unknown_source(tmp_path):
    with pytest.raises(ValueError):
        write_scrape("steam", [], out_dir=tmp_path)
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python -m pytest tests/test_scraper_base.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scrapers.base'`.

- [ ] **Step 4: Implement `scrapers/base.py`**

Create `scrapers/base.py`:

```python
"""Shared scraper infrastructure: the ScrapedGame record, the persistent-browser
lifecycle, and the normalized-JSON writer/reader.

Scraping never imports models or touches the database — it only produces JSON.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Module-level path constants (mirror models.DB_PATH).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = PROJECT_ROOT / ".pw-profile"   # persistent Playwright login session
RECON_DIR = PROJECT_ROOT / ".recon"          # raw captured HTML (personal; gitignored)
SCRAPE_DIR = PROJECT_ROOT / "scraped"        # normalized JSON output (gitignored)

VALID_SOURCES = frozenset({"playstation", "xbox", "nintendo"})


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

    def __post_init__(self) -> None:
        if self.source_title is None:
            self.source_title = self.title


def write_scrape(source: str, games: list[ScrapedGame], out_dir: Path = SCRAPE_DIR) -> Path:
    """Write a normalized scrape file and return its path."""
    if source not in VALID_SOURCES:
        raise ValueError(f"unknown source: {source!r}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    payload = {
        "source": source,
        "scraped_at": now.isoformat(),
        "count": len(games),
        "games": [asdict(g) for g in games],
    }
    out_path = out_dir / f"{source}_{now:%Y%m%d}.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("wrote %d games to %s", len(games), out_path)
    return out_path


def read_scrape(path: Path) -> list[dict]:
    """Read the games list out of a normalized scrape file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["games"]


@contextmanager
def persistent_browser(headless: bool = False):
    """Yield a Playwright page backed by a persistent profile (login persists).

    Live shell — verified manually, not unit-tested. First run per vendor opens a
    real window for interactive login + 2FA; the session is reused thereafter.
    """
    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=headless,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            yield page
        finally:
            context.close()


def autoscroll(page, max_rounds: int = 60, pause_ms: int = 500) -> None:
    """Scroll to the bottom repeatedly to trigger lazy-loaded library items."""
    prev_height = 0
    for _ in range(max_rounds):
        page.mouse.wheel(0, 20000)
        page.wait_for_timeout(pause_ms)
        height = page.evaluate("document.body.scrollHeight")
        if height == prev_height:
            break
        prev_height = height
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/test_scraper_base.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add scrapers/__init__.py scrapers/base.py tests/test_scraper_base.py
git commit -m "feat: scraper base (ScrapedGame, JSON IO, persistent browser)"
```

---

### Task 4: The importer — match cascade + `import_scraped.py`

This is the heart of the project and is fully testable with hand-written JSON (no scrapers needed).

**Files:**
- Create: `import_scraped.py`
- Create: `tests/test_import_scraped.py`

- [ ] **Step 1: Write the failing importer tests**

Create `tests/test_import_scraped.py`:

```python
import models
import import_scraped as imp


def _add_existing_game(title, platform_short="PS4", category="modern_console",
                       status="playing", rating=4):
    """Insert a curated game the way the live DB holds one."""
    conn = models.get_db()
    conn.execute(
        "INSERT OR IGNORE INTO platforms (name, short_name, category) VALUES (?, ?, ?)",
        (platform_short, platform_short, category),
    )
    display = models.clean_title(title)
    norm = models.normalize_title(display)
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)", (display, norm))
    gid = conn.execute("SELECT id FROM games WHERE normalized_title = ?", (norm,)).fetchone()[0]
    pid = conn.execute("SELECT id FROM platforms WHERE short_name = ?", (platform_short,)).fetchone()[0]
    conn.execute("INSERT OR IGNORE INTO game_platforms (game_id, platform_id) VALUES (?, ?)", (gid, pid))
    conn.execute("INSERT INTO user_ratings (game_id, status, rating) VALUES (?, ?, ?)", (gid, status, rating))
    conn.commit()
    conn.close()
    return gid


def _g(title, platform, source="playstation", external_id=None, cover_url=None):
    return {"title": title, "platform": platform, "source": source,
            "external_id": external_id, "cover_url": cover_url, "source_title": title}


def test_new_legacy_game_creates_legacy_platform(temp_db):
    conn = models.get_db()
    stats = imp.import_games(conn, [_g("Tomba", "PS3", external_id="X1")], "playstation")
    conn.commit()
    assert stats.new_games == 1
    cat = conn.execute("SELECT category FROM platforms WHERE short_name = 'PS3'").fetchone()[0]
    assert cat == "legacy_console"
    conn.close()


def test_external_id_match_survives_rename(temp_db):
    conn = models.get_db()
    imp.import_games(conn, [_g("NieR:Automata", "PS4", external_id="CUSA07")], "playstation")
    conn.commit()
    conn.execute("UPDATE games SET title = 'Nier', normalized_title = ?",
                 (models.normalize_title("Nier"),))
    conn.commit()
    stats = imp.import_games(conn, [_g("NieR:Automata", "PS4", external_id="CUSA07")], "playstation")
    conn.commit()
    assert stats.external_id_matches == 1
    assert stats.new_games == 0
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
    conn.close()


def test_existing_curation_is_preserved(temp_db):
    gid = _add_existing_game("Hades", "PS4", status="completed", rating=4)
    conn = models.get_db()
    imp.import_games(conn, [_g("Hades", "PS4", external_id="C1")], "playstation")
    conn.commit()
    row = conn.execute("SELECT status, rating FROM user_ratings WHERE game_id = ?", (gid,)).fetchone()
    assert row["status"] == "completed"
    assert row["rating"] == 4
    conn.close()


def test_import_is_idempotent(temp_db):
    conn = models.get_db()
    games = [_g("Celeste", "PS4", external_id="C2")]
    imp.import_games(conn, games, "playstation")
    conn.commit()
    stats = imp.import_games(conn, games, "playstation")
    conn.commit()
    assert stats.new_games == 0
    assert stats.platform_links_added == 0
    assert stats.external_ids_added == 0
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
    conn.close()


def test_cross_vendor_unifies_into_one_game(temp_db):
    conn = models.get_db()
    imp.import_games(conn, [_g("Hades", "PS5", "playstation", external_id="P1")], "playstation")
    conn.commit()
    imp.import_games(conn, [_g("Hades", "Xbox", "xbox", external_id="X9")], "xbox")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
    gid = conn.execute("SELECT id FROM games").fetchone()[0]
    plats = {r[0] for r in conn.execute(
        "SELECT p.short_name FROM platforms p "
        "JOIN game_platforms gp ON gp.platform_id = p.id WHERE gp.game_id = ?", (gid,))}
    assert plats == {"PS5", "Xbox"}
    assert conn.execute("SELECT COUNT(*) FROM game_external_ids WHERE game_id = ?", (gid,)).fetchone()[0] == 2
    conn.close()


def test_dry_run_writes_nothing(temp_db):
    conn = models.get_db()
    stats = imp.import_games(conn, [_g("Bastion", "PS4", external_id="B1")], "playstation", dry_run=True)
    conn.commit()
    assert stats.new_games == 1
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0
    conn.close()


def test_fuzzy_confirm_merges(temp_db):
    _add_existing_game("The Legend of Zelda: Breath of the Wild", "Switch")
    conn = models.get_db()
    stats = imp.import_games(
        conn,
        [_g("Legend of Zelda Breath of the Wild", "Switch", "nintendo", external_id="N1")],
        "nintendo",
        confirm_fn=lambda *a: True,
    )
    conn.commit()
    assert stats.fuzzy_confirmed == 1
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
    conn.close()


def test_fuzzy_reject_creates_new(temp_db):
    _add_existing_game("The Legend of Zelda: Breath of the Wild", "Switch")
    conn = models.get_db()
    stats = imp.import_games(
        conn,
        [_g("Legend of Zelda Breath of the Wild", "Switch", "nintendo", external_id="N1")],
        "nintendo",
        confirm_fn=lambda *a: False,
    )
    conn.commit()
    assert stats.fuzzy_rejected == 1
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 2
    conn.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_import_scraped.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'import_scraped'`.

- [ ] **Step 3: Implement `import_scraped.py` (resolver + helpers + importer)**

Create `import_scraped.py`:

```python
"""Load scraped vendor library JSON into games.db with rename-proof identity.

Match cascade per scraped game: exact (source, external_id) -> exact normalized
title -> fuzzy title (needs confirmation) -> new game. Never overwrites existing
user curation (status / rating / notes / series); only adds platform links and
external ids. Idempotent; supports --dry-run.
"""
from __future__ import annotations

import argparse
import difflib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import models

logger = logging.getLogger(__name__)

FUZZY_MATCH_THRESHOLD = 0.85
DEFAULT_STATUS = "backlog"

# Display names for platform rows created on the fly (short_name -> name).
PLATFORM_DISPLAY_NAMES = {
    "PS5": "PlayStation 5", "PS4": "PlayStation 4", "PS3": "PlayStation 3",
    "Vita": "PlayStation Vita", "PSP": "PlayStation Portable",
    "Xbox": "Xbox", "X360": "Xbox 360", "OGXbox": "Xbox (original)",
    "Switch": "Nintendo Switch", "WiiU": "Wii U", "3DS": "Nintendo 3DS",
}


def match_key(title: str) -> str:
    """Title normalization for matching (mirrors how migrate_db stores it)."""
    return models.normalize_title(models.clean_title(title))


@dataclass
class MatchResult:
    game_id: Optional[int]
    method: str               # "external_id" | "title" | "fuzzy" | "new"
    score: float = 1.0
    matched_title: Optional[str] = None


def resolve_game(conn, source: str, external_id: Optional[str], title: str) -> MatchResult:
    """Resolve a scraped game to an existing game_id via the match cascade."""
    if external_id:
        row = conn.execute(
            "SELECT game_id FROM game_external_ids WHERE source = ? AND external_id = ?",
            (source, external_id),
        ).fetchone()
        if row:
            return MatchResult(row[0], "external_id")

    key = match_key(title)
    row = conn.execute("SELECT id FROM games WHERE normalized_title = ?", (key,)).fetchone()
    if row:
        return MatchResult(row[0], "title")

    best = MatchResult(None, "new", 0.0, None)
    for g in conn.execute("SELECT id, title, normalized_title FROM games").fetchall():
        score = difflib.SequenceMatcher(None, key, g["normalized_title"]).ratio()
        if score > best.score:
            best = MatchResult(g["id"], "fuzzy", score, g["title"])
    if best.score >= FUZZY_MATCH_THRESHOLD:
        return best
    return MatchResult(None, "new", best.score, best.matched_title)


@dataclass
class ImportStats:
    new_games: int = 0
    external_id_matches: int = 0
    title_matches: int = 0
    fuzzy_confirmed: int = 0
    fuzzy_rejected: int = 0
    platform_links_added: int = 0
    external_ids_added: int = 0
    ratings_created: int = 0
    platforms_created: list = field(default_factory=list)
    fuzzy_candidates: list = field(default_factory=list)  # (scraped, existing, score)

    def merge(self, other: "ImportStats") -> None:
        self.new_games += other.new_games
        self.external_id_matches += other.external_id_matches
        self.title_matches += other.title_matches
        self.fuzzy_confirmed += other.fuzzy_confirmed
        self.fuzzy_rejected += other.fuzzy_rejected
        self.platform_links_added += other.platform_links_added
        self.external_ids_added += other.external_ids_added
        self.ratings_created += other.ratings_created
        self.platforms_created += other.platforms_created
        self.fuzzy_candidates += other.fuzzy_candidates


def _create_game(conn, game: dict) -> int:
    display = models.clean_title(game["title"])
    cur = conn.execute(
        "INSERT INTO games (title, normalized_title, cover_url) VALUES (?, ?, ?)",
        (display, models.normalize_title(display), game.get("cover_url")),
    )
    return cur.lastrowid


def _apply_or_plan(conn, game_id, game, source, stats, *, dry_run, is_new) -> None:
    """Add platform link + external id + default rating. Read-only when dry_run.

    When is_new, the game has no existing rows, so every sub-add is new (and the
    game_id may be None during a dry run — never queried in that case).
    """
    short = game["platform"]

    prow = conn.execute("SELECT id FROM platforms WHERE short_name = ?", (short,)).fetchone()
    if prow:
        platform_id = prow[0]
    else:
        stats.platforms_created.append((short, models.classify_platform(short)))
        if dry_run:
            platform_id = None
        else:
            name = PLATFORM_DISPLAY_NAMES.get(short, short)
            platform_id = conn.execute(
                "INSERT INTO platforms (name, short_name, category) VALUES (?, ?, ?)",
                (name, short, models.classify_platform(short)),
            ).lastrowid

    link_is_new = is_new or platform_id is None or conn.execute(
        "SELECT 1 FROM game_platforms WHERE game_id = ? AND platform_id = ?",
        (game_id, platform_id),
    ).fetchone() is None
    if link_is_new:
        stats.platform_links_added += 1
        if not dry_run:
            conn.execute(
                "INSERT OR IGNORE INTO game_platforms (game_id, platform_id) VALUES (?, ?)",
                (game_id, platform_id),
            )

    ext = game.get("external_id")
    if ext:
        ext_is_new = is_new or conn.execute(
            "SELECT 1 FROM game_external_ids WHERE source = ? AND external_id = ?",
            (source, ext),
        ).fetchone() is None
        if ext_is_new:
            stats.external_ids_added += 1
            if not dry_run:
                conn.execute(
                    "INSERT OR IGNORE INTO game_external_ids "
                    "(game_id, source, external_id, source_title) VALUES (?, ?, ?, ?)",
                    (game_id, source, ext, game.get("source_title") or game["title"]),
                )

    rating_is_new = is_new or conn.execute(
        "SELECT 1 FROM user_ratings WHERE game_id = ?", (game_id,)
    ).fetchone() is None
    if rating_is_new:
        stats.ratings_created += 1
        if not dry_run:
            conn.execute(
                "INSERT OR IGNORE INTO user_ratings (game_id, status) VALUES (?, ?)",
                (game_id, DEFAULT_STATUS),
            )


def _interactive_confirm(scraped: str, existing: str, score: float) -> bool:
    answer = input(f"  Merge '{scraped}' into existing '{existing}' "
                   f"(similarity {score:.2f})? [y/N] ")
    return answer.strip().lower() == "y"


def import_games(conn, games: list[dict], source: str, *, dry_run: bool = False,
                 confirm_fn: Callable[[str, str, float], bool] = _interactive_confirm) -> ImportStats:
    """Reconcile a list of scraped game dicts into the DB. Returns stats."""
    stats = ImportStats()
    for game in games:
        m = resolve_game(conn, source, game.get("external_id"), game["title"])
        is_new = False

        if m.method == "external_id":
            stats.external_id_matches += 1
            game_id = m.game_id
        elif m.method == "title":
            stats.title_matches += 1
            game_id = m.game_id
        elif m.method == "fuzzy":
            stats.fuzzy_candidates.append((game["title"], m.matched_title, round(m.score, 3)))
            if dry_run:
                continue
            if confirm_fn(game["title"], m.matched_title, m.score):
                stats.fuzzy_confirmed += 1
                game_id = m.game_id
            else:
                stats.fuzzy_rejected += 1
                stats.new_games += 1
                is_new = True
                game_id = _create_game(conn, game)
        else:  # new
            stats.new_games += 1
            is_new = True
            game_id = None if dry_run else _create_game(conn, game)

        _apply_or_plan(conn, game_id, game, source, stats, dry_run=dry_run, is_new=is_new)
    return stats
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_import_scraped.py -v`
Expected: 9 passed.

- [ ] **Step 5: Add the CLI to `import_scraped.py`**

Append to `import_scraped.py`:

```python
def _iter_json_paths(paths: list[str]):
    for p in paths:
        path = Path(p)
        if path.is_dir():
            yield from sorted(path.glob("*.json"))
        else:
            yield path


def _log_summary(total: ImportStats, *, dry_run: bool) -> None:
    label = "WOULD CHANGE (dry run)" if dry_run else "CHANGED"
    logger.info("--- %s ---", label)
    logger.info("new games:          %d", total.new_games)
    logger.info("matched by id:      %d", total.external_id_matches)
    logger.info("matched by title:   %d", total.title_matches)
    logger.info("platform links:     +%d", total.platform_links_added)
    logger.info("external ids:       +%d", total.external_ids_added)
    if total.platforms_created:
        logger.info("new platform rows:  %s", total.platforms_created)
    if total.fuzzy_candidates:
        logger.info("FUZZY — needs your review (%d):", len(total.fuzzy_candidates))
        for scraped, existing, score in total.fuzzy_candidates:
            logger.info("  '%s'  ~  '%s'  (%.2f)", scraped, existing, score)


def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Import scraped library JSON into games.db")
    parser.add_argument("paths", nargs="+", help="JSON files or a directory of them (e.g. scraped)")
    parser.add_argument("--dry-run", action="store_true", help="preview changes; write nothing")
    parser.add_argument("--accept-fuzzy", action="store_true",
                        help="auto-confirm fuzzy matches instead of prompting")
    args = parser.parse_args(argv)

    models.migrate_db()  # ensure schema (incl. game_external_ids) is current
    conn = models.get_db()
    confirm = (lambda *a: True) if args.accept_fuzzy else _interactive_confirm

    total = ImportStats()
    for path in _iter_json_paths(args.paths):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        stats = import_games(conn, data["games"], data["source"],
                             dry_run=args.dry_run, confirm_fn=confirm)
        total.merge(stats)
        logger.info("%s (%s): +%d new, %d id, %d title, %d fuzzy",
                    Path(path).name, data["source"], stats.new_games,
                    stats.external_id_matches, stats.title_matches, len(stats.fuzzy_candidates))

    if args.dry_run:
        logger.info("DRY RUN — no changes written.")
    else:
        conn.commit()
    _log_summary(total, dry_run=args.dry_run)
    conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -v`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add import_scraped.py tests/test_import_scraped.py
git commit -m "feat: scraped-library importer with rename-proof match cascade"
```

---

### Task 5: PlayStation scraper (recon → fixture → TDD parse → live scrape)

> **The one recon-dependent value in this plan is the set of CSS selectors.** Step 1 captures your real library DOM (gitignored); you then set the selector constants and build the *synthetic* fixture (fake titles, real structure) so the test exercises the real selectors. Everything else is concrete.

**Files:**
- Create: `scrapers/playstation.py`
- Create: `scrape_libraries.py`
- Create: `tests/fixtures/playstation_library_sample.html`
- Create: `tests/test_parse_playstation.py`

- [ ] **Step 1: Create the scraper stub + CLI, then run recon (LIVE)**

Create `scrapers/playstation.py`:

```python
"""PlayStation library scraper: parse library.playstation.com into ScrapedGame."""
from __future__ import annotations

from bs4 import BeautifulSoup

from scrapers.base import ScrapedGame

VENDOR_URL = "https://library.playstation.com/recently-purchased"
SOURCE = "playstation"

# Vendor platform label (UPPER, spaces removed) -> canonical short_name.
PLATFORM_LABELS = {"PS5": "PS5", "PS4": "PS4", "PS3": "PS3", "PSVITA": "Vita", "PSP": "PSP"}
DEFAULT_PLATFORM = "PS4"  # modern default when badge is missing/unknown

# --- recon-derived selectors (confirm against .recon/playstation.html in Step 1) ---
CARD_SELECTOR = "li.psw-grid-list--item"
TITLE_SELECTOR = ".psw-game-title"
COVER_SELECTOR = "img"
PLATFORM_SELECTOR = ".psw-platform-badge"
ID_ATTR = "data-telemetry-id"


def _platform_of(card) -> str:
    el = card.select_one(PLATFORM_SELECTOR)
    label = el.get_text(strip=True).upper().replace(" ", "") if el else ""
    return PLATFORM_LABELS.get(label, DEFAULT_PLATFORM)


def parse(html: str) -> list[ScrapedGame]:
    soup = BeautifulSoup(html, "html.parser")
    games = []
    for card in soup.select(CARD_SELECTOR):
        title_el = card.select_one(TITLE_SELECTOR)
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        img = card.select_one(COVER_SELECTOR)
        games.append(ScrapedGame(
            title=title,
            platform=_platform_of(card),
            source=SOURCE,
            external_id=card.get(ID_ATTR),
            cover_url=img.get("src") if img else None,
            source_title=title,
        ))
    return games
```

Create `scrape_libraries.py`:

```python
"""CLI to scrape vendor libraries into normalized JSON (scraped/<vendor>_<date>.json).

  python scrape_libraries.py --vendor playstation
  python scrape_libraries.py --vendor all
  python scrape_libraries.py --recon --vendor playstation   # capture raw HTML
"""
from __future__ import annotations

import argparse
import logging

from scrapers.base import RECON_DIR, autoscroll, persistent_browser, write_scrape
from scrapers import playstation

logger = logging.getLogger(__name__)

SCRAPERS = {
    "playstation": playstation,
    # "xbox": xbox,        # added in Task 6
    # "nintendo": nintendo,  # added in Task 7
}


def _load_library(page, mod):
    page.goto(mod.VENDOR_URL)
    input(f"Log in if needed, wait for your {mod.SOURCE} library to FULLY load, then press Enter...")
    autoscroll(page)


def run_recon(vendor: str) -> None:
    mod = SCRAPERS[vendor]
    RECON_DIR.mkdir(parents=True, exist_ok=True)
    with persistent_browser(headless=False) as page:
        _load_library(page, mod)
        out = RECON_DIR / f"{vendor}.html"
        out.write_text(page.content(), encoding="utf-8")
        logger.info("saved %s (%d bytes)", out, out.stat().st_size)


def run_scrape(vendor: str) -> None:
    mod = SCRAPERS[vendor]
    with persistent_browser(headless=False) as page:
        _load_library(page, mod)
        games = mod.parse(page.content())
    write_scrape(vendor, games)
    logger.info("scraped %d %s games", len(games), vendor)


def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Scrape vendor libraries to JSON")
    parser.add_argument("--vendor", required=True, choices=list(SCRAPERS) + ["all"])
    parser.add_argument("--recon", action="store_true", help="save raw HTML to .recon/")
    args = parser.parse_args(argv)
    vendors = list(SCRAPERS) if args.vendor == "all" else [args.vendor]
    for vendor in vendors:
        (run_recon if args.recon else run_scrape)(vendor)


if __name__ == "__main__":
    main()
```

Run (LIVE — a browser opens; log in once): `python scrape_libraries.py --recon --vendor playstation`
Expected: after you press Enter, `.recon/playstation.html` is written. Open it and find: the repeating game-card element + its class, where the title text lives, where the cover `<img>` is, where the stable id is (an attribute or the product `href`), and the platform badge text. **Update the selector constants in `scrapers/playstation.py` to match.**

- [ ] **Step 2: Build the synthetic fixture (fake titles, real structure)**

Create `tests/fixtures/playstation_library_sample.html` using the **real element/class names you found** (replace the ones below if they differ), with three fake games:

```html
<ul>
  <li class="psw-grid-list--item" data-telemetry-id="PPSA01">
    <span class="psw-game-title">Returnal</span>
    <span class="psw-platform-badge">PS5</span>
    <img src="https://example/returnal.png">
  </li>
  <li class="psw-grid-list--item" data-telemetry-id="CUSA02">
    <span class="psw-game-title">Bloodborne</span>
    <span class="psw-platform-badge">PS4</span>
    <img src="https://example/bloodborne.png">
  </li>
  <li class="psw-grid-list--item" data-telemetry-id="NPEB03">
    <span class="psw-game-title">Demon's Souls Classic</span>
    <span class="psw-platform-badge">PS3</span>
    <img src="https://example/demons.png">
  </li>
</ul>
```

> Keep the fixture's classes/attributes **identical** to the selector constants in `playstation.py` — that's what makes the test verify the real selectors.

- [ ] **Step 3: Write the failing parse test**

Create `tests/test_parse_playstation.py`:

```python
from pathlib import Path

from scrapers.playstation import parse

FIXTURE = Path(__file__).parent / "fixtures" / "playstation_library_sample.html"


def _parsed():
    return {g.title: g for g in parse(FIXTURE.read_text(encoding="utf-8"))}


def test_parses_all_cards():
    assert len(parse(FIXTURE.read_text(encoding="utf-8"))) == 3


def test_extracts_platform_and_id():
    by_title = _parsed()
    assert by_title["Returnal"].platform == "PS5"
    assert by_title["Returnal"].external_id == "PPSA01"
    assert by_title["Bloodborne"].platform == "PS4"
    assert by_title["Demon's Souls Classic"].platform == "PS3"


def test_source_and_cover():
    g = _parsed()["Returnal"]
    assert g.source == "playstation"
    assert g.cover_url.endswith("returnal.png")
```

- [ ] **Step 4: Run the parse test**

Run: `python -m pytest tests/test_parse_playstation.py -v`
Expected: PASS once the fixture classes and `playstation.py` selectors agree. If it fails, reconcile the selector constants with the fixture (and with `.recon/playstation.html`).

- [ ] **Step 5: Live scrape to JSON (verify end-to-end)**

Run (LIVE): `python scrape_libraries.py --vendor playstation`
Expected: after the library loads + autoscroll, `scraped/playstation_<date>.json` is written. Open it; confirm the game count looks right and entries have `title`, `platform`, `external_id`, `cover_url`. If counts are low, the library didn't fully lazy-load — re-run and wait longer before pressing Enter.

- [ ] **Step 6: Commit (synthetic fixture only — never `.recon/` or `scraped/`)**

```bash
git add scrapers/playstation.py scrape_libraries.py tests/fixtures/playstation_library_sample.html tests/test_parse_playstation.py
git commit -m "feat: PlayStation library scraper + parse tests"
```

---

### Task 6: Xbox scraper

Same shape as Task 5. Confirm the best owned-games page during recon (the order/billing history or the Xbox web library).

**Files:**
- Create: `scrapers/xbox.py`
- Modify: `scrape_libraries.py` (register `xbox`)
- Create: `tests/fixtures/xbox_library_sample.html`
- Create: `tests/test_parse_xbox.py`

- [ ] **Step 1: Create the Xbox scraper stub, then run recon (LIVE)**

Create `scrapers/xbox.py`:

```python
"""Xbox library scraper: parse the Microsoft/Xbox owned-games page into ScrapedGame."""
from __future__ import annotations

from bs4 import BeautifulSoup

from scrapers.base import ScrapedGame

VENDOR_URL = "https://account.microsoft.com/billing/orders"
SOURCE = "xbox"

# Modern Xbox (One/Series) reuses the existing coarse "Xbox" row; older eras split out.
PLATFORM_LABELS = {
    "XBOXSERIESX|S": "Xbox", "XBOXSERIES": "Xbox", "XBOXONE": "Xbox", "XBOX": "Xbox",
    "XBOX360": "X360", "XBOXORIGINAL": "OGXbox",
}
DEFAULT_PLATFORM = "Xbox"

# --- recon-derived selectors (confirm against .recon/xbox.html in Step 1) ---
CARD_SELECTOR = "div.product-row"
TITLE_SELECTOR = ".product-title"
COVER_SELECTOR = "img"
PLATFORM_SELECTOR = ".product-platform"
ID_ATTR = "data-product-id"


def _platform_of(card) -> str:
    el = card.select_one(PLATFORM_SELECTOR)
    label = el.get_text(strip=True).upper().replace(" ", "") if el else ""
    return PLATFORM_LABELS.get(label, DEFAULT_PLATFORM)


def parse(html: str) -> list[ScrapedGame]:
    soup = BeautifulSoup(html, "html.parser")
    games = []
    for card in soup.select(CARD_SELECTOR):
        title_el = card.select_one(TITLE_SELECTOR)
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        img = card.select_one(COVER_SELECTOR)
        games.append(ScrapedGame(
            title=title,
            platform=_platform_of(card),
            source=SOURCE,
            external_id=card.get(ID_ATTR),
            cover_url=img.get("src") if img else None,
            source_title=title,
        ))
    return games
```

In `scrape_libraries.py`, add the import and registry entry:

```python
from scrapers import playstation, xbox
```
```python
SCRAPERS = {
    "playstation": playstation,
    "xbox": xbox,
    # "nintendo": nintendo,  # added in Task 7
}
```

Run (LIVE): `python scrape_libraries.py --recon --vendor xbox`
Expected: `.recon/xbox.html` written. Inspect it; if the order/billing page is awkward, try the Xbox web "My games" page instead and set `VENDOR_URL` accordingly. Update the selector constants to match the real DOM. Record which platform-label strings appear so `PLATFORM_LABELS` keys match (UPPER, spaces removed).

- [ ] **Step 2: Build the synthetic fixture**

Create `tests/fixtures/xbox_library_sample.html` using the real classes you found, with three fake games (a Series/One title → `Xbox`, and an `Xbox 360` title → `X360`):

```html
<div>
  <div class="product-row" data-product-id="9NXBOX01">
    <span class="product-title">Forza Horizon Test</span>
    <span class="product-platform">Xbox Series X|S</span>
    <img src="https://example/forza.png">
  </div>
  <div class="product-row" data-product-id="9NXBOX02">
    <span class="product-title">Halo Sample</span>
    <span class="product-platform">Xbox One</span>
    <img src="https://example/halo.png">
  </div>
  <div class="product-row" data-product-id="9NXBOX03">
    <span class="product-title">Gears Classic</span>
    <span class="product-platform">Xbox 360</span>
    <img src="https://example/gears.png">
  </div>
</div>
```

- [ ] **Step 3: Write the failing parse test**

Create `tests/test_parse_xbox.py`:

```python
from pathlib import Path

from scrapers.xbox import parse

FIXTURE = Path(__file__).parent / "fixtures" / "xbox_library_sample.html"


def _parsed():
    return {g.title: g for g in parse(FIXTURE.read_text(encoding="utf-8"))}


def test_parses_all_cards():
    assert len(parse(FIXTURE.read_text(encoding="utf-8"))) == 3


def test_modern_and_legacy_platforms():
    by_title = _parsed()
    assert by_title["Forza Horizon Test"].platform == "Xbox"
    assert by_title["Halo Sample"].platform == "Xbox"
    assert by_title["Gears Classic"].platform == "X360"


def test_source_and_id():
    g = _parsed()["Forza Horizon Test"]
    assert g.source == "xbox"
    assert g.external_id == "9NXBOX01"
```

- [ ] **Step 4: Run the parse test**

Run: `python -m pytest tests/test_parse_xbox.py -v`
Expected: PASS once fixture classes and `xbox.py` selectors agree.

- [ ] **Step 5: Live scrape to JSON**

Run (LIVE): `python scrape_libraries.py --vendor xbox`
Expected: `scraped/xbox_<date>.json` written; entries look right.

- [ ] **Step 6: Commit**

```bash
git add scrapers/xbox.py scrape_libraries.py tests/fixtures/xbox_library_sample.html tests/test_parse_xbox.py
git commit -m "feat: Xbox library scraper + parse tests"
```

---

### Task 7: Nintendo scraper

Same shape. Recon confirms the eShop/account purchase-history page and whether 3DS/Wii U history is reachable.

**Files:**
- Create: `scrapers/nintendo.py`
- Modify: `scrape_libraries.py` (register `nintendo`)
- Create: `tests/fixtures/nintendo_library_sample.html`
- Create: `tests/test_parse_nintendo.py`

- [ ] **Step 1: Create the Nintendo scraper stub, then run recon (LIVE)**

Create `scrapers/nintendo.py`:

```python
"""Nintendo library scraper: parse the Nintendo account purchase history into ScrapedGame."""
from __future__ import annotations

from bs4 import BeautifulSoup

from scrapers.base import ScrapedGame

VENDOR_URL = "https://ec.nintendo.com/my/transactions/1"
SOURCE = "nintendo"

PLATFORM_LABELS = {
    "NINTENDOSWITCH": "Switch", "SWITCH": "Switch", "SWITCH2": "Switch",
    "WIIU": "WiiU", "NINTENDO3DS": "3DS", "3DS": "3DS", "NEW3DS": "3DS",
}
DEFAULT_PLATFORM = "Switch"

# --- recon-derived selectors (confirm against .recon/nintendo.html in Step 1) ---
CARD_SELECTOR = "div.transaction"
TITLE_SELECTOR = ".transaction-title"
COVER_SELECTOR = "img"
PLATFORM_SELECTOR = ".transaction-platform"
ID_ATTR = "data-transaction-id"


def _platform_of(card) -> str:
    el = card.select_one(PLATFORM_SELECTOR)
    label = el.get_text(strip=True).upper().replace(" ", "") if el else ""
    return PLATFORM_LABELS.get(label, DEFAULT_PLATFORM)


def parse(html: str) -> list[ScrapedGame]:
    soup = BeautifulSoup(html, "html.parser")
    games = []
    for card in soup.select(CARD_SELECTOR):
        title_el = card.select_one(TITLE_SELECTOR)
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        img = card.select_one(COVER_SELECTOR)
        games.append(ScrapedGame(
            title=title,
            platform=_platform_of(card),
            source=SOURCE,
            external_id=card.get(ID_ATTR),
            cover_url=img.get("src") if img else None,
            source_title=title,
        ))
    return games
```

In `scrape_libraries.py`, finalize the imports and registry:

```python
from scrapers import nintendo, playstation, xbox
```
```python
SCRAPERS = {
    "playstation": playstation,
    "xbox": xbox,
    "nintendo": nintendo,
}
```

Run (LIVE): `python scrape_libraries.py --recon --vendor nintendo`
Expected: `.recon/nintendo.html` written. Inspect it; the purchase/transaction history may paginate — if so, note it (handle pagination in Step 5 by re-running per page or extending `_load_library`). Update selector constants + `PLATFORM_LABELS` to match.

- [ ] **Step 2: Build the synthetic fixture**

Create `tests/fixtures/nintendo_library_sample.html` (a Switch title → `Switch`, a 3DS title → `3DS`):

```html
<div>
  <div class="transaction" data-transaction-id="70010001">
    <span class="transaction-title">Pixel Quest Switch</span>
    <span class="transaction-platform">Nintendo Switch</span>
    <img src="https://example/pixel.png">
  </div>
  <div class="transaction" data-transaction-id="50010002">
    <span class="transaction-title">Retro Adventure 3DS</span>
    <span class="transaction-platform">Nintendo 3DS</span>
    <img src="https://example/retro.png">
  </div>
</div>
```

- [ ] **Step 3: Write the failing parse test**

Create `tests/test_parse_nintendo.py`:

```python
from pathlib import Path

from scrapers.nintendo import parse

FIXTURE = Path(__file__).parent / "fixtures" / "nintendo_library_sample.html"


def _parsed():
    return {g.title: g for g in parse(FIXTURE.read_text(encoding="utf-8"))}


def test_parses_all_cards():
    assert len(parse(FIXTURE.read_text(encoding="utf-8"))) == 2


def test_modern_and_legacy_platforms():
    by_title = _parsed()
    assert by_title["Pixel Quest Switch"].platform == "Switch"
    assert by_title["Retro Adventure 3DS"].platform == "3DS"


def test_source_and_id():
    g = _parsed()["Pixel Quest Switch"]
    assert g.source == "nintendo"
    assert g.external_id == "70010001"
```

- [ ] **Step 4: Run the parse test**

Run: `python -m pytest tests/test_parse_nintendo.py -v`
Expected: PASS once fixture classes and `nintendo.py` selectors agree.

- [ ] **Step 5: Live scrape to JSON**

Run (LIVE): `python scrape_libraries.py --vendor nintendo`
Expected: `scraped/nintendo_<date>.json` written; entries look right.

- [ ] **Step 6: Commit**

```bash
git add scrapers/nintendo.py scrape_libraries.py tests/fixtures/nintendo_library_sample.html tests/test_parse_nintendo.py
git commit -m "feat: Nintendo library scraper + parse tests"
```

---

### Task 8: Full pipeline — import, dry-run review, verify views

**Files:** none created; this is the end-to-end run + manual verification.

- [ ] **Step 1: Confirm the full automated suite is green**

Run: `python -m pytest -v`
Expected: all tests pass (migration, base, importer, three parse suites).

- [ ] **Step 2: Back up the live DB before writing to it**

Run: `python -c "import shutil, datetime; shutil.copy('games.db', 'games.db.bak-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))"`
Expected: a `games.db.bak-*` copy exists (gitignored via `*.db`). Safety net for the first real import.

- [ ] **Step 3: Scrape all three vendors (LIVE)**

Run: `python scrape_libraries.py --vendor all`
Expected: `scraped/playstation_<date>.json`, `scraped/xbox_<date>.json`, `scraped/nintendo_<date>.json`.

- [ ] **Step 4: Dry-run the import and review the diff**

Run: `python import_scraped.py scraped --dry-run`
Expected: a summary of new games, id/title matches, new platform rows (e.g. `('PS3','legacy_console')`), and a **FUZZY — needs your review** list. Read the fuzzy list carefully — these are likely renames of games you already have. Note any that should merge.

- [ ] **Step 5: Real import**

Run: `python import_scraped.py scraped`
Expected: for each fuzzy candidate you'll be prompted `Merge '...' into '...'? [y/N]` — answer per your Step 4 review. Final summary prints what changed; `conn.commit()` persists it.

- [ ] **Step 6: Verify the views in the app (manual)**

Run: `python app.py` then open http://127.0.0.1:5000
Expected:
- **Modern** count grew by the newly-imported PS4/PS5/Switch/Xbox titles.
- **Legacy** is now populated wherever a vendor exposed legacy purchases (PS3/Vita/X360/WiiU/3DS).
- Mode counts and the platform filter show the new platform rows.
- Spot-check 2–3 games you had **renamed**: they were not duplicated (matched by id/title/fuzzy).
- Open a previously-curated game: its status/rating/notes are unchanged.

- [ ] **Step 7: Security check, then commit any remaining tracked changes**

Run: `git status`
Expected: **no** `.recon/`, `scraped/`, `.pw-profile/`, `games.db`, or `games.db.bak-*` staged or untracked-and-about-to-be-added (all gitignored). Only source/test/docs files.

If the working tree is clean (all code already committed in Tasks 1–7), there is nothing to commit here. Otherwise:

```bash
git add -A
git commit -m "chore: finalize library scraping pipeline"
```

---

## Self-Review

**Spec coverage:**
- Three-stage decoupled pipeline (scrape → JSON → import) → Tasks 3–8. ✓
- Playwright persistent context, login-once → `persistent_browser` (Task 3), recon/scrape steps (Tasks 5–7). ✓
- `game_external_ids` table + migration → Task 2. ✓
- Per-console era mapping via `classify_platform`, on-the-fly legacy platform creation → `_apply_or_plan` (Task 4); verified by `test_new_legacy_game_creates_legacy_platform`. ✓
- Four-step match cascade (id → title → fuzzy → new) → `resolve_game` (Task 4); each branch tested. ✓
- Rename-proof identity → `test_external_id_match_survives_rename`. ✓
- Cross-vendor / double-buy unification → `test_cross_vendor_unifies_into_one_game`. ✓
- Curation never overwritten → `test_existing_curation_is_preserved`; new games default `backlog`. ✓
- Idempotency → `test_import_is_idempotent`. ✓
- `--dry-run` writes nothing + previews fuzzy → `test_dry_run_writes_nothing` + Task 8 Step 4. ✓
- `status_hint` captured, not acted on → present on `ScrapedGame`, unused by importer (per spec). ✓
- Security: `.pw-profile/`/`.recon/`/`scraped/` gitignored; only synthetic fixtures committed; pre-commit security check → Task 1 + Task 8 Step 7. ✓
- Best-effort legacy coverage discovered in recon → Tasks 5–7 Step 1 notes. ✓

**Placeholder scan:** The only deferred-to-execution values are the per-vendor CSS selectors + `VENDOR_URL`, which are inherently unknowable until recon sees the live DOM; each vendor task makes capturing and setting them an explicit step, and the synthetic fixtures keep the parse tests concrete. No "TODO"/"implement later" in code.

**Type/name consistency:** `ScrapedGame`, `write_scrape`/`read_scrape`, `persistent_browser`/`autoscroll`, `RECON_DIR`/`SCRAPE_DIR`/`PROFILE_DIR`, `resolve_game`/`MatchResult`/`import_games`/`ImportStats`/`_apply_or_plan`, `match_key`, `FUZZY_MATCH_THRESHOLD`, `migrate_external_ids`, and each vendor's `VENDOR_URL`/`SOURCE`/`parse`/`PLATFORM_LABELS` are used consistently across tasks.
