"""
Game Tracker - Flask Application
"""
import sqlite3
import dedup
from flask import Flask, render_template, request, jsonify
from models import (
    get_db, init_db, migrate_db, normalize_title, clean_title,
    reclean_display_titles, DB_PATH,
)
from recommendation import get_recommendations, get_quick_picks
from config import load_config, save_config, get_twitch_credentials
from background_tasks import run_cover_fetch_background, get_cover_fetch_status

app = Flask(__name__)


# ============================================================================
# Template Routes
# ============================================================================

@app.route('/')
def index():
    """Main page - show game library."""
    return render_template('index.html')


@app.route('/game/<int:game_id>')
def game_detail(game_id):
    """Game detail page."""
    return render_template('game.html', game_id=game_id)


@app.route('/recommendations')
def recommendations_page():
    """Recommendations page."""
    return render_template('recommendations.html')


@app.route('/settings')
def settings_page():
    """Settings page."""
    return render_template('settings.html')


@app.route('/series')
@app.route('/series/<int:series_id>')
def series_page(series_id=None):
    """Series management page."""
    return render_template('series.html', series_id=series_id)


# ============================================================================
# API Routes
# ============================================================================

@app.route('/api/games')
def api_games():
    """Get all games with filters."""
    conn = get_db()

    status = request.args.get('status', None)
    platform = request.args.get('platform', None)
    tag = request.args.get('tag', None)
    search = request.args.get('search', None)
    sort = request.args.get('sort', 'title')
    order = request.args.get('order', 'asc')

    query = """
        SELECT DISTINCT
            g.id,
            g.title,
            g.cover_url,
            g.metacritic_score,
            g.opencritic_score,
            ur.status,
            ur.rating,
            ur.priority,
            ur.hours_played,
            ur.notes,
            ur.series_id,
            ur.series_order,
            s.name as series_name
        FROM games g
        LEFT JOIN user_ratings ur ON ur.game_id = g.id
        LEFT JOIN series s ON s.id = ur.series_id
        LEFT JOIN game_platforms gp ON gp.game_id = g.id
        LEFT JOIN platforms p ON p.id = gp.platform_id
        LEFT JOIN game_tags gt ON gt.game_id = g.id
        LEFT JOIN tags t ON t.id = gt.tag_id
        WHERE 1=1
    """
    params = []

    if status:
        query += " AND ur.status = ?"
        params.append(status)

    if platform:
        query += " AND p.short_name = ?"
        params.append(platform)

    if tag:
        query += " AND t.name = ?"
        params.append(tag)

    if search:
        query += " AND g.title LIKE ?"
        params.append(f"%{search}%")

    missing_art = request.args.get('missing_art', None)
    if missing_art:
        query += " AND (g.cover_url IS NULL OR g.cover_url = '')"

    # Sorting
    sort_columns = {
        'title': 'g.title',
        'rating': 'ur.rating',
        'priority': 'ur.priority',
        'metacritic': 'g.metacritic_score',
        'status': 'ur.status',
        'manual': 'ur.sort_order'
    }
    sort_col = sort_columns.get(sort, 'g.title')
    sort_order = 'DESC' if order == 'desc' else 'ASC'

    # Handle NULL values in sorting
    if sort in ['rating', 'priority', 'metacritic']:
        query += f" ORDER BY {sort_col} IS NULL, {sort_col} {sort_order}"
    elif sort == 'manual':
        # For manual sort, put NULL sort_order last, then sort by sort_order ASC
        query += " ORDER BY ur.sort_order IS NULL, ur.sort_order ASC, g.title ASC"
    elif sort == 'title':
        # For title sort, use series name if in a series, then series_order within series
        # Games not in a series sort by their own title
        query += f" ORDER BY COALESCE(s.name, g.title) {sort_order}, ur.series_order ASC, g.title ASC"
    else:
        query += f" ORDER BY {sort_col} {sort_order}"

    rows = conn.execute(query, params).fetchall()

    games = []
    for row in rows:
        game = dict(row)

        # Get platforms (+ era category) for this game
        platforms = conn.execute("""
            SELECT p.short_name, p.category
            FROM platforms p
            JOIN game_platforms gp ON gp.platform_id = p.id
            WHERE gp.game_id = ?
        """, (row['id'],)).fetchall()
        game['platforms'] = [p['short_name'] for p in platforms]
        game['categories'] = sorted({p['category'] for p in platforms})

        # Get tags for this game
        tags = conn.execute("""
            SELECT t.name, t.category
            FROM tags t
            JOIN game_tags gt ON gt.tag_id = t.id
            WHERE gt.game_id = ?
        """, (row['id'],)).fetchall()
        game['tags'] = [{'name': t['name'], 'category': t['category']} for t in tags]
        game['physical'] = any(t['name'] == 'Physical' for t in game['tags'])

        games.append(game)

    conn.close()
    return jsonify(games)


