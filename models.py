import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "games.db"
SERIES_PATTERNS_PATH = Path(__file__).parent / "series_patterns.json"           # per-user (gitignored)
SERIES_PATTERNS_DEFAULT_PATH = Path(__file__).parent / "series_patterns.default.json"  # committed seed
GAME_TRAITS_PATH = Path(__file__).parent / "game_traits.json"                  # per-user (gitignored)
GAME_TRAITS_DEFAULT_PATH = Path(__file__).parent / "game_traits.default.json"  # committed seed
BUNDLE_CATALOG_PATH = Path(__file__).parent / "bundle_catalog.json"            # per-user (gitignored)
BUNDLE_CATALOG_DEFAULT_PATH = Path(__file__).parent / "bundle_catalog.default.json"  # committed seed
SERIES_CATALOG_PATH = Path(__file__).parent / "series_catalog.json"            # per-user (gitignored)
SERIES_CATALOG_DEFAULT_PATH = Path(__file__).parent / "series_catalog.default.json"  # committed seed


def load_series_patterns() -> dict:
    """Load the prefix->series-name table (per-user file, else committed seed)."""
    path = SERIES_PATTERNS_PATH if SERIES_PATTERNS_PATH.exists() else SERIES_PATTERNS_DEFAULT_PATH
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def match_series_prefix(title: str, known_series: dict) -> str | None:
    """Return the series name for the longest matching title prefix, else None.
    Longest-first so a specific prefix beats a shorter one ('Cyberpunk 2077' > 'Cyberpunk')."""
    for prefix, series_name in sorted(known_series.items(), key=lambda kv: -len(kv[0])):
        if title.upper().startswith(prefix.upper()):
            return series_name
    return None


def load_game_traits() -> dict:
    """Load the normalized_title->traits catalog (per-user file, else committed seed)."""
    path = GAME_TRAITS_PATH if GAME_TRAITS_PATH.exists() else GAME_TRAITS_DEFAULT_PATH
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def load_bundle_catalog() -> dict:
    """Load the normalized_title->bundle-entry catalog (per-user file, else committed seed)."""
    path = BUNDLE_CATALOG_PATH if BUNDLE_CATALOG_PATH.exists() else BUNDLE_CATALOG_DEFAULT_PATH
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def load_series_catalog() -> dict:
    """Load the normalized_title->series-entry catalog (per-user file, else committed seed)."""
    path = SERIES_CATALOG_PATH if SERIES_CATALOG_PATH.exists() else SERIES_CATALOG_DEFAULT_PATH
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def add_series_pattern(prefix: str, name: str) -> bool:
    """Add prefix->name to the per-user patterns file (seeding from the default on
    first write). Returns True if added, False if blank or already present."""
    prefix, name = prefix.strip(), name.strip()
    if not prefix or not name:
        return False
    patterns = dict(load_series_patterns())
    if patterns.get(prefix) == name:
        return False
    patterns[prefix] = name
    with open(SERIES_PATTERNS_PATH, "w", encoding="utf-8") as f:
        json.dump(patterns, f, indent=2, ensure_ascii=False, sort_keys=True)
    return True

# Platform era classification (module-level, immutable).
PC_PLATFORMS = frozenset({"PC", "Steam", "GOG", "Epic", "EGS"})
LEGACY_PLATFORMS = frozenset({
    "PS3", "PS2", "PS1", "PSX", "PSV", "Vita", "PSP",
    "X360", "XBOX", "OGXbox",
    "Wii", "WiiU", "GC", "GCN", "N64", "SNES", "NES",
    "3DS", "NDS", "DS", "GBA", "GBC", "GB",
    "Genesis", "Saturn", "Dreamcast",
})

# Legacy consoles seeded as selectable platforms so manually-added physical/retro
# games have something to attach to. (name, short_name) — short_names MUST be in
# LEGACY_PLATFORMS so classify_platform tags them legacy_console. Aliases (PSX, DS,
# GCN, XBOX, PSV) are intentionally omitted in favour of one canonical short_name
# each. Ordered newest→oldest, grouped by brand; the Add Game dropdown preserves it.
LEGACY_PLATFORM_SEED = (
    ("Nintendo 3DS", "3DS"),
    ("Nintendo DS", "NDS"),
    ("Game Boy Advance", "GBA"),
    ("Game Boy Color", "GBC"),
    ("Game Boy", "GB"),
    ("Nintendo Wii U", "WiiU"),
    ("Nintendo Wii", "Wii"),
    ("Nintendo GameCube", "GC"),
    ("Nintendo 64", "N64"),
    ("Super Nintendo", "SNES"),
    ("Nintendo Entertainment System", "NES"),
    ("PlayStation 3", "PS3"),
    ("PlayStation 2", "PS2"),
    ("PlayStation 1", "PS1"),
    ("PlayStation Portable", "PSP"),
    ("PlayStation Vita", "Vita"),
    ("Xbox 360", "X360"),
    ("Xbox (Original)", "OGXbox"),
    ("Sega Genesis", "Genesis"),
    ("Sega Saturn", "Saturn"),
    ("Sega Dreamcast", "Dreamcast"),
)

MODERN_CONSOLE = "modern_console"
LEGACY_CONSOLE = "legacy_console"
PC_CATEGORY = "pc"
MOBILE_CATEGORY = "mobile"
SUBSCRIPTION_CATEGORY = "subscription"

MOBILE_PLATFORM_SEED = (
    ("iOS", "iOS"),
    ("Android", "Android"),
)
SUBSCRIPTION_PLATFORM_SEED = (
    ("Xbox Game Pass", "GamePass"),
    ("PlayStation Plus", "PSPlus"),
    ("Nintendo Switch Online", "NSO"),
    ("EA Play", "EAPlay"),
    ("Ubisoft+", "UbisoftPlus"),
    ("Amazon Luna", "Luna"),
)

# Short_names of the seeded mobile + subscription platforms, so classify_platform
# (and thus the idempotent category re-derive) keeps them out of modern_console.
MOBILE_PLATFORM_SHORTS = frozenset(short for _, short in MOBILE_PLATFORM_SEED)
SUBSCRIPTION_PLATFORM_SHORTS = frozenset(short for _, short in SUBSCRIPTION_PLATFORM_SEED)

# Legacy platforms that DID have a digital storefront (eShop/PSN/XBLA), so their
# games still need a physical/digital qualifier. Pure cartridge/disc legacy do not.
DIGITAL_MARKET_LEGACY_OVERRIDES = frozenset({"3DS", "WiiU", "PS3", "X360", "Vita", "PSP"})


def classify_platform(short_name: str) -> str:
    """Map a platform short_name to an era category."""
    if short_name in PC_PLATFORMS:
        return PC_CATEGORY
    if short_name in MOBILE_PLATFORM_SHORTS:
        return MOBILE_CATEGORY
    if short_name in SUBSCRIPTION_PLATFORM_SHORTS:
        return SUBSCRIPTION_CATEGORY
    if short_name in LEGACY_PLATFORMS:
        return LEGACY_CONSOLE
    return MODERN_CONSOLE


