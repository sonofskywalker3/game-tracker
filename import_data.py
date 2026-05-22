"""
Import game data from CSV files and psprices HTML backup.
"""
import csv
import re
import sqlite3
from pathlib import Path
from html.parser import HTMLParser

from models import get_db, init_db, normalize_title, DB_PATH


class PSPricesParser(HTMLParser):
    """Parse game data from psprices HTML backup."""

    def __init__(self):
        super().__init__()
        self.games = []
        self.in_game_fragment = False
        self.current_game = None
        self.capture_img = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)

        # Look for game-fragment divs
        if tag == 'div' and 'game-fragment' in attrs_dict.get('class', ''):
            self.in_game_fragment = True
            self.current_game = {
                'psprices_id': attrs_dict.get('data-id', ''),
                'status': attrs_dict.get('data-status', ''),
                'title': self._decode_html(attrs_dict.get('data-title', '')),
                'metacritic': self._parse_rating(attrs_dict.get('data-meta-rating', '')),
                'opencritic': self._parse_rating(attrs_dict.get('data-open-rating', '')),
                'cover_url': None
            }
            self.capture_img = True

        # Capture cover image
        if self.capture_img and tag == 'img' and self.current_game:
            src = attrs_dict.get('src', '')
            if src and 'placeholder' not in src:
                self.current_game['cover_url'] = src
                self.capture_img = False

    def handle_endtag(self, tag):
        if tag == 'div' and self.in_game_fragment and self.current_game:
            if self.current_game.get('title'):
                self.games.append(self.current_game)
            self.current_game = None
            self.in_game_fragment = False
            self.capture_img = False

    def _decode_html(self, text):
        """Decode HTML entities."""
        text = text.replace('&quot;', '"')
        text = text.replace('&amp;', '&')
        text = text.replace('&lt;', '<')
        text = text.replace('&gt;', '>')
        text = text.replace('&#39;', "'")
        text = text.replace('&apos;', "'")
        return text

    def _parse_rating(self, rating_str):
        """Parse rating string to integer or None."""
        if not rating_str:
            return None
        rating_str = rating_str.strip()
        if rating_str:
            try:
                return int(rating_str)
            except ValueError:
                return None
        return None


def map_status(psprices_status):
    """Map psprices status to our status values."""
    mapping = {
        'not_started': 'backlog',
        'planned': 'backlog',
        'played': 'playing',
        'completed': 'completed'
    }
    return mapping.get(psprices_status, 'backlog')


def import_csv_games(csv_path, platform_short_name, conn):
    """Import games from a CSV file."""
    platform_id = conn.execute(
        "SELECT id FROM platforms WHERE short_name = ?",
        (platform_short_name,)
    ).fetchone()

    if not platform_id:
        print(f"Platform {platform_short_name} not found!")
        return 0

    platform_id = platform_id[0]
    imported = 0

    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        next(reader)  # Skip header

        for row in reader:
            if not row or not row[0].strip():
                continue

            title = row[0].strip()
            normalized = normalize_title(title)

            # Insert or get game
            conn.execute("""
                INSERT OR IGNORE INTO games (title, normalized_title)
                VALUES (?, ?)
            """, (title, normalized))

            game_id = conn.execute(
                "SELECT id FROM games WHERE normalized_title = ?",
                (normalized,)
            ).fetchone()[0]

            # Link to platform
            conn.execute("""
                INSERT OR IGNORE INTO game_platforms (game_id, platform_id)
                VALUES (?, ?)
            """, (game_id, platform_id))

            # Create default user_ratings entry if not exists
            conn.execute("""
                INSERT OR IGNORE INTO user_ratings (game_id, status)
                VALUES (?, 'backlog')
            """, (game_id,))

            imported += 1

    return imported