@app.route('/api/games', methods=['POST'])
def api_create_game():
    """Create a new game."""
    conn = get_db()
    data = request.json

    title = data.get('title', '').strip()
    if not title:
        conn.close()
        return jsonify({'error': 'Title is required'}), 400

    # Clean up the title (remove platform indicators, trademark symbols)
    title = clean_title(title)
    normalized = normalize_title(title)

    # Check if game already exists
    existing = conn.execute(
        "SELECT id FROM games WHERE normalized_title = ?",
        (normalized,)
    ).fetchone()

    if existing:
        conn.close()
        return jsonify({'error': 'Game already exists', 'game_id': existing['id']}), 409

    # Insert new game
    cover_url = data.get('cover_url', '').strip() or None
    conn.execute(
        "INSERT INTO games (title, normalized_title, cover_url) VALUES (?, ?, ?)",
        (title, normalized, cover_url)
    )
    game_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Create default user_ratings entry
    conn.execute(
        "INSERT INTO user_ratings (game_id, status) VALUES (?, 'backlog')",
        (game_id,)
    )

    # Add platforms if provided
    platforms = data.get('platforms', [])
    for platform_short_name in platforms:
        platform = conn.execute(
            "SELECT id FROM platforms WHERE short_name = ?",
            (platform_short_name,)
        ).fetchone()
        if platform:
            conn.execute(
                "INSERT INTO game_platforms (game_id, platform_id) VALUES (?, ?)",
                (game_id, platform['id'])
            )

    conn.commit()
    conn.close()

    return jsonify({'success': True, 'game_id': game_id}), 201


@app.route('/api/games/<int:game_id>')
def api_game(game_id):
    """Get single game details."""
    conn = get_db()

    game = conn.execute("""
        SELECT
            g.*,
            ur.status,
            ur.rating,
            ur.priority,
            ur.hours_played,
            ur.notes,
            ur.started_at,
            ur.completed_at,
            ur.series_id,
            ur.series_order,
            s.name as series_name
        FROM games g
        LEFT JOIN user_ratings ur ON ur.game_id = g.id
        LEFT JOIN series s ON s.id = ur.series_id
        WHERE g.id = ?
    """, (game_id,)).fetchone()

    if not game:
        conn.close()
        return jsonify({'error': 'Game not found'}), 404

    result = dict(game)

    # Get platforms
    platforms = conn.execute("""
        SELECT p.id, p.name, p.short_name
        FROM platforms p
        JOIN game_platforms gp ON gp.platform_id = p.id
        WHERE gp.game_id = ?
    """, (game_id,)).fetchall()
    result['platforms'] = [dict(p) for p in platforms]

    # Get tags
    tags = conn.execute("""
        SELECT t.id, t.name, t.category
        FROM tags t
        JOIN game_tags gt ON gt.tag_id = t.id
        WHERE gt.game_id = ?
    """, (game_id,)).fetchall()
    result['tags'] = [dict(t) for t in tags]

    conn.close()
    return jsonify(result)