def get_db():
    """Get database connection with row factory."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Initialize database with schema."""
    conn = get_db()
    conn.executescript("""
        -- Games table: core game information
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            normalized_title TEXT NOT NULL,  -- lowercase, no special chars for matching
            cover_url TEXT,
            metacritic_score INTEGER,
            opencritic_score INTEGER,
            igdb_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            psn_addons_synced_at TIMESTAMP,
            UNIQUE(normalized_title)
        );

        -- Platforms table
        CREATE TABLE IF NOT EXISTS platforms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            short_name TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL DEFAULT 'modern_console'
        );

        -- Game-Platform relationship (which platforms you own the game on)
        CREATE TABLE IF NOT EXISTS game_platforms (
            game_id INTEGER NOT NULL,
            platform_id INTEGER NOT NULL,
            owned BOOLEAN DEFAULT 1,
            psprices_id TEXT,  -- reference ID from psprices if available
            PRIMARY KEY (game_id, platform_id),
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE,
            FOREIGN KEY (platform_id) REFERENCES platforms(id) ON DELETE CASCADE
        );

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

        -- Confirmed non-duplicates (dedup: pairs the user marked distinct)
        CREATE TABLE IF NOT EXISTS not_duplicates (
            game_id_lo INTEGER NOT NULL,
            game_id_hi INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (game_id_lo, game_id_hi),
            FOREIGN KEY (game_id_lo) REFERENCES games(id) ON DELETE CASCADE,
            FOREIGN KEY (game_id_hi) REFERENCES games(id) ON DELETE CASCADE
        );

        -- Tags table (genres, themes, custom tags)
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            category TEXT DEFAULT 'custom'  -- 'genre', 'theme', 'custom'
        );

        -- Game-Tag relationship
        CREATE TABLE IF NOT EXISTS game_tags (
            game_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            PRIMARY KEY (game_id, tag_id),
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE,
            FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
        );

        -- Series table for grouping games
        CREATE TABLE IF NOT EXISTS series (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- User ratings and notes
        CREATE TABLE IF NOT EXISTS user_ratings (
            game_id INTEGER PRIMARY KEY,
            status TEXT DEFAULT 'backlog',  -- backlog, playing, completed, dropped, wishlist
            rating INTEGER,  -- 1-4 scale (hate/meh/like/love)
            notes TEXT,
            priority INTEGER DEFAULT 5,  -- 1-10, higher = play sooner
            hours_played REAL DEFAULT 0,
            started_at DATE,
            completed_at DATE,
            sort_order INTEGER,  -- manual sort order for drag-and-drop reordering
            series_id INTEGER,  -- which series this game belongs to
            series_order INTEGER,  -- order within the series
            series_source TEXT,  -- provenance of series_id: auto / catalog / manual
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE,
            FOREIGN KEY (series_id) REFERENCES series(id) ON DELETE SET NULL
        );

        -- Indexes for common queries
        CREATE INDEX IF NOT EXISTS idx_games_normalized_title ON games(normalized_title);
        CREATE INDEX IF NOT EXISTS idx_user_ratings_status ON user_ratings(status);
        CREATE INDEX IF NOT EXISTS idx_user_ratings_priority ON user_ratings(priority DESC);
        CREATE INDEX IF NOT EXISTS idx_user_ratings_sort_order ON user_ratings(sort_order);
        CREATE INDEX IF NOT EXISTS idx_game_tags_game ON game_tags(game_id);
        CREATE INDEX IF NOT EXISTS idx_game_tags_tag ON game_tags(tag_id);
        CREATE INDEX IF NOT EXISTS idx_game_external_ids_game ON game_external_ids(game_id);

        -- DLC / expansions for a game (child rows; checkbox ownership)
        CREATE TABLE IF NOT EXISTS dlc (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id    INTEGER NOT NULL,
            name       TEXT    NOT NULL,
            igdb_id    INTEGER,
            kind       TEXT    DEFAULT 'dlc',
            owned      INTEGER DEFAULT 0,
            source     TEXT    DEFAULT 'igdb',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (game_id, name),
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_dlc_game ON dlc(game_id);
        -- Vendor add-on ids for DLC rows (one DLC may carry ids from several stores)
        CREATE TABLE IF NOT EXISTS dlc_external_ids (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            dlc_id       INTEGER NOT NULL,
            source       TEXT    NOT NULL,
            external_id  TEXT    NOT NULL,
            source_title TEXT,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (source, external_id),
            FOREIGN KEY (dlc_id) REFERENCES dlc(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_dlc_ext_dlc ON dlc_external_ids(dlc_id);

        -- Saved per-game decider conversations (picks-tab chat history)
        CREATE TABLE IF NOT EXISTS decider_chats (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id     INTEGER NOT NULL,
            slot_id     INTEGER,
            slot_label  TEXT,
            messages    TEXT    NOT NULL,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_decider_chats_game ON decider_chats(game_id);
    """)

    # Insert default platforms
    platforms = [
        ("PlayStation", "PS", "modern_console"),
        ("Nintendo Switch", "Switch", "modern_console"),
        ("Xbox", "Xbox", "modern_console"),
        ("PC", "PC", "pc"),
        ("Steam", "Steam", "pc"),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO platforms (name, short_name, category) VALUES (?, ?, ?)",
        platforms
    )

    # Seed legacy consoles so manually-added retro/physical games have a platform.
    migrate_seed_legacy_platforms(conn)

    # Insert some common genre tags
    genres = [
        ("Action", "genre"), ("Adventure", "genre"), ("RPG", "genre"),
        ("JRPG", "genre"), ("Platformer", "genre"), ("Metroidvania", "genre"),
        ("Roguelike", "genre"), ("Puzzle", "genre"), ("Strategy", "genre"),
        ("Simulation", "genre"), ("Horror", "genre"), ("Fighting", "genre"),
        ("Racing", "genre"), ("Sports", "genre"), ("Shooter", "genre"),
        ("Visual Novel", "genre"), ("Indie", "genre"), ("Open World", "theme"),
        ("Story Rich", "theme"), ("Multiplayer", "theme"), ("Co-op", "theme"),
        ("Singleplayer", "theme"), ("Retro", "theme"), ("Pixel Art", "theme"),
        ("VR", "theme"),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO tags (name, category) VALUES (?, ?)",
        genres
    )

    conn.commit()
    conn.close()
    print(f"Database initialized at {DB_PATH}")