def import_psprices_html(html_path, conn):
    """Import games from psprices HTML backup."""
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()

    parser = PSPricesParser()
    parser.feed(content)

    # Get PlayStation platform ID (psprices is primarily PlayStation)
    ps_platform = conn.execute(
        "SELECT id FROM platforms WHERE short_name = 'PS'"
    ).fetchone()

    # Also get Switch platform for Nintendo games
    switch_platform = conn.execute(
        "SELECT id FROM platforms WHERE short_name = 'Switch'"
    ).fetchone()

    imported = 0
    updated = 0

    for game in parser.games:
        title = game['title']
        normalized = normalize_title(title)

        # Check if game exists
        existing = conn.execute(
            "SELECT id FROM games WHERE normalized_title = ?",
            (normalized,)
        ).fetchone()

        if existing:
            game_id = existing[0]
            # Update with psprices data (ratings, cover)
            conn.execute("""
                UPDATE games
                SET metacritic_score = COALESCE(?, metacritic_score),
                    opencritic_score = COALESCE(?, opencritic_score),
                    cover_url = COALESCE(?, cover_url),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (game['metacritic'], game['opencritic'], game['cover_url'], game_id))
            updated += 1
        else:
            # Insert new game
            conn.execute("""
                INSERT INTO games (title, normalized_title, metacritic_score, opencritic_score, cover_url)
                VALUES (?, ?, ?, ?, ?)
            """, (title, normalized, game['metacritic'], game['opencritic'], game['cover_url']))
            game_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            imported += 1

        # Determine platform from title hints
        platform_id = ps_platform[0] if ps_platform else None
        title_lower = title.lower()
        if 'nintendo' in title_lower or 'switch' in title_lower:
            platform_id = switch_platform[0] if switch_platform else platform_id

        # Link to platform with psprices_id
        if platform_id:
            conn.execute("""
                INSERT OR REPLACE INTO game_platforms (game_id, platform_id, psprices_id)
                VALUES (?, ?, ?)
            """, (game_id, platform_id, game['psprices_id']))

        # Update user ratings with status from psprices
        our_status = map_status(game['status'])
        conn.execute("""
            INSERT INTO user_ratings (game_id, status)
            VALUES (?, ?)
            ON CONFLICT(game_id) DO UPDATE SET
                status = CASE
                    WHEN excluded.status IN ('playing', 'completed') THEN excluded.status
                    ELSE user_ratings.status
                END,
                updated_at = CURRENT_TIMESTAMP
        """, (game_id, our_status))

    return imported, updated


def run_import():
    """Run the full import process."""
    project_dir = Path(__file__).parent

    # Initialize database
    if not DB_PATH.exists():
        init_db()

    conn = get_db()

    print("Importing game data...\n")

    # Import CSV files
    csv_files = [
        ("playstation_games.csv", "PS"),
        ("switch_games.csv", "Switch"),
        ("xbox_games.csv", "Xbox"),
    ]

    for filename, platform in csv_files:
        csv_path = project_dir / filename
        if csv_path.exists():
            count = import_csv_games(csv_path, platform, conn)
            print(f"  {platform}: {count} games imported from {filename}")
        else:
            print(f"  {filename} not found, skipping...")

    # Import psprices HTML
    psprices_path = Path.home() / "Documents" / "My Game Backlog.html"
    if psprices_path.exists():
        imported, updated = import_psprices_html(psprices_path, conn)
        print(f"\n  PSPrices: {imported} new games, {updated} updated with ratings/covers")
    else:
        print(f"\n  PSPrices HTML not found at {psprices_path}, skipping...")

    conn.commit()

    # Print summary
    total_games = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    total_with_ratings = conn.execute(
        "SELECT COUNT(*) FROM games WHERE metacritic_score IS NOT NULL OR opencritic_score IS NOT NULL"
    ).fetchone()[0]

    print(f"\n{'='*50}")
    print(f"Total games in database: {total_games}")
    print(f"Games with critic scores: {total_with_ratings}")

    # Status breakdown
    print("\nStatus breakdown:")
    for row in conn.execute("""
        SELECT status, COUNT(*) as count
        FROM user_ratings
        GROUP BY status
        ORDER BY count DESC
    """):
        print(f"  {row['status']}: {row['count']}")

    # Platform breakdown
    print("\nPlatform breakdown:")
    for row in conn.execute("""
        SELECT p.name, COUNT(gp.game_id) as count
        FROM platforms p
        LEFT JOIN game_platforms gp ON p.id = gp.platform_id
        GROUP BY p.id
        ORDER BY count DESC
    """):
        print(f"  {row['name']}: {row['count']}")

    conn.close()
    print(f"\nDatabase saved to: {DB_PATH}")


if __name__ == "__main__":
    run_import()