@app.route('/api/games/<int:game_id>', methods=['PUT'])
def api_update_game(game_id):
    """Update game rating, status, priority, title, cover_url, etc."""
    conn = get_db()
    data = request.json

    try:
        # Update game table fields (title, cover_url)
        if 'title' in data or 'cover_url' in data:
            game_updates = []
            game_params = []
            if 'title' in data:
                game_updates.append("title = ?")
                game_updates.append("normalized_title = ?")
                game_params.append(data['title'])
                game_params.append(normalize_title(data['title']))
            if 'cover_url' in data:
                game_updates.append("cover_url = ?")
                game_params.append(data['cover_url'] if data['cover_url'] else None)
            game_updates.append("updated_at = CURRENT_TIMESTAMP")
            game_params.append(game_id)
            conn.execute(f"UPDATE games SET {', '.join(game_updates)} WHERE id = ?", game_params)

        # Update user_ratings
        updates = []
        params = []

        for field in ['status', 'rating', 'priority', 'notes', 'hours_played', 'started_at', 'completed_at']:
            if field in data:
                updates.append(f"{field} = ?")
                params.append(data[field])

        if updates:
            field_names = [f.split(' = ')[0] for f in updates]
            updates.append("updated_at = CURRENT_TIMESTAMP")

            conn.execute(f"""
                INSERT INTO user_ratings (game_id, {', '.join(field_names)})
                VALUES (?, {', '.join('?' * len(params))})
                ON CONFLICT(game_id) DO UPDATE SET {', '.join(updates)}
            """, [game_id] + params + params)

        # Update tags if provided
        if 'tags' in data:
            # Remove existing tags
            conn.execute("DELETE FROM game_tags WHERE game_id = ?", (game_id,))

            # Add new tags
            for tag_name in data['tags']:
                # Get or create tag
                tag = conn.execute("SELECT id FROM tags WHERE name = ?", (tag_name,)).fetchone()
                if not tag:
                    conn.execute("INSERT INTO tags (name, category) VALUES (?, 'custom')", (tag_name,))
                    tag_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                else:
                    tag_id = tag['id']

                conn.execute("INSERT OR IGNORE INTO game_tags (game_id, tag_id) VALUES (?, ?)",
                            (game_id, tag_id))

        # Update platforms if provided
        if 'platforms' in data:
            # Remove existing platform links
            conn.execute("DELETE FROM game_platforms WHERE game_id = ?", (game_id,))

            # Add new platform links
            for platform_short_name in data['platforms']:
                platform = conn.execute(
                    "SELECT id FROM platforms WHERE short_name = ?",
                    (platform_short_name,)
                ).fetchone()
                if platform:
                    conn.execute(
                        "INSERT OR IGNORE INTO game_platforms (game_id, platform_id) VALUES (?, ?)",
                        (game_id, platform['id'])
                    )

        conn.commit()
        return jsonify({'success': True})
    except sqlite3.IntegrityError:
        return jsonify({'error': 'A game with that name already exists'}), 400
    finally:
        conn.close()


@app.route('/api/games/<int:game_id>', methods=['DELETE'])
def api_delete_game(game_id):
    """Delete a game and all its related data."""
    conn = get_db()

    try:
        # Check if game exists
        game = conn.execute("SELECT id FROM games WHERE id = ?", (game_id,)).fetchone()
        if not game:
            return jsonify({'error': 'Game not found'}), 404

        # Delete the game (CASCADE will handle related tables)
        conn.execute("DELETE FROM games WHERE id = ?", (game_id,))
        conn.commit()

        return jsonify({'success': True})
    finally:
        conn.close()


@app.route('/api/games/normalize', methods=['POST'])
def api_normalize_titles():
    """Re-clean all display titles with the current clean_title rules.

    Display-only: never recomputes normalized_title. Recomputing the match key and
    merging the duplicates it surfaces is the dedup workstream; leaving it alone
    means an improved clean_title can't trip UNIQUE(normalized_title). The legacy
    {"force": true} flag is accepted for compatibility but no longer changes the
    result — clean_title already smart-title-cases ALL-CAPS titles.
    """
    conn = get_db()
    changes = reclean_display_titles(conn)
    if changes:
        conn.commit()
    conn.close()
    return jsonify({
        'cleaned_count': len(changes),
        'titles': [{'original': c['original'], 'cleaned': c['cleaned']} for c in changes],
    })