def smart_title_case(title):
    """Convert ALL CAPS title to proper title case, preserving stylized names."""
    import re

    # Check if title is mostly ALL CAPS (>70% uppercase letters)
    letters = [c for c in title if c.isalpha()]
    if not letters or sum(1 for c in letters if c.isupper()) / len(letters) < 0.7:
        return title  # Not an ALL CAPS title, preserve original

    # Roman numerals pattern
    roman_pattern = re.compile(r'^[IVXLCDM]+$')
    # Words/acronyms to keep uppercase
    keep_upper = {'II', 'III', 'IV', 'VI', 'VII', 'VIII', 'IX', 'XI', 'XII', 'XIII', 'XIV', 'XV', 'XVI',
                  'HD', 'VR', 'DLC', 'RPG', 'XL', 'DS', '3D', '2D', 'GTA', 'USA', 'UK', 'NYC', 'NT', 'NES', 'SNES',
                  'PS4', 'PS5', 'PS', 'DQ', 'FF', 'NIS', 'UFO', 'GP'}

    def process_part(part):
        """Process a single word part."""
        if part.upper() in keep_upper or roman_pattern.match(part.upper()):
            return part.upper()
        else:
            return part.capitalize()

    words = title.split()
    result = []

    for word in words:
        # Handle hyphenated words (e.g., "XIII-2")
        if '-' in word:
            parts = word.split('-')
            processed_parts = [process_part(p) for p in parts]
            result.append('-'.join(processed_parts))
        # Handle words with slashes (e.g., "X/X-2")
        elif '/' in word:
            parts = word.split('/')
            processed_parts = [process_part(p) for p in parts]
            result.append('/'.join(processed_parts))
        else:
            result.append(process_part(word))

    return ' '.join(result)


# Leading region/language parentheticals that are not part of a game's name,
# e.g. "(English) Pokémon FireRed Version". Extensible; matched case-insensitively
# as a parenthetical at the very start. (Trailing platform parens are handled
# separately by the regex inside clean_title.)
LEADING_TAGS = frozenset({
    "english", "japanese", "french", "german", "spanish", "italian",
    "usa", "us", "na", "europe", "eu", "pal", "ntsc",
    "japan", "jp", "world", "asia", "korea", "china",
})

# Platform-edition suffixes that are not part of a game's name, e.g.
# "Fantasy Life i - Nintendo Switch 2 Edition". Extensible; stripped from the end
# along with any joining " - ", ": ", or space. Longer suffixes are tried first so
# the more specific one wins.
KNOWN_EDITION_SUFFIXES = (
    "Nintendo Switch 2 Edition",
    "Nintendo Switch Edition",
)

# Seed slots: the owner's four context slots. Inserted only when the slots table
# is empty (seed-once); fully user-editable afterward. platforms are platform
# short_names (see the platforms table). See the gamer-persona memory.
SEED_SLOTS = (
    {"label": "Switch · Quick",      "sort_order": 0, "platforms": ["Switch"],
     "max_session_minutes": 60, "min_session_minutes": None, "streamable_only": 0,
     "prioritize_started": 1,
     "context_notes": "Couch, short sitting, kids in bed. Clean stopping points."},
    {"label": "Switch · Long",       "sort_order": 1, "platforms": ["Switch"],
     "max_session_minutes": None, "min_session_minutes": 60, "streamable_only": 0,
     "prioritize_started": 1,
     "context_notes": "Couch, longer Switch session."},
    {"label": "Garage · Console",    "sort_order": 2, "platforms": ["PS", "Xbox"],
     "max_session_minutes": None, "min_session_minutes": None, "streamable_only": 0,
     "prioritize_started": 1,
     "context_notes": "Needs the real garage setup; reflex/low-latency; worth the trip."},
    {"label": "Long · Stream-safe",  "sort_order": 3, "platforms": ["PS", "Xbox"],
     "max_session_minutes": None, "min_session_minutes": 60, "streamable_only": 1,
     "prioritize_started": 1,
     "context_notes": "Turn-based / lag-tolerant. Garage or Shield-streamed to the couch."},
)


def seed_default_slots(conn: sqlite3.Connection) -> None:
    """Insert the seed slots only if the slots table is empty. Idempotent; never
    clobbers user-defined slots."""
    existing = conn.execute("SELECT COUNT(*) FROM slots").fetchone()[0]
    if existing:
        return
    for s in SEED_SLOTS:
        conn.execute(
            "INSERT INTO slots (label, sort_order, platforms, max_session_minutes, "
            "min_session_minutes, streamable_only, prioritize_started, context_notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (s["label"], s["sort_order"], json.dumps(s["platforms"]),
             s["max_session_minutes"], s["min_session_minutes"],
             s["streamable_only"], s["prioritize_started"], s["context_notes"]))
    conn.commit()


def strip_leading_tag(title):
    """Strip a leading region/language parenthetical like '(English) '."""
    import re
    m = re.match(r"\s*\(([^)]*)\)\s*", title)
    if m and m.group(1).strip().lower() in LEADING_TAGS:
        return title[m.end():]
    return title


def strip_edition_suffix(title):
    """Strip a known platform-edition suffix (and its joining separator)."""
    import re
    for suffix in sorted(KNOWN_EDITION_SUFFIXES, key=len, reverse=True):
        pattern = r"\s*[-–:]?\s*" + re.escape(suffix) + r"\s*$"
        stripped = re.sub(pattern, "", title, flags=re.IGNORECASE)
        if stripped != title:
            return stripped.rstrip()
    return title


def clean_title(title):
    """Clean a display title: strip vendor junk that is not part of the name.

    Removes trademark symbols, a leading region/language tag, stray straight
    double quotes, a trailing platform parenthetical, and known platform-edition
    suffixes; ALL-CAPS titles are smart-title-cased. Does NOT guess the casing of
    normal-case titles — authoritative casing comes from the IGDB canonical pass.
    """
    import re
    # Remove trademark symbols
    title = title.replace('™', '').replace('®', '').replace('©', '')
    # Strip a leading region/language tag, e.g. "(English) Pokémon FireRed"
    title = strip_leading_tag(title)
    # Strip stray straight double quotes, e.g. '"Edna & Harvey" Bundle'
    title = title.replace('"', '')
    # Remove platform indicators like (PS4), (PS5), (Xbox One), etc. at the end
    title = re.sub(r'\s*\((PS[45]?|Xbox[^)]*|Switch|PC|Nintendo[^)]*)\)\s*$', '', title, flags=re.IGNORECASE)
    # Strip known platform-edition suffixes like "Nintendo Switch 2 Edition"
    title = strip_edition_suffix(title)
    # Clean up any double spaces and strip
    title = re.sub(r'\s+', ' ', title).strip()
    # Fix ALL CAPS titles (smart_title_case checks internally if title is mostly caps)
    title = smart_title_case(title)
    return title


def reclean_display_titles(conn, dry_run=False):
    """Recompute every game's display title with the current clean_title rules.

    Display-only: updates games.title but NEVER normalized_title. Recomputing the
    match key (and merging the duplicates it surfaces) is the dedup workstream;
    leaving normalized_title alone here means an improved clean_title can never
    trip UNIQUE(normalized_title) and crash on startup.

    Idempotent (clean_title is a fixed point) and --dry-run-able. Does not commit;
    the caller owns the transaction. Returns the list of changed rows as
    ``{"id", "original", "cleaned"}`` dicts.
    """
    changes = []
    for row in conn.execute("SELECT id, title FROM games").fetchall():
        original = row["title"]
        cleaned = clean_title(original)
        if cleaned != original:
            changes.append({"id": row["id"], "original": original, "cleaned": cleaned})
            if not dry_run:
                conn.execute(
                    "UPDATE games SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (cleaned, row["id"]),
                )
    return changes


