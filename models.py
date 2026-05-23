import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "games.db"

# Platform era classification (module-level, immutable).
PC_PLATFORMS = frozenset({"PC", "Steam", "GOG", "Epic", "EGS"})
LEGACY_PLATFORMS = frozenset({
    "PS3", "PS2", "PS1", "PSX", "PSV", "Vita", "PSP",
    "X360", "XBOX", "OGXbox",
    "Wii", "WiiU", "GC", "GCN", "N64", "SNES", "NES",
    "3DS", "NDS", "DS", "GBA", "GBC", "GB",
    "Genesis", "Saturn", "Dreamcast",
})

MODERN_CONSOLE = "modern_console"
LEGACY_CONSOLE = "legacy_console"
PC_CATEGORY = "pc"


def classify_platform(short_name: str) -> str:
    """Map a platform short_name to an era category."""
    if short_name in PC_PLATFORMS:
        return PC_CATEGORY
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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
    """)

    # Insert default platforms
    platforms = [
        ("PlayStation", "PS", "modern_console"),
        ("Nintendo Switch", "Switch", "modern_console"),
        ("Xbox", "Xbox", "modern_console"),
        ("PC", "PC", "pc"),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO platforms (name, short_name, category) VALUES (?, ?, ?)",
        platforms
    )

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

    # Add/backfill platform era category
    migrate_platform_category(conn)

    # Add the external-ids identity table
    migrate_external_ids(conn)

    # Add the not-duplicates table (dedup workstream)
    migrate_not_duplicates(conn)

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

    # Known series patterns (prefix -> series name)
    known_series = {
        "Assassin's Creed": "Assassin's Creed",
        "Assassins Creed": "Assassin's Creed",
        "FINAL FANTASY": "Final Fantasy",
        "Final Fantasy": "Final Fantasy",
        "DRAGON QUEST": "Dragon Quest",
        "Dragon Quest": "Dragon Quest",
        "Castlevania": "Castlevania",
        "Danganronpa": "Danganronpa",
        "Darksiders": "Darksiders",
        "Batman": "Batman",
        "Borderlands": "Borderlands",
        "Bloodstained": "Bloodstained",
        "Blaster Master Zero": "Blaster Master Zero",
        "Kingdom Hearts": "Kingdom Hearts",
        "The Legend of Zelda": "The Legend of Zelda",
        "Legend of Zelda": "The Legend of Zelda",
        "Zelda": "The Legend of Zelda",
        "Super Mario": "Super Mario",
        "Mario": "Mario",
        "Metroid": "Metroid",
        "Resident Evil": "Resident Evil",
        "Metal Gear": "Metal Gear",
        "Devil May Cry": "Devil May Cry",
        "Mega Man": "Mega Man",
        "Axiom Verge": "Axiom Verge",
        "Diablo": "Diablo",
        "Fallout": "Fallout",
        "Halo": "Halo",
        "Grand Theft Auto": "Grand Theft Auto",
        "God of War": "God of War",
        "Dark Souls": "Dark Souls",
        "Souls": "Souls",
        "Elden Ring": "Elden Ring",
        "Pokemon": "Pokemon",
        "Pok\u00e9mon": "Pokemon",
        "Monster Hunter": "Monster Hunter",
        "Ratchet & Clank": "Ratchet & Clank",
        "Ratchet and Clank": "Ratchet & Clank",
        "Uncharted": "Uncharted",
        "The Last of Us": "The Last of Us",
        "Last of Us": "The Last of Us",
        "Horizon": "Horizon",
        "Spider-Man": "Spider-Man",
        "Spiderman": "Spider-Man",
        "Mass Effect": "Mass Effect",
        "Persona": "Persona",
        "Shin Megami Tensei": "Shin Megami Tensei",
        "Fire Emblem": "Fire Emblem",
        "Xenoblade": "Xenoblade",
        "Tales of": "Tales of",
        "Ys": "Ys",
        "Atelier": "Atelier",
        "Disgaea": "Disgaea",
        "NieR": "NieR",
        "Nier": "NieR",
        "Yakuza": "Yakuza",
        "Like a Dragon": "Like a Dragon",
        "Sonic": "Sonic",
        "Kirby": "Kirby",
        "Shovel Knight": "Shovel Knight",
        "Ori": "Ori",
        "Hollow Knight": "Hollow Knight",
        "Cat Quest": "Cat Quest",
        "Cozy Grove": "Cozy Grove",
        "Dadish": "Dadish",
        "Deponia": "Deponia",
        "Deus Ex": "Deus Ex",
        "Dishonored": "Dishonored",
        "Ghost of Tsushima": "Ghost of Tsushima",
        "Guacamelee": "Guacamelee",
        "Hello Neighbor": "Hello Neighbor",
        "AI: THE SOMNIUM FILES": "AI: The Somnium Files",
        "Alwa's": "Alwa's",
        "Blossom Tales": "Blossom Tales",
        "Bubble Trouble": "Bubble Trouble",
        "Cyberpunk 2077": "Cyberpunk 2077",
        "DOOM": "DOOM",
        "Doom": "DOOM",
        "Wolfenstein": "Wolfenstein",
        "BioShock": "BioShock",
        "Bioshock": "BioShock",
        "Dead Space": "Dead Space",
        "Tomb Raider": "Tomb Raider",
        "Hitman": "Hitman",
        "Just Cause": "Just Cause",
        "Far Cry": "Far Cry",
        "Watch Dogs": "Watch Dogs",
        "The Witcher": "The Witcher",
        "Witcher": "The Witcher",
        "Cyberpunk": "Cyberpunk",
        "Call of Duty": "Call of Duty",
        "Battlefield": "Battlefield",
        "Crysis": "Crysis",
        "Saints Row": "Saints Row",
        "Sleeping Dogs": "Sleeping Dogs",
        "Dead Rising": "Dead Rising",
        "Left 4 Dead": "Left 4 Dead",
        "Portal": "Portal",
        "Half-Life": "Half-Life",
        "Half Life": "Half-Life",
        "Quake": "Quake",
        "Mortal Kombat": "Mortal Kombat",
        "Street Fighter": "Street Fighter",
        "Tekken": "Tekken",
        "SoulCalibur": "SoulCalibur",
        "Soul Calibur": "SoulCalibur",
        "Guilty Gear": "Guilty Gear",
        "BlazBlue": "BlazBlue",
        "Dragon Ball": "Dragon Ball",
        "DRAGON BALL": "Dragon Ball",
        "Naruto": "Naruto",
        "One Piece": "One Piece",
        "CODE VEIN": "Code Vein",
        "Control": "Control",
        "Disco Elysium": "Disco Elysium",
        "Don't Starve": "Don't Starve",
    }

    # Get all games
    games = conn.execute("SELECT id, title FROM games ORDER BY title").fetchall()

    # Track which series we've created and which games belong to them
    series_games = {}  # series_name -> [(game_id, title, sort_key)]

    for game in games:
        game_id = game['id']
        title = game['title']

        # Check against known series patterns
        matched_series = None
        for prefix, series_name in known_series.items():
            if title.upper().startswith(prefix.upper()):
                matched_series = series_name
                break

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
                INSERT INTO user_ratings (game_id, series_id, series_order)
                VALUES (?, ?, ?)
                ON CONFLICT(game_id) DO UPDATE SET
                    series_id = excluded.series_id,
                    series_order = excluded.series_order,
                    updated_at = CURRENT_TIMESTAMP
            """, (game_id, series_id, order))
            assigned_count += 1

    conn.commit()
    conn.close()

    print(f"Created {created_count} new series, assigned {assigned_count} games")
    return created_count, assigned_count


if __name__ == "__main__":
    init_db()