@app.route('/api/duplicates')
def api_duplicates():
    """Detect duplicate games for the dedup modal."""
    conn = get_db()
    groups = dedup.find_duplicate_groups(conn)
    referenced = {gid for group in groups["definite"] for gid in group}
    referenced |= {c["a"] for c in groups["candidates"]}
    referenced |= {c["b"] for c in groups["candidates"]}

    games = []
    for gid in referenced:
        g = conn.execute("SELECT id, title, cover_url FROM games WHERE id = ?", (gid,)).fetchone()
        if not g:
            continue
        platforms = [r["short_name"] for r in conn.execute(
            "SELECT p.short_name FROM game_platforms gp JOIN platforms p "
            "ON p.id = gp.platform_id WHERE gp.game_id = ?", (gid,))]
        ur = conn.execute(
            "SELECT status, rating, notes, priority, hours_played, started_at, "
            "completed_at, sort_order, series_id, series_order "
            "FROM user_ratings WHERE game_id = ?", (gid,)).fetchone()
        games.append({
            "id": g["id"], "title": g["title"], "cover_url": g["cover_url"],
            "platforms": platforms,
            "curation": dict(ur) if ur else {"status": "backlog"},
        })
    conn.close()
    return jsonify({"definite": groups["definite"],
                    "candidates": groups["candidates"], "games": games})


@app.route('/api/games/reorder', methods=['POST'])
def api_reorder_games():
    """Update the sort order of games based on drag-and-drop reordering."""
    conn = get_db()
    data = request.json

    game_ids = data.get('game_ids', [])
    if not game_ids:
        conn.close()
        return jsonify({'error': 'No game IDs provided'}), 400

    # Update sort_order for each game, ensuring user_ratings row exists
    for index, game_id in enumerate(game_ids):
        conn.execute("""
            INSERT INTO user_ratings (game_id, sort_order)
            VALUES (?, ?)
            ON CONFLICT(game_id) DO UPDATE SET
                sort_order = excluded.sort_order,
                updated_at = CURRENT_TIMESTAMP
        """, (game_id, index))

    conn.commit()
    conn.close()

    return jsonify({'success': True})


# ============================================================================
# Series API Routes
# ============================================================================

@app.route('/api/series')
def api_series():
    """Get all series with their games."""
    conn = get_db()
    series_list = conn.execute("""
        SELECT s.*, COUNT(ur.game_id) as game_count
        FROM series s
        LEFT JOIN user_ratings ur ON ur.series_id = s.id
        GROUP BY s.id
        ORDER BY s.name
    """).fetchall()

    result = []
    for s in series_list:
        series_dict = dict(s)
        # Get games in this series
        games = conn.execute("""
            SELECT g.id, g.title, g.cover_url, ur.series_order
            FROM games g
            JOIN user_ratings ur ON ur.game_id = g.id
            WHERE ur.series_id = ?
            ORDER BY ur.series_order, g.title
        """, (s['id'],)).fetchall()
        series_dict['games'] = [dict(g) for g in games]
        result.append(series_dict)

    conn.close()
    return jsonify(result)


@app.route('/api/series', methods=['POST'])
def api_create_series():
    """Create a new series."""
    conn = get_db()
    data = request.json

    name = data.get('name', '').strip()
    if not name:
        conn.close()
        return jsonify({'error': 'Name is required'}), 400

    # Check if series already exists
    existing = conn.execute("SELECT id FROM series WHERE name = ?", (name,)).fetchone()
    if existing:
        conn.close()
        return jsonify({'error': 'Series already exists', 'series_id': existing['id']}), 409

    conn.execute("INSERT INTO series (name) VALUES (?)", (name,))
    series_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()

    return jsonify({'success': True, 'series_id': series_id}), 201


@app.route('/api/series/<int:series_id>', methods=['DELETE'])
def api_delete_series(series_id):
    """Delete a series (games are kept, just unlinked)."""
    conn = get_db()

    # Unlink games from this series
    conn.execute("UPDATE user_ratings SET series_id = NULL, series_order = NULL WHERE series_id = ?", (series_id,))
    conn.execute("DELETE FROM series WHERE id = ?", (series_id,))
    conn.commit()
    conn.close()

    return jsonify({'success': True})


@app.route('/api/series/<int:series_id>', methods=['PUT'])
def api_rename_series(series_id):
    """Rename a series."""
    conn = get_db()
    data = request.json

    name = data.get('name', '').strip()
    if not name:
        conn.close()
        return jsonify({'error': 'Series name is required'}), 400

    # Check if name already exists (for a different series)
    existing = conn.execute(
        "SELECT id FROM series WHERE name = ? AND id != ?",
        (name, series_id)
    ).fetchone()

    if existing:
        conn.close()
        return jsonify({'error': 'A series with this name already exists'}), 409

    conn.execute("UPDATE series SET name = ? WHERE id = ?", (name, series_id))
    conn.commit()
    conn.close()

    return jsonify({'success': True})