def migrate_platform_category(conn):
    """Add platforms.category if missing and (re)backfill from short_name.

    Idempotent: safe to run on every startup. Backfill is deterministic
    (derived purely from short_name), so re-running never loses data.
    """
    cols = [c[1] for c in conn.execute("PRAGMA table_info(platforms)").fetchall()]
    if "category" not in cols:
        conn.execute(
            "ALTER TABLE platforms ADD COLUMN category TEXT NOT NULL "
            "DEFAULT 'modern_console'"
        )
    for row in conn.execute("SELECT id, short_name FROM platforms").fetchall():
        conn.execute(
            "UPDATE platforms SET category = ? WHERE id = ?",
            (classify_platform(row[1]), row[0]),
        )
    conn.commit()


def migrate_platform_digital_market(conn: sqlite3.Connection) -> None:
    """Add platforms.has_digital_market and (re)seed it. Idempotent.

    Default by category: modern_console / pc / mobile / subscription have a digital
    market (1); legacy_console does not (0), except the eShop/PSN-era overrides."""
    cols = [c[1] for c in conn.execute("PRAGMA table_info(platforms)").fetchall()]
    if "has_digital_market" not in cols:
        conn.execute(
            "ALTER TABLE platforms ADD COLUMN has_digital_market INTEGER NOT NULL DEFAULT 0"
        )
    for row in conn.execute("SELECT id, short_name, category FROM platforms").fetchall():
        has_market = (
            row[2] in ("modern_console", "pc", "mobile", "subscription")
            or row[1] in DIGITAL_MARKET_LEGACY_OVERRIDES
        )
        conn.execute(
            "UPDATE platforms SET has_digital_market = ? WHERE id = ?",
            (1 if has_market else 0, row[0]),
        )
    conn.commit()


def migrate_seed_legacy_platforms(conn):
    """Seed the known legacy consoles as selectable platforms. Idempotent.

    INSERT OR IGNORE keys on the unique short_name, so existing rows (incl. ones
    a user renamed) are never clobbered and re-running is a no-op. Category is
    derived from classify_platform, so it can't drift from LEGACY_PLATFORMS.
    """
    conn.executemany(
        "INSERT OR IGNORE INTO platforms (name, short_name, category) VALUES (?, ?, ?)",
        [(name, short, classify_platform(short)) for name, short in LEGACY_PLATFORM_SEED],
    )
    conn.commit()


def migrate_seed_extra_platforms(conn: sqlite3.Connection) -> None:
    """Seed the mobile + subscription platform categories (stub for later catalogs).
    Idempotent: INSERT OR IGNORE keys on the unique short_name."""
    conn.executemany(
        "INSERT OR IGNORE INTO platforms (name, short_name, category) VALUES (?, ?, ?)",
        [(name, short, MOBILE_CATEGORY) for name, short in MOBILE_PLATFORM_SEED]
        + [(name, short, SUBSCRIPTION_CATEGORY) for name, short in SUBSCRIPTION_PLATFORM_SEED],
    )
    conn.commit()


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


def migrate_not_duplicates(conn):
    """Create the not_duplicates table if missing. Idempotent.

    Records pairs the user confirmed are NOT the same game (stored ordered,
    lo < hi) so the dedup tool never re-asks. Cascade-cleaned when either game
    is deleted (e.g. by a merge).
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS not_duplicates (
            game_id_lo INTEGER NOT NULL,
            game_id_hi INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (game_id_lo, game_id_hi),
            FOREIGN KEY (game_id_lo) REFERENCES games(id) ON DELETE CASCADE,
            FOREIGN KEY (game_id_hi) REFERENCES games(id) ON DELETE CASCADE
        );
    """)
    conn.commit()


def migrate_dlc(conn: sqlite3.Connection) -> None:
    """Add the dlc child table and games.igdb_id column if missing (idempotent)."""
    cols = [c[1] for c in conn.execute("PRAGMA table_info(games)")]
    if "igdb_id" not in cols:
        conn.execute("ALTER TABLE games ADD COLUMN igdb_id INTEGER")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dlc (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id    INTEGER NOT NULL,
            name       TEXT    NOT NULL,
            igdb_id    INTEGER,
            kind       TEXT    DEFAULT 'dlc',
            owned      INTEGER DEFAULT 0,
            source     TEXT    DEFAULT 'igdb',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (game_id, name),
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dlc_game ON dlc(game_id)")
    conn.commit()


def migrate_dlc_external_ids(conn: sqlite3.Connection) -> None:
    """Create the dlc_external_ids table if missing. Idempotent.

    One DLC carries many rows here (one per store); identity is
    (source, external_id), so a re-scrape matches an owned add-on by its stable
    vendor id and the per-game deep-fetch (later SPs) can reconcile owned rows to
    catalogue rows by id.
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS dlc_external_ids (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            dlc_id       INTEGER NOT NULL,
            source       TEXT    NOT NULL,
            external_id  TEXT    NOT NULL,
            source_title TEXT,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (source, external_id),
            FOREIGN KEY (dlc_id) REFERENCES dlc(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_dlc_ext_dlc ON dlc_external_ids(dlc_id);
    """)
    conn.commit()


def migrate_dlc_review_queue(conn: sqlite3.Connection) -> None:
    """Create the dlc_review_queue table + indexes if missing. Idempotent.

    Persists OwnershipReport.review items across scrapes so the resolution modal
    can resolve them at any time. UPSERT key is (source, external_id) via the
    partial unique index (null source/ext rows are allowed for legacy paths).
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dlc_review_queue (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            addon_title   TEXT    NOT NULL,
            source        TEXT,
            external_id   TEXT,
            source_title  TEXT,
            reason        TEXT    NOT NULL,
            game_id       INTEGER,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at   TIMESTAMP,
            dismissed_at  TIMESTAMP,
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE SET NULL
        )
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_dlc_review_vendor_id
            ON dlc_review_queue(source, external_id)
            WHERE source IS NOT NULL AND external_id IS NOT NULL
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_dlc_review_open
            ON dlc_review_queue(resolved_at, dismissed_at)
    """)
    conn.commit()