@app.route('/api/series/suggestions')
def api_series_suggestions():
    """Suggest series names based on existing games in library."""
    import re
    from collections import Counter

    conn = get_db()

    # Get all game titles
    games = conn.execute("SELECT title FROM games").fetchall()
    titles = [g['title'] for g in games]

    # Get existing series names
    existing_series = conn.execute("SELECT name FROM series").fetchall()
    existing_names = set(s['name'].lower() for s in existing_series)

    conn.close()

    # Common patterns to extract series names
    suggestions = Counter()

    for title in titles:
        # Pattern: "Name: Subtitle" or "Name - Subtitle"
        match = re.match(r'^([^:\-–]+)[:\-–]', title)
        if match:
            name = match.group(1).strip()
            if len(name) > 2:
                suggestions[name] += 1

        # Pattern: "Name 2", "Name II", "Name 3", etc.
        match = re.match(r'^(.+?)\s+(?:\d+|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII)(?:\s|$|:)', title)
        if match:
            name = match.group(1).strip()
            if len(name) > 2:
                suggestions[name] += 1

        # Pattern: "Name Remastered/Remake/HD/Definitive"
        match = re.match(r'^(.+?)\s+(?:Remastered|Remake|HD|Definitive|GOTY|Edition|Complete|Ultimate|Enhanced)(?:\s|$)', title, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            if len(name) > 2:
                suggestions[name] += 1

    # Filter out suggestions that already exist or only have 1 game
    filtered = [
        {'name': name, 'count': count}
        for name, count in suggestions.most_common(20)
        if count >= 1 and name.lower() not in existing_names
    ]

    return jsonify(filtered)


def get_original_title(title):
    """Strip remaster/remake suffixes to get the original game title."""
    import re
    # Remove common remaster/remake suffixes
    patterns = [
        r'\s*[-–:]\s*(Remastered|Remake|HD|Definitive|GOTY|Complete|Ultimate|Enhanced|Anniversary|Deluxe|Special|Director\'?s?\s*Cut).*$',
        r'\s+(Remastered|Remake|HD Remaster|HD Edition|Definitive Edition|GOTY Edition|Complete Edition|Ultimate Edition|Enhanced Edition|Anniversary Edition|Deluxe Edition|Special Edition|Director\'?s?\s*Cut).*$',
    ]
    original = title
    for pattern in patterns:
        original = re.sub(pattern, '', original, flags=re.IGNORECASE)
    return original.strip()


@app.route('/api/series/<int:series_id>/sort-by-release', methods=['POST'])
def api_sort_series_by_release(series_id):
    """Sort games in a series by their original release date using IGDB."""
    from fetch_covers import get_access_token
    import requests

    conn = get_db()

    # Get games in this series
    games = conn.execute("""
        SELECT g.id, g.title
        FROM games g
        JOIN user_ratings ur ON g.id = ur.game_id
        WHERE ur.series_id = ?
    """, (series_id,)).fetchall()

    if not games:
        conn.close()
        return jsonify({'error': 'No games in series'}), 400

    # Get IGDB credentials
    client_id, client_secret = get_twitch_credentials()
    if not client_id or not client_secret:
        conn.close()
        return jsonify({'error': 'IGDB not configured'}), 400

    try:
        access_token = get_access_token(client_id, client_secret)

        headers = {
            'Client-ID': client_id,
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'text/plain'
        }

        # Fetch release dates for all games
        game_dates = []
        for game in games:
            # Get original title (strip remaster/remake suffixes)
            original_title = get_original_title(game['title'])
            search_title = original_title.replace('"', '')

            # Search for the original game
            igdb_query = f'''
                search "{search_title}";
                fields name, first_release_date;
                limit 5;
            '''

            response = requests.post(
                'https://api.igdb.com/v4/games',
                headers=headers,
                data=igdb_query
            )

            release_date = None
            if response.ok:
                results = response.json()
                # Try to find exact or close match
                for result in results:
                    if result.get('first_release_date'):
                        result_name = result.get('name', '').lower()
                        search_lower = search_title.lower()
                        # Prefer exact match or close match
                        if result_name == search_lower or search_lower in result_name or result_name in search_lower:
                            release_date = result['first_release_date']
                            break
                # Fallback to first result with a date
                if not release_date and results:
                    for result in results:
                        if result.get('first_release_date'):
                            release_date = result['first_release_date']
                            break

            game_dates.append({
                'id': game['id'],
                'title': game['title'],
                'release_date': release_date or 9999999999  # Put unknown dates at end
            })

        # Sort by release date
        game_dates.sort(key=lambda g: g['release_date'])

        # Update series order
        for index, game in enumerate(game_dates):
            conn.execute("""
                UPDATE user_ratings
                SET series_order = ?, updated_at = CURRENT_TIMESTAMP
                WHERE game_id = ? AND series_id = ?
            """, (index, game['id'], series_id))

        conn.commit()
        conn.close()

        return jsonify({
            'success': True,
            'order': [g['id'] for g in game_dates]
        })

    except Exception as e:
        conn.close()
        return jsonify({'error': str(e)}), 500


@app.route('/api/series/<int:series_id>/missing')
def api_series_missing_games(series_id):
    """Find games in the IGDB franchise/collection that user doesn't own."""
    from fetch_covers import get_access_token
    import requests

    conn = get_db()

    # Get series name
    series = conn.execute("SELECT name FROM series WHERE id = ?", (series_id,)).fetchone()
    if not series:
        conn.close()
        return jsonify({'error': 'Series not found'}), 404

    series_name = series['name']

    # Get all game titles user owns (normalized for comparison)
    all_games = conn.execute("SELECT title, normalized_title FROM games").fetchall()
    owned_titles = set()
    for g in all_games:
        owned_titles.add(g['normalized_title'].lower() if g['normalized_title'] else g['title'].lower())
        # Also add without common suffixes
        clean = get_original_title(g['title']).lower()
        owned_titles.add(clean)

    conn.close()

    # Get IGDB credentials
    client_id, client_secret = get_twitch_credentials()
    if not client_id or not client_secret:
        return jsonify({'error': 'IGDB not configured'}), 400

    try:
        access_token = get_access_token(client_id, client_secret)

        headers = {
            'Client-ID': client_id,
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'text/plain'
        }

        # First, search for franchises matching the series name
        franchise_query = f'''
            search "{series_name.replace('"', '')}";
            fields name, games.name, games.cover.url, games.first_release_date, games.category;
            limit 3;
        '''

        response = requests.post(
            'https://api.igdb.com/v4/franchises',
            headers=headers,
            data=franchise_query
        )

        all_franchise_games = []
        if response.ok:
            franchises = response.json()
            for franchise in franchises:
                if franchise.get('games'):
                    for game in franchise['games']:
                        if isinstance(game, dict):
                            all_franchise_games.append(game)

        # Also search collections
        collection_query = f'''
            search "{series_name.replace('"', '')}";
            fields name, games.name, games.cover.url, games.first_release_date, games.category;
            limit 3;
        '''

        response = requests.post(
            'https://api.igdb.com/v4/collections',
            headers=headers,
            data=collection_query
        )

        if response.ok:
            collections = response.json()
            for collection in collections:
                if collection.get('games'):
                    for game in collection['games']:
                        if isinstance(game, dict):
                            all_franchise_games.append(game)

        # Filter to main games only (category 0 = main game) and ones user doesn't own
        missing_games = []
        seen_names = set()

        for game in all_franchise_games:
            name = game.get('name', '')
            if not name or name in seen_names:
                continue

            # Check if category is main game (0) or skip if no category
            category = game.get('category', 0)
            if category not in [0, 8, 9, 10, 11]:  # Main game, remake, remaster, expanded, port
                continue

            seen_names.add(name)

            # Check if user owns this (fuzzy match)
            name_lower = name.lower()
            name_clean = get_original_title(name).lower()

            if name_lower in owned_titles or name_clean in owned_titles:
                continue

            # Check partial matches
            owned = False
            for owned_title in owned_titles:
                if name_clean in owned_title or owned_title in name_clean:
                    owned = True
                    break
            if owned:
                continue

            cover_url = None
            if game.get('cover') and isinstance(game['cover'], dict):
                cover_url = game['cover'].get('url', '')
                if cover_url:
                    cover_url = cover_url.replace('t_thumb', 't_cover_big')
                    if not cover_url.startswith('http'):
                        cover_url = 'https:' + cover_url

            missing_games.append({
                'name': name,
                'cover_url': cover_url,
                'release_date': game.get('first_release_date')
            })

        # Sort by release date
        missing_games.sort(key=lambda g: g.get('release_date') or 9999999999)

        return jsonify(missing_games)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/series/<int:series_id>/games', methods=['POST'])
def api_add_game_to_series(series_id):
    """Add a game to a series."""
    conn = get_db()
    data = request.json

    game_id = data.get('game_id')
    if not game_id:
        conn.close()
        return jsonify({'error': 'game_id is required'}), 400

    # Get the next series_order
    max_order = conn.execute(
        "SELECT MAX(series_order) FROM user_ratings WHERE series_id = ?",
        (series_id,)
    ).fetchone()[0]
    next_order = (max_order or 0) + 1

    # Update the game's series
    conn.execute("""
        INSERT INTO user_ratings (game_id, series_id, series_order)
        VALUES (?, ?, ?)
        ON CONFLICT(game_id) DO UPDATE SET
            series_id = excluded.series_id,
            series_order = excluded.series_order,
            updated_at = CURRENT_TIMESTAMP
    """, (game_id, series_id, next_order))

    conn.commit()
    conn.close()

    return jsonify({'success': True})


@app.route('/api/series/<int:series_id>/reorder', methods=['POST'])
def api_reorder_series(series_id):
    """Reorder games within a series."""
    conn = get_db()
    data = request.json

    game_ids = data.get('game_ids', [])

    for index, game_id in enumerate(game_ids):
        conn.execute("""
            UPDATE user_ratings
            SET series_order = ?, updated_at = CURRENT_TIMESTAMP
            WHERE game_id = ? AND series_id = ?
        """, (index, game_id, series_id))

    conn.commit()
    conn.close()

    return jsonify({'success': True})


@app.route('/api/games/<int:game_id>/series', methods=['DELETE'])
def api_remove_game_from_series(game_id):
    """Remove a game from its series."""
    conn = get_db()

    conn.execute("""
        UPDATE user_ratings
        SET series_id = NULL, series_order = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE game_id = ?
    """, (game_id,))

    conn.commit()
    conn.close()

    return jsonify({'success': True})


@app.route('/api/recommendations')
def api_recommendations():
    """Get game recommendations."""
    conn = get_db()
    limit = request.args.get('limit', 10, type=int)

    recs = get_recommendations(conn, limit=limit)
    picks = get_quick_picks(conn)

    # Convert Row objects to dicts for quick picks
    quick_picks = {}
    for category, games in picks.items():
        quick_picks[category] = [dict(g) for g in games]

    conn.close()

    return jsonify({
        'recommendations': recs,
        'quick_picks': quick_picks
    })


@app.route('/api/tags')
def api_tags():
    """Get all tags."""
    conn = get_db()
    tags = conn.execute("""
        SELECT t.*, COUNT(gt.game_id) as game_count
        FROM tags t
        LEFT JOIN game_tags gt ON gt.tag_id = t.id
        GROUP BY t.id
        ORDER BY t.category, t.name
    """).fetchall()
    conn.close()
    return jsonify([dict(t) for t in tags])


@app.route('/api/platforms')
def api_platforms():
    """Get all platforms."""
    conn = get_db()
    platforms = conn.execute("""
        SELECT p.*, COUNT(gp.game_id) as game_count
        FROM platforms p
        LEFT JOIN game_platforms gp ON gp.platform_id = p.id
        GROUP BY p.id
        ORDER BY game_count DESC
    """).fetchall()
    conn.close()
    return jsonify([dict(p) for p in platforms])


@app.route('/api/stats')
def api_stats():
    """Get library statistics."""
    conn = get_db()

    stats = {}

    # Total games
    stats['total_games'] = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]

    # By status
    status_counts = conn.execute("""
        SELECT status, COUNT(*) as count
        FROM user_ratings
        GROUP BY status
    """).fetchall()
    stats['by_status'] = {row['status']: row['count'] for row in status_counts}

    # By platform
    platform_counts = conn.execute("""
        SELECT p.short_name, COUNT(gp.game_id) as count
        FROM platforms p
        LEFT JOIN game_platforms gp ON gp.platform_id = p.id
        GROUP BY p.id
    """).fetchall()
    stats['by_platform'] = {row['short_name']: row['count'] for row in platform_counts}

    # Average ratings
    stats['avg_user_rating'] = conn.execute(
        "SELECT AVG(rating) FROM user_ratings WHERE rating IS NOT NULL"
    ).fetchone()[0]

    stats['avg_critic_score'] = conn.execute("""
        SELECT AVG(COALESCE(metacritic_score, opencritic_score))
        FROM games
        WHERE metacritic_score IS NOT NULL OR opencritic_score IS NOT NULL
    """).fetchone()[0]

    # Games rated by user
    stats['games_rated'] = conn.execute(
        "SELECT COUNT(*) FROM user_ratings WHERE rating IS NOT NULL"
    ).fetchone()[0]

    conn.close()
    return jsonify(stats)