def migrate_slots(conn: sqlite3.Connection) -> None:
    """Create the slots table if missing. Idempotent.

    A slot is a user-defined play context (label + constraints). It always holds
    at most one current game + a plaintext goal. Constraints (platforms, session
    window, streamable_only, prioritize_started) drive deterministic eligibility
    now and the SP2 chat prompt later. current_game_id is SET NULL on game delete
    so a deleted game never orphans a slot.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS slots (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            label                TEXT    NOT NULL,
            sort_order           INTEGER NOT NULL DEFAULT 0,
            platforms            TEXT,          -- JSON array of platform short_names
            max_session_minutes  INTEGER,       -- upper bound on a comfortable sitting
            min_session_minutes  INTEGER,       -- lower bound
            streamable_only      INTEGER NOT NULL DEFAULT 0,
            prioritize_started   INTEGER NOT NULL DEFAULT 1,
            completionist        INTEGER NOT NULL DEFAULT 0,
            context_notes        TEXT,          -- owner's own words; feeds SP2 prompt
            current_game_id      INTEGER,
            goal                 TEXT,
            FOREIGN KEY (current_game_id) REFERENCES games(id) ON DELETE SET NULL
        )
    """)
    cols = [c[1] for c in conn.execute("PRAGMA table_info(slots)").fetchall()]
    if "streamable_only" not in cols:
        conn.execute("ALTER TABLE slots ADD COLUMN streamable_only INTEGER NOT NULL DEFAULT 0")
    cols = [c[1] for c in conn.execute("PRAGMA table_info(slots)").fetchall()]
    if "prioritize_started" not in cols:
        conn.execute("ALTER TABLE slots ADD COLUMN prioritize_started INTEGER NOT NULL DEFAULT 1")
    cols = [c[1] for c in conn.execute("PRAGMA table_info(slots)").fetchall()]
    if "focus_series_id" not in cols:
        conn.execute(
            "ALTER TABLE slots ADD COLUMN focus_series_id INTEGER "
            "REFERENCES series(id) ON DELETE SET NULL")
    cols = [c[1] for c in conn.execute("PRAGMA table_info(slots)").fetchall()]
    if "completionist" not in cols:
        conn.execute("ALTER TABLE slots ADD COLUMN completionist INTEGER NOT NULL DEFAULT 0")
    conn.commit()


def migrate_slot_history(conn: sqlite3.Connection) -> None:
    """Create the slot_history table if missing. Idempotent.

    One row per game that has passed through a slot — the "what did I just finish"
    + momentum + genre-fatigue memory. outcome is one of beat/completed/dropped/shelved.
    No FK constraints on slot_id/game_id: history must survive slot or game deletion.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS slot_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            slot_id    INTEGER NOT NULL,
            game_id    INTEGER NOT NULL,
            goal       TEXT,
            pinned_at  TIMESTAMP,
            removed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            outcome    TEXT    NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_slot_history_removed ON slot_history(removed_at)")
    conn.commit()


def migrate_slot_dismissals(conn: sqlite3.Connection) -> None:
    """Create the slot_dismissals table if missing. Idempotent.

    A dismissed suggestion (slot_id, game_id) is hidden from that slot's candidate
    list until the slot's current game is replaced (the engine clears the slot's
    rows then). Cascades away if the slot or game is deleted.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS slot_dismissals (
            slot_id    INTEGER NOT NULL,
            game_id    INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (slot_id, game_id),
            FOREIGN KEY (slot_id) REFERENCES slots(id) ON DELETE CASCADE,
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
        )
    """)
    conn.commit()


def migrate_game_signals(conn: sqlite3.Connection) -> None:
    """Add HowLongToBeat + override signal columns to games. Idempotent.

    Session-tolerance and the default latency tolerance are NOT stored — they are
    derived at scoring time (slot_signals.py) so retuning the lookup tables re-scores
    everything without a migration. Only the raw HLTB durations and the manual
    overrides live here.
    """
    cols = [c[1] for c in conn.execute("PRAGMA table_info(games)").fetchall()]
    additions = [
        ("hltb_id", "TEXT"),
        ("hltb_main_minutes", "INTEGER"),
        ("hltb_main_extra_minutes", "INTEGER"),
        ("hltb_completionist_minutes", "INTEGER"),
        ("time_to_beat_override_minutes", "INTEGER"),
        ("input_lag_override", "INTEGER"),
    ]
    for name, decl in additions:
        if name not in cols:
            conn.execute(f"ALTER TABLE games ADD COLUMN {name} {decl}")
    conn.commit()


def migrate_game_traits(conn: sqlite3.Connection) -> None:
    """Add the session-tolerance + series-role columns to games. Idempotent.

    Each carries a `*_source` of catalog/ai/manual (manual LOCKS the row against
    catalog re-sync and AI). session_length (short/long) is written by the trait
    catalog (apply_traits_catalog); series_role (mainline/spinoff) is written by the
    SERIES catalog (apply_series_catalog) — this migration only adds the columns. Null
    is always a safe, neutral value.
    """
    cols = [c[1] for c in conn.execute("PRAGMA table_info(games)").fetchall()]
    additions = [
        ("session_length", "TEXT"),
        ("session_length_source", "TEXT"),
        ("series_role", "TEXT"),
        ("series_role_source", "TEXT"),
    ]
    for name, decl in additions:
        if name not in cols:
            conn.execute(f"ALTER TABLE games ADD COLUMN {name} {decl}")
    conn.commit()


def migrate_collection_name(conn: sqlite3.Connection) -> None:
    """Add games.collection_name (the launcher compilation a broken-out game belongs
    to). Idempotent. Non-null drives the tile 'collection' badge + the detail-view
    'Part of <name>' launch cue; null is a normal standalone game.
    """
    cols = [c[1] for c in conn.execute("PRAGMA table_info(games)").fetchall()]
    if "collection_name" not in cols:
        conn.execute("ALTER TABLE games ADD COLUMN collection_name TEXT")
    conn.commit()


def migrate_series_source(conn: sqlite3.Connection) -> None:
    """Add user_ratings.series_source (auto/catalog/manual) so the series catalog can
    override prefix-auto assignments while never clobbering a manual one. Idempotent.
    Null is treated as unset (overwritable). See backfill_series_source for existing rows.
    """
    cols = [c[1] for c in conn.execute("PRAGMA table_info(user_ratings)").fetchall()]
    if "series_source" not in cols:
        conn.execute("ALTER TABLE user_ratings ADD COLUMN series_source TEXT")
    conn.commit()


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


def migrate_igdb_review_reason(conn: sqlite3.Connection) -> None:
    """Add games.igdb_review_reason (TEXT, nullable). Idempotent. Holds a short
    human reason the audit flagged a game (e.g. 'bundle', 'mobile->console'),
    surfaced in the Needs-review UI. Cleared whenever needs_igdb_review is cleared."""
    cols = [c[1] for c in conn.execute("PRAGMA table_info(games)").fetchall()]
    if "igdb_review_reason" not in cols:
        conn.execute("ALTER TABLE games ADD COLUMN igdb_review_reason TEXT")
    conn.commit()


def migrate_psn_addons_synced_at(conn: sqlite3.Connection) -> None:
    """Add games.psn_addons_synced_at (TIMESTAMP, nullable). Idempotent. Tracks
    when PSN add-on ownership was last synced for a game; NULL means never synced,
    allowing the add-on pass to backfill the whole library then run incrementally."""
    cols = [c[1] for c in conn.execute("PRAGMA table_info(games)").fetchall()]
    if "psn_addons_synced_at" not in cols:
        conn.execute("ALTER TABLE games ADD COLUMN psn_addons_synced_at TIMESTAMP")
    conn.commit()


def migrate_barcode_registry(conn: sqlite3.Connection) -> None:
    """Permanent UPC -> game registry for mobile barcode scanning. Every confirmed
    scan writes a row, so repeat scans are instant, free, and human-accurate.

    Renamed from barcode_cache: if the old table exists and the new one does not,
    rename it in place (preserving all rows); otherwise create barcode_registry.
    Idempotent."""
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "barcode_cache" in tables and "barcode_registry" not in tables:
        conn.execute("ALTER TABLE barcode_cache RENAME TO barcode_registry")
        conn.commit()
        return
    conn.execute("""
        CREATE TABLE IF NOT EXISTS barcode_registry (
            upc TEXT PRIMARY KEY,
            igdb_id INTEGER,
            title TEXT,
            platform TEXT,
            game_id INTEGER REFERENCES games(id) ON DELETE SET NULL,
            confirmed_at TEXT
        )
    """)
    conn.commit()


def migrate_barcode_registry_cover(conn: sqlite3.Connection) -> None:
    """Add barcode_registry.cover_url so a re-scan (cache hit) can show the same
    cover art the first scan did. Idempotent."""
    cols = [c[1] for c in conn.execute("PRAGMA table_info(barcode_registry)").fetchall()]
    if "cover_url" not in cols:
        conn.execute("ALTER TABLE barcode_registry ADD COLUMN cover_url TEXT")
    conn.commit()


TRAIT_FIELDS = ("session_length",)


def apply_traits_catalog(conn: sqlite3.Connection, game_id: int | None = None) -> None:
    """Set catalog trait values on games not already manually locked. Idempotent.

    Resolution: a `manual` source LOCKS the row (skipped here). Otherwise the catalog
    value (keyed by normalized_title) is written with source='catalog', overwriting a
    prior catalog/ai/null value. A missing catalog or absent entry is a safe no-op.
    game_id=None processes every game (startup); a specific id processes one (on add).
    """
    catalog = load_game_traits()
    if not catalog:
        return
    sql = "SELECT id, normalized_title, session_length_source FROM games"
    params: tuple = ()
    if game_id is not None:
        sql += " WHERE id = ?"
        params = (game_id,)
    for row in conn.execute(sql, params).fetchall():
        entry = catalog.get(row["normalized_title"])
        if not entry:
            continue
        for trait in TRAIT_FIELDS:
            if row[f"{trait}_source"] == "manual":
                continue  # locked
            value = entry.get(trait)
            if value is None:
                continue
            conn.execute(
                f"UPDATE games SET {trait} = ?, {trait}_source = 'catalog' WHERE id = ?",
                (value, row["id"]))
    conn.commit()


def migrate_decider_chats(conn: sqlite3.Connection) -> None:
    """Saved per-game decider conversations (picks-tab chat history)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS decider_chats (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id     INTEGER NOT NULL,
            slot_id     INTEGER,
            slot_label  TEXT,
            messages    TEXT    NOT NULL,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_decider_chats_game ON decider_chats(game_id);
    """)
    conn.commit()


def migrate_game_platform_format(conn: sqlite3.Connection) -> None:
    """Add game_platforms.format ('physical'|'digital') and backfill from the per-game
    physical flag (a 'Physical' tag), defaulting to digital. Only fills NULLs, so
    re-running never overrides a value set later per platform. Idempotent."""
    cols = [c[1] for c in conn.execute("PRAGMA table_info(game_platforms)").fetchall()]
    if "format" not in cols:
        conn.execute("ALTER TABLE game_platforms ADD COLUMN format TEXT")
    conn.execute("""
        UPDATE game_platforms SET format =
            CASE WHEN EXISTS (
                SELECT 1 FROM game_tags gt JOIN tags t ON t.id = gt.tag_id
                WHERE gt.game_id = game_platforms.game_id AND t.name = 'Physical'
            ) THEN 'physical' ELSE 'digital' END
        WHERE format IS NULL
    """)
    conn.commit()


def migrate_collections(conn: sqlite3.Connection) -> None:
    """IGDB-canonical collections layer (Stage 1, additive alongside series).

    collections: one row per IGDB collection (PK = the IGDB collection id).
    game_collections: m2m — a game appears in EVERY collection IGDB lists it
    under (FF7 Remake -> Final Fantasy + Compilation of FF7 + FF7 Remake).
    games.original_release_ts: the earliest known release (own date,
    version_parent, or parent_game — remasters sort at the original), stored
    so collection views sort chronologically without live IGDB calls.
    Idempotent."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS collections (
            id         INTEGER PRIMARY KEY,   -- IGDB collection id
            name       TEXT NOT NULL,
            slug       TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS game_collections (
            game_id       INTEGER NOT NULL,
            collection_id INTEGER NOT NULL,
            PRIMARY KEY (game_id, collection_id),
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE,
            FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_game_collections_collection
            ON game_collections(collection_id)
    """)
    cols = [c[1] for c in conn.execute("PRAGMA table_info(games)").fetchall()]
    if "original_release_ts" not in cols:
        conn.execute("ALTER TABLE games ADD COLUMN original_release_ts INTEGER")
    conn.commit()


def migrate_schema_flags(conn: sqlite3.Connection) -> None:
    """Create the schema_flags marker table (records one-time reconciles that must
    not re-run on every startup). Idempotent."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_flags (
            name       TEXT PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


TAGGED_PHYSICAL_FLAG = "tagged_games_to_physical"


def migrate_tagged_games_to_physical(conn: sqlite3.Connection) -> None:
    """One-time conservative reconcile of the legacy 'Physical' tag into the
    per-platform format source of truth. For every game carrying the 'Physical'
    tag, set format='physical' on each owned platform whose current format is
    'digital' or NULL. Never downgrades 'both' or an existing 'physical', and
    never touches untagged games.

    Gated by a schema_flags marker: without it this re-ran on every startup and
    silently reverted deliberate physical->digital edits on games still carrying
    the retired legacy tag.

    Must run AFTER migrate_game_platform_format (which creates the column)."""
    migrate_schema_flags(conn)
    if conn.execute("SELECT 1 FROM schema_flags WHERE name = ?",
                    (TAGGED_PHYSICAL_FLAG,)).fetchone():
        return
    conn.execute("""
        UPDATE game_platforms SET format = 'physical'
        WHERE (format IS NULL OR format = 'digital')
          AND EXISTS (
              SELECT 1 FROM game_tags gt JOIN tags t ON t.id = gt.tag_id
              WHERE gt.game_id = game_platforms.game_id AND t.name = 'Physical'
          )
    """)
    conn.execute("INSERT OR IGNORE INTO schema_flags (name) VALUES (?)",
                 (TAGGED_PHYSICAL_FLAG,))
    conn.commit()


def migrate_upc_review(conn: sqlite3.Connection) -> None:
    """Create the upc_review table if missing. Idempotent.

    Doubles as the enrichment review queue (status='pending') and the
    dedup/attempt ledger ('no_match' attempted, 'dismissed' rejected). Confirmed
    links live in barcode_registry, not here.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS upc_review (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id       INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
            platform      TEXT    NOT NULL,
            upc           TEXT,
            product_title TEXT,
            cover_url     TEXT,
            status        TEXT NOT NULL CHECK(status IN ('pending', 'no_match', 'dismissed')),
            reason        TEXT,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_upc_review_game_platform
            ON upc_review(game_id, platform)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_upc_review_pending
            ON upc_review(status)
    """)
    conn.commit()


def migrate_upc_enrichment_state(conn: sqlite3.Connection) -> None:
    """Create the single-row upc_enrichment_state table if missing. Idempotent.

    Holds the daily-drip bookkeeping: last UTC date a batch ran + calls used that
    day (shared per-IP UPCitemdb quota). One row, id=1, seeded on create.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS upc_enrichment_state (
            id             INTEGER PRIMARY KEY CHECK(id = 1),
            last_run_date  TEXT,
            last_run_count INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute(
        "INSERT OR IGNORE INTO upc_enrichment_state (id, last_run_date, last_run_count) "
        "VALUES (1, NULL, 0)")
    conn.commit()


def migrate_slot_schedule_window(conn: sqlite3.Connection) -> None:
    """Create the per-slot schedule-window table if missing. Idempotent.

    Each row is one day/time window for a slot; a slot may have 0..N windows.
    Zero windows means the slot is 'anytime' (always active). Cascades away with
    its slot so a deleted slot never orphans windows.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS slot_schedule_window (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            slot_id   INTEGER NOT NULL,
            days      INTEGER NOT NULL,   -- 7-bit mask, bit 0 = Monday .. bit 6 = Sunday
            start_min INTEGER NOT NULL,   -- minutes since local midnight, 0..1439
            end_min   INTEGER NOT NULL,   -- minutes since local midnight, 0..1439
            FOREIGN KEY (slot_id) REFERENCES slots(id) ON DELETE CASCADE
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_schedule_window_slot "
        "ON slot_schedule_window(slot_id)")
    conn.commit()


def migrate_user_profile(conn: sqlite3.Connection) -> None:
    """Create + seed the single-row user_profile table if missing. Idempotent.

    Holds the owner's daily rhythm (work hours, bedtime, meal windows). Used only
    to pre-fill suggested window times in the web editor; it does not affect
    matching.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_profile (
            id             INTEGER PRIMARY KEY CHECK(id = 1),
            work_start_min INTEGER,
            work_end_min   INTEGER,
            bed_time_min   INTEGER,
            meal_windows   TEXT        -- JSON list of {start_min, end_min}, optional
        )
    """)
    conn.execute(
        "INSERT OR IGNORE INTO user_profile "
        "(id, work_start_min, work_end_min, bed_time_min, meal_windows) "
        "VALUES (1, NULL, NULL, NULL, NULL)")
    conn.commit()