@app.route('/api/settings', methods=['GET'])
def api_get_settings():
    """Get current settings (credentials masked)."""
    config = load_config()
    # Mask secrets for display
    masked = {
        'twitch_client_id': config.get('twitch_client_id', ''),
        'twitch_client_secret': '••••••••' if config.get('twitch_client_secret') else '',
        'has_credentials': bool(config.get('twitch_client_id') and config.get('twitch_client_secret'))
    }
    return jsonify(masked)


@app.route('/api/settings', methods=['PUT'])
def api_update_settings():
    """Update settings."""
    data = request.json
    updates = {}

    if 'twitch_client_id' in data:
        updates['twitch_client_id'] = data['twitch_client_id'].strip()

    if 'twitch_client_secret' in data and data['twitch_client_secret'] != '••••••••':
        updates['twitch_client_secret'] = data['twitch_client_secret'].strip()

    if updates:
        save_config(updates)

    return jsonify({'success': True})


@app.route('/api/covers/status')
def api_covers_status():
    """Get cover art status."""
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    missing = conn.execute(
        "SELECT COUNT(*) FROM games WHERE cover_url IS NULL OR cover_url = ''"
    ).fetchone()[0]
    conn.close()

    client_id, client_secret = get_twitch_credentials()

    return jsonify({
        'total': total,
        'with_covers': total - missing,
        'missing': missing,
        'has_credentials': bool(client_id and client_secret)
    })