def migrate_db():
    """Run database migrations for schema updates."""
    conn = get_db()

    # Check if sort_order column exists in user_ratings
    columns = conn.execute("PRAGMA table_info(user_ratings)").fetchall()
    column_names = [col[1] for col in columns]

    if 'sort_order' not in column_names:
        conn.execute("ALTER TABLE user_ratings ADD COLUMN sort_order INTEGER")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_ratings_sort_order ON user_ratings(sort_order)")
        conn.commit()
        print("Added sort_order column to user_ratings")

    # Create series table if not exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS series (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    # Add series columns to user_ratings if not exist
    columns = conn.execute("PRAGMA table_info(user_ratings)").fetchall()
    column_names = [col[1] for col in columns]

    if 'series_id' not in column_names:
        conn.execute("ALTER TABLE user_ratings ADD COLUMN series_id INTEGER REFERENCES series(id) ON DELETE SET NULL")
        conn.execute("ALTER TABLE user_ratings ADD COLUMN series_order INTEGER")
        conn.commit()
        print("Added series columns to user_ratings")

    # Every series operation filters on series_id; index it.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_ratings_series_id "
                 "ON user_ratings(series_id)")
    conn.commit()

    # Add/backfill platform era category
    migrate_platform_category(conn)

    # Seed legacy consoles as selectable platforms
    migrate_seed_legacy_platforms(conn)

    # Seed mobile + subscription platform stubs
    migrate_seed_extra_platforms(conn)

    # Add/backfill has_digital_market flag (must run after all platform seeding)
    migrate_platform_digital_market(conn)

    # Add the external-ids identity table
    migrate_external_ids(conn)

    # Add the not-duplicates table (dedup workstream)
    migrate_not_duplicates(conn)

    # Add the dlc_review_queue table (DLC resolution modal)
    migrate_dlc_review_queue(conn)

    # Add the dlc table + games.igdb_id (DLC tracking)
    migrate_dlc(conn)

    # Add the dlc_external_ids table (vendor add-on ids; DLC source-of-truth rework)
    migrate_dlc_external_ids(conn)

    # Add the Slate tables (picks-tab revamp foundation)
    migrate_slots(conn)
    migrate_slot_history(conn)
    migrate_slot_dismissals(conn)
    migrate_game_signals(conn)
    migrate_game_traits(conn)
    migrate_collection_name(conn)
    migrate_collections(conn)
    migrate_series_source(conn)
    migrate_igdb_review(conn)
    migrate_igdb_review_reason(conn)
    migrate_psn_addons_synced_at(conn)
    migrate_barcode_registry(conn)
    migrate_barcode_registry_cover(conn)
    migrate_game_platform_format(conn)
    migrate_tagged_games_to_physical(conn)
    migrate_decider_chats(conn)
    migrate_upc_review(conn)
    migrate_upc_enrichment_state(conn)
    migrate_slot_schedule_window(conn)
    migrate_user_profile(conn)
    backfill_series_source(conn)
    apply_traits_catalog(conn)
    apply_series_catalog(conn)
    seed_default_slots(conn)

    # Re-clean display titles with the current rules (remove (PS4), trademark
    # symbols, leading region tags, edition suffixes, etc.). Display-only:
    # normalized_title is intentionally left untouched — recomputing the match key
    # and merging the duplicates it surfaces is the dedup workstream.
    changes = reclean_display_titles(conn)
    if changes:
        conn.commit()
        print(f"Cleaned up {len(changes)} game titles")

    conn.close()


def normalize_title(title):
    """Normalize title for matching across sources."""
    import re
    # Remove platform indicators like (PS4), (PS5), etc.
    title = re.sub(r'\s*\([^)]*\)\s*$', '', title)
    # Remove trademark symbols
    title = title.replace('™', '').replace('®', '').replace('©', '')
    # Lowercase and strip
    title = title.lower().strip()
    # Remove special characters but keep spaces
    title = re.sub(r'[^\w\s]', '', title)
    # Collapse multiple spaces
    title = re.sub(r'\s+', ' ', title)
    return title


def auto_populate_series():
    """Automatically create series based on common game title prefixes."""
    conn = get_db()

    known_series = load_series_patterns()

    # Get all games
    games = conn.execute("SELECT id, title FROM games ORDER BY title").fetchall()

    # Track which series we've created and which games belong to them
    series_games = {}  # series_name -> [(game_id, title, sort_key)]

    for game in games:
        game_id = game['id']
        title = game['title']

        # Check against known series patterns, longest prefix first so a specific
        # prefix wins over a shorter one regardless of file order
        # ("Cyberpunk 2077" must beat "Cyberpunk").
        matched_series = match_series_prefix(title, known_series)

        if matched_series:
            if matched_series not in series_games:
                series_games[matched_series] = []

            # Create a sort key - try to extract numbers for ordering
            # This helps sort "Final Fantasy VII" before "Final Fantasy X"
            sort_key = title.lower()
            series_games[matched_series].append((game_id, title, sort_key))

    # Create series and assign games
    created_count = 0
    assigned_count = 0

    for series_name, games_list in series_games.items():
        if len(games_list) < 2:
            continue  # Skip series with only 1 game

        # Check if series already exists
        existing = conn.execute("SELECT id FROM series WHERE name = ?", (series_name,)).fetchone()
        if existing:
            series_id = existing['id']
        else:
            conn.execute("INSERT INTO series (name) VALUES (?)", (series_name,))
            series_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            created_count += 1

        # Sort games and assign to series
        games_list.sort(key=lambda x: x[2])  # Sort by sort_key

        for order, (game_id, title, _) in enumerate(games_list):
            # Check if already assigned
            existing_assignment = conn.execute(
                "SELECT series_id FROM user_ratings WHERE game_id = ?",
                (game_id,)
            ).fetchone()

            if existing_assignment and existing_assignment['series_id']:
                continue  # Already assigned to a series

            conn.execute("""
                INSERT INTO user_ratings (game_id, series_id, series_order, series_source)
                VALUES (?, ?, ?, 'auto')
                ON CONFLICT(game_id) DO UPDATE SET
                    series_id = excluded.series_id,
                    series_order = excluded.series_order,
                    series_source = excluded.series_source,
                    updated_at = CURRENT_TIMESTAMP
            """, (game_id, series_id, order))
            assigned_count += 1

    conn.commit()
    conn.close()

    print(f"Created {created_count} new series, assigned {assigned_count} games")
    return created_count, assigned_count


def backfill_series_source(conn: sqlite3.Connection) -> None:
    """Stamp series_source on pre-existing assignments that predate the column.
    'auto' if the current series equals what prefix-matching would produce for the
    title, else 'manual' (a human must have set it). Only fills NULL source rows."""
    known = load_series_patterns()
    rows = conn.execute("""
        SELECT ur.game_id, g.title, s.name AS series_name
        FROM user_ratings ur
        JOIN games g ON g.id = ur.game_id
        JOIN series s ON s.id = ur.series_id
        WHERE ur.series_id IS NOT NULL AND ur.series_source IS NULL
    """).fetchall()
    for r in rows:
        source = "auto" if match_series_prefix(r["title"], known) == r["series_name"] else "manual"
        conn.execute("UPDATE user_ratings SET series_source = ? WHERE game_id = ?",
                     (source, r["game_id"]))
    conn.commit()


SERIES_ROLE_VALUES = frozenset({"mainline", "spinoff"})


def apply_series_catalog(conn: sqlite3.Connection, game_id: int | None = None,
                         *, dry_run: bool = False) -> list[dict]:
    """Default games into series from the per-title catalog. Fill-only, idempotent.

    Pass A (membership+order): bucket catalog-matched games by target series; join an
    existing series always, create a new series only at >=2 catalog-matched games;
    write series_id/series_order/series_source='catalog' unless the current source is
    'manual'. Absent order leaves the existing series_order unchanged. Pass B (role):
    write games.series_role (source='catalog') unless series_role_source='manual'.
    Matches by normalized_title. game_id scopes to one game; dry_run writes nothing.
    Missing catalog/entry/file is a safe no-op. Returns a report list.
    Report entries are {series, action, created, assigned} where action is 'created', 'joined', or 'skipped_singleton'.
    When game_id is given (on-add), only existing series are joined; creating a brand-new series is a library-wide decision left to the full pass (game_id=None on startup/bulk apply).
    """
    catalog = load_series_catalog()
    if not catalog:
        return []

    game_sql = "SELECT id, normalized_title, series_role_source FROM games"
    params: tuple = ()
    if game_id is not None:
        game_sql += " WHERE id = ?"
        params = (game_id,)
    games = conn.execute(game_sql, params).fetchall()

    # --- Pass A: membership + order ---
    by_series: dict[str, list[tuple[int, dict]]] = {}
    for g in games:
        entry = catalog.get(g["normalized_title"])
        if entry and entry.get("series"):
            by_series.setdefault(entry["series"], []).append((g["id"], entry))

    report: list[dict] = []
    for series_name, members in by_series.items():
        existing = conn.execute("SELECT id FROM series WHERE name = ?", (series_name,)).fetchone()
        if existing:
            series_id, created = existing["id"], False
        elif len(members) < 2:
            report.append({"series": series_name, "action": "skipped_singleton",
                           "created": False, "assigned": 0})
            continue
        else:
            created = True
            if dry_run:
                series_id = None  # unused in dry-run; the per-member write below is skipped
            else:
                conn.execute("INSERT INTO series (name) VALUES (?)", (series_name,))
                series_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        assigned = 0
        for gid, entry in members:
            cur = conn.execute(
                "SELECT series_source FROM user_ratings WHERE game_id = ?", (gid,)).fetchone()
            if cur and cur["series_source"] == "manual":
                continue  # locked
            assigned += 1
            if dry_run:
                continue
            conn.execute("""
                INSERT INTO user_ratings (game_id, series_id, series_order, series_source)
                VALUES (?, ?, ?, 'catalog')
                ON CONFLICT(game_id) DO UPDATE SET
                    series_id = excluded.series_id,
                    series_order = COALESCE(excluded.series_order, user_ratings.series_order),
                    series_source = excluded.series_source,
                    updated_at = CURRENT_TIMESTAMP
            """, (gid, series_id, entry.get("order")))
        report.append({"series": series_name, "action": "created" if created else "joined",
                       "created": created, "assigned": assigned})

    # --- Pass B: role (independent of membership) ---
    for g in games:
        entry = catalog.get(g["normalized_title"])
        if not entry:
            continue
        role = entry.get("role")
        if role not in SERIES_ROLE_VALUES:
            continue
        if g["series_role_source"] == "manual":
            continue
        if not dry_run:
            conn.execute(
                "UPDATE games SET series_role = ?, series_role_source = 'catalog' WHERE id = ?",
                (role, g["id"]))

    if not dry_run:
        conn.commit()
    return report


if __name__ == "__main__":
    init_db()