@app.route('/api/igdb/search')
def api_igdb_search():
    """Search IGDB for games matching a query."""
    from fetch_covers import get_access_token
    import requests

    query = request.args.get('q', '').strip()
    if not query or len(query) < 2:
        return jsonify([])

    client_id, client_secret = get_twitch_credentials()
    if not client_id or not client_secret:
        return jsonify({'error': 'Twitch API credentials not configured'}), 400

    try:
        access_token = get_access_token(client_id, client_secret)

        headers = {
            'Client-ID': client_id,
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'text/plain'
        }

        igdb_query = f'''
            search "{query}";
            fields name, cover.url;
            limit 8;
        '''

        response = requests.post(
            'https://api.igdb.com/v4/games',
            headers=headers,
            data=igdb_query
        )
        response.raise_for_status()
        results = response.json()

        # Format results
        games = []
        for game in results:
            cover_url = None
            if game.get('cover') and game['cover'].get('url'):
                cover_url = game['cover']['url'].replace('t_thumb', 't_cover_big')
                if not cover_url.startswith('http'):
                    cover_url = 'https:' + cover_url

            games.append({
                'name': game.get('name', ''),
                'cover_url': cover_url
            })

        return jsonify(games)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/covers/fetch', methods=['POST'])
def api_fetch_covers():
    """Start fetching missing cover art in background."""
    client_id, client_secret = get_twitch_credentials()

    if not client_id or not client_secret:
        return jsonify({'error': 'Twitch API credentials not configured'}), 400

    success, message = run_cover_fetch_background(client_id, client_secret)

    if success:
        return jsonify({'success': True, 'message': message})
    else:
        return jsonify({'error': message}), 409  # Conflict - already running


@app.route('/api/covers/fetch/status')
def api_fetch_covers_status():
    """Get current status of cover fetch background task."""
    return jsonify(get_cover_fetch_status())


# ============================================================================
# Startup
# ============================================================================

if __name__ == '__main__':
    # Initialize database if needed
    if not DB_PATH.exists():
        init_db()
    else:
        # Run migrations for existing databases
        migrate_db()

    app.run(debug=True, port=5000)
