"""
Game Tracker - Flask Application
"""
import difflib
import json
import logging
import os
import sqlite3
from collections import Counter
import requests
import dedup
import hltb
import import_scraped
import decider
import igdb_match
import slots
import barcode
from flask import Flask, render_template, request, jsonify
from models import (
    get_db, init_db, migrate_db, normalize_title, clean_title,
    reclean_display_titles, DB_PATH, add_series_pattern, apply_traits_catalog,
    apply_series_catalog,
)
from recommendation import get_recommendations, get_quick_picks
from config import load_config, save_config, get_twitch_credentials, DECIDER_MODELS
from background_tasks import run_cover_fetch_background, get_cover_fetch_status
import scrape_service

app = Flask(__name__)
log = logging.getLogger(__name__)


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
def series_overview_page():
    """Visual overview of all series (fanned-stack tiles)."""
    return render_template('series_overview.html')


@app.route('/series/manage')
@app.route('/series/<int:series_id>')
def series_page(series_id=None):
    """Series management/editor page."""
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
            g.collection_name,
            g.metacritic_score,
            g.opencritic_score,
            COALESCE(g.needs_igdb_review, 0) AS needs_igdb_review,
            g.igdb_review_reason,
            g.created_at,
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
        'manual': 'ur.sort_order',
        'newest': 'g.created_at'
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
        game['needs_igdb_review'] = bool(game.get('needs_igdb_review'))
        game['igdb_review_reason'] = game.get('igdb_review_reason')

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

    # Read the optional UPC for barcode cache persistence
    upc = (data.get('upc') or '').strip() or None

    # Check if game already exists
    existing = conn.execute(
        "SELECT id, igdb_id FROM games WHERE normalized_title = ?",
        (normalized,)
    ).fetchone()

    if existing:
        if upc:
            barcode.registry_put(conn, upc, igdb_id=existing['igdb_id'], title=title,
                              game_id=existing['id'])
            conn.commit()
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
    fmt = 'physical' if data.get('physical') else 'digital'
    for platform_short_name in platforms:
        platform = conn.execute(
            "SELECT id FROM platforms WHERE short_name = ?",
            (platform_short_name,)
        ).fetchone()
        if platform:
            conn.execute(
                "INSERT INTO game_platforms (game_id, platform_id, format) VALUES (?, ?, ?)",
                (game_id, platform['id'], fmt)
            )

    # Optional physical-copy flag, surfaced via the 'Physical' tag (see api_games).
    if data.get('physical'):
        conn.execute(
            "INSERT OR IGNORE INTO tags (name, category) VALUES ('Physical', 'custom')"
        )
        tag_id = conn.execute(
            "SELECT id FROM tags WHERE name = 'Physical'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO game_tags (game_id, tag_id) VALUES (?, ?)",
            (game_id, tag_id)
        )

    conn.commit()
    apply_traits_catalog(conn, game_id)
    apply_series_catalog(conn, game_id)

    # Best-effort IGDB enrichment so a manually-added game gets the same metadata a
    # scraped one does: igdb_id (needed to sync DLC), cover, and genre tags. Never
    # fails the create — a missing key / network error just leaves it bare.
    try:
        import igdb_dlc
        client_id, secret = get_twitch_credentials()
        if client_id:
            token = igdb_dlc.get_access_token(client_id, secret)
            igdb_dlc.enrich_game(conn, game_id, client_id, token)
            conn.commit()
    except Exception as exc:   # best-effort: enrichment must never block creation
        app.logger.warning("manual-add IGDB enrich failed for game %s: %s", game_id, exc)
        conn.rollback()

    if upc:
        platform_short = platforms[0] if platforms else None
        igdb_row = conn.execute(
            "SELECT igdb_id FROM games WHERE id = ?", (game_id,)
        ).fetchone()
        barcode.registry_put(conn, upc, igdb_id=igdb_row['igdb_id'] if igdb_row else None,
                          title=title, platform=platform_short, game_id=game_id)
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

    # Get platforms (with per-platform format + the platform's digital-market flag,
    # which drives the (Physical/Digital) qualifier in the editor and on mobile)
    platforms = conn.execute("""
        SELECT p.id, p.name, p.short_name, p.category,
               p.has_digital_market, gp.format
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

    # DLC list (folded into the game detail for the modal)
    dlc = conn.execute(
        "SELECT id, name, kind, owned, source FROM dlc WHERE game_id = ? ORDER BY kind, name",
        (game_id,)).fetchall()
    result['dlc'] = [{**dict(d), 'owned': bool(d['owned'])} for d in dlc]

    # External IDs (used by the 'Change cover' button)
    ext_rows = conn.execute(
        "SELECT source, external_id FROM game_external_ids WHERE game_id = ?",
        (game_id,)).fetchall()
    result["external_ids"] = {r["source"]: r["external_id"] for r in ext_rows}

    conn.close()
    return jsonify(result)


@app.route('/api/games/search')
def api_games_search():
    """Library typeahead: ?q=<term> -> up to 10 games with id, title, cover_url,
    platforms. Returns [] for queries shorter than 2 chars."""
    query = (request.args.get('q') or '').strip()
    if len(query) < 2:
        return jsonify([])
    conn = get_db()
    like = f"%{query}%"
    rows = conn.execute(
        "SELECT id, title, cover_url, collection_name FROM games "
        "WHERE title LIKE ? COLLATE NOCASE OR normalized_title LIKE ? COLLATE NOCASE "
        "ORDER BY title COLLATE NOCASE LIMIT 10",
        (like, like)).fetchall()
    game_ids = [r["id"] for r in rows]
    plat_by_game: dict[int, list[str]] = {gid: [] for gid in game_ids}
    if game_ids:
        placeholders = ",".join("?" * len(game_ids))
        for r in conn.execute(
            f"SELECT gp.game_id, p.short_name FROM game_platforms gp "
            f"JOIN platforms p ON p.id = gp.platform_id "
            f"WHERE gp.game_id IN ({placeholders})", game_ids):
            plat_by_game[r["game_id"]].append(r["short_name"])
    conn.close()
    return jsonify([
        {"id": r["id"], "title": r["title"], "cover_url": r["cover_url"],
         "collection_name": r["collection_name"],
         "platforms": plat_by_game.get(r["id"], [])}
        for r in rows
    ])


@app.route('/api/dlc/<int:dlc_id>/owned', methods=['POST'])
def api_set_dlc_owned(dlc_id):
    """Toggle ownership of a DLC entry."""
    data = request.get_json(silent=True) or {}
    owned = 1 if data.get('owned') else 0
    conn = get_db()
    cur = conn.execute("UPDATE dlc SET owned = ? WHERE id = ?", (owned, dlc_id))
    conn.commit()
    found = cur.rowcount > 0
    conn.close()
    if not found:
        return jsonify({'error': 'DLC not found'}), 404
    return jsonify({'ok': True, 'owned': bool(owned)})


@app.route('/api/games/<int:game_id>/dlc', methods=['POST'])
def api_add_dlc(game_id):
    """Add a manual DLC entry; returns the existing row if the name is a dup."""
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    kind = data.get('kind') or 'dlc'
    if not name:
        return jsonify({'error': 'name required'}), 400
    conn = get_db()
    if not conn.execute("SELECT 1 FROM games WHERE id = ?", (game_id,)).fetchone():
        conn.close()
        return jsonify({'error': 'Game not found'}), 404
    existing = conn.execute(
        "SELECT id, name, kind, owned, source FROM dlc WHERE game_id = ? AND name = ?",
        (game_id, name)).fetchone()
    if existing:
        conn.close()
        return jsonify({**dict(existing), 'owned': bool(existing['owned'])})
    cur = conn.execute(
        "INSERT INTO dlc (game_id, name, kind, source) VALUES (?, ?, ?, 'manual')",
        (game_id, name, kind))
    conn.commit()
    new = conn.execute(
        "SELECT id, name, kind, owned, source FROM dlc WHERE id = ?", (cur.lastrowid,)).fetchone()
    conn.close()
    return jsonify({**dict(new), 'owned': bool(new['owned'])}), 201


@app.route('/api/dlc/<int:dlc_id>', methods=['DELETE'])
def api_delete_dlc(dlc_id):
    """Delete a DLC entry (manual or IGDB-sourced)."""
    conn = get_db()
    cur = conn.execute("DELETE FROM dlc WHERE id = ?", (dlc_id,))
    conn.commit()
    found = cur.rowcount > 0
    conn.close()
    if not found:
        return jsonify({'error': 'DLC not found'}), 404
    return jsonify({'ok': True})


@app.route('/api/dlc/review/count')
def api_dlc_review_count():
    """Open-queue size (resolved/dismissed excluded). Cheap; drives the badge."""
    conn = get_db()
    n = conn.execute(
        "SELECT COUNT(*) FROM dlc_review_queue "
        "WHERE resolved_at IS NULL AND dismissed_at IS NULL"
    ).fetchone()[0]
    conn.close()
    return jsonify({'count': n})


@app.route('/api/dlc/review')
def api_dlc_review_list():
    """Open review items + inlined candidate parents/DLCs (re-derived against
    the current library, so candidates reflect any merges/renames since the
    scrape)."""
    import dlc_ownership
    conn = get_db()
    items = conn.execute(
        "SELECT id, addon_title, source, external_id, source_title, reason, game_id "
        "FROM dlc_review_queue "
        "WHERE resolved_at IS NULL AND dismissed_at IS NULL "
        "ORDER BY created_at, id"
    ).fetchall()
    library = [(r["id"], r["normalized_title"])
               for r in conn.execute("SELECT id, normalized_title FROM games")]
    out = []
    for it in items:
        candidates = {"games": [], "dlc": []}
        if it["reason"] == "ambiguous parent":
            # Re-derive: longest equal-length prefix winners (mirrors parent_of).
            addon = dlc_ownership.norm(it["addon_title"])
            best_len = 0
            winners: list[int] = []
            for gid, gnorm in library:
                if not gnorm:
                    continue
                if addon == gnorm or addon.startswith(gnorm + " "):
                    if len(gnorm) > best_len:
                        best_len, winners = len(gnorm), [gid]
                    elif len(gnorm) == best_len:
                        winners.append(gid)
            if winners:
                placeholders = ",".join("?" * len(winners))
                game_rows = conn.execute(
                    f"SELECT id, title, cover_url FROM games WHERE id IN ({placeholders})",
                    winners).fetchall()
                plat_by_game: dict[int, list[str]] = {gid: [] for gid in winners}
                for r in conn.execute(
                    f"SELECT gp.game_id, p.short_name FROM game_platforms gp "
                    f"JOIN platforms p ON p.id = gp.platform_id "
                    f"WHERE gp.game_id IN ({placeholders})", winners):
                    plat_by_game[r["game_id"]].append(r["short_name"])
                candidates["games"] = [
                    {"id": r["id"], "title": r["title"], "cover_url": r["cover_url"],
                     "platforms": plat_by_game.get(r["id"], [])}
                    for r in game_rows
                ]
        elif it["reason"] == "ambiguous dlc" and it["game_id"]:
            parent = it["game_id"]
            parent_norm = next(
                (g for gid, g in library if gid == parent), "") or ""
            rows = [(r["id"], r["name"])
                    for r in conn.execute(
                        "SELECT id, name FROM dlc WHERE game_id = ?", (parent,))]
            rem = dlc_ownership.remainder(it["addon_title"], parent_norm)
            equal = [r for r in rows if dlc_ownership.norm(r[1]) == rem]
            candidates["dlc"] = [{"id": dlc_id, "name": name} for dlc_id, name in equal]
        out.append({
            "id": it["id"],
            "addon_title": it["addon_title"],
            "source": it["source"],
            "external_id": it["external_id"],
            "source_title": it["source_title"],
            "reason": it["reason"],
            "game_id": it["game_id"],
            "candidates": candidates,
        })
    conn.close()
    return jsonify({"items": out, "count": len(out)})


@app.route('/api/dlc/review/<int:review_id>/resolve', methods=['POST'])
def api_dlc_review_resolve(review_id):
    """Apply a user-picked decision to a queued review item."""
    import dlc_review
    data = request.get_json(silent=True) or {}
    picked_game_id = data.get("game_id")
    picked_dlc_id = data.get("dlc_id")
    create_new_dlc = bool(data.get("create_new_dlc"))
    chosen = (picked_game_id is not None) + (picked_dlc_id is not None) + (1 if create_new_dlc else 0)
    if chosen != 1:
        return jsonify({"error": "Pick exactly one of game_id, dlc_id, or create_new_dlc"}), 400
    conn = get_db()
    try:
        match = dlc_review.resolve(
            conn, review_id,
            picked_game_id=picked_game_id,
            picked_dlc_id=picked_dlc_id,
            create_new_dlc=create_new_dlc,
        )
    except sqlite3.IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({"error": "A DLC row with that name already exists for this game"}), 409
    except ValueError as exc:
        conn.rollback()
        conn.close()
        msg = str(exc)
        # "not found" cases → 404; other ValueErrors → 400.
        status = 404 if "not found" in msg else 400
        return jsonify({"error": msg}), status
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM dlc_review_queue "
        "WHERE resolved_at IS NULL AND dismissed_at IS NULL"
    ).fetchone()[0]
    conn.close()
    return jsonify({"ok": True, "marked": match.reason in ("created", "reconciled"),
                    "count": count})


@app.route('/api/dlc/review/<int:review_id>/dismiss', methods=['POST'])
def api_dlc_review_dismiss(review_id):
    """Mark a review item dismissed (not a real add-on)."""
    import dlc_review
    conn = get_db()
    try:
        dlc_review.dismiss(conn, review_id)
    except ValueError as exc:
        conn.close()
        return jsonify({"error": str(exc)}), 404
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM dlc_review_queue "
        "WHERE resolved_at IS NULL AND dismissed_at IS NULL"
    ).fetchone()[0]
    conn.close()
    return jsonify({"ok": True, "count": count})


@app.route('/api/dlc/review/rematch', methods=['POST'])
def api_dlc_review_rematch():
    """Re-run the matcher over open review rows; resolve+own newly-matched ones."""
    import dlc_review
    conn = get_db()
    try:
        report = dlc_review.rematch_unresolved(conn)
    except sqlite3.IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({"error": "A DLC row with that name already exists for this game"}), 409
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM dlc_review_queue "
        "WHERE resolved_at IS NULL AND dismissed_at IS NULL"
    ).fetchone()[0]
    conn.close()
    return jsonify({"ok": True, "resolved": report.resolved,
                    "marked": report.marked, "count": count})


@app.route('/api/games/<int:game_id>/dlc/refresh', methods=['POST'])
def api_refresh_dlc(game_id):
    """Re-fetch a game's DLC from IGDB (by stored id, or by title if unset)."""
    import config
    import igdb_dlc
    conn = get_db()
    if not conn.execute("SELECT 1 FROM games WHERE id = ?", (game_id,)).fetchone():
        conn.close()
        return jsonify({'error': 'Game not found'}), 404
    client_id, secret = config.get_twitch_credentials()
    if not client_id:
        conn.close()
        return jsonify({'error': 'IGDB credentials not configured'}), 400
    try:
        token = igdb_dlc.get_access_token(client_id, secret)
        report = igdb_dlc.enrich_game(conn, game_id, client_id, token)
        conn.commit()
    except requests.RequestException as exc:
        conn.rollback()
        conn.close()
        return jsonify({'error': f'IGDB request failed: {exc}'}), 502
    dlc = conn.execute(
        "SELECT id, name, kind, owned, source FROM dlc WHERE game_id = ? ORDER BY kind, name",
        (game_id,)).fetchall()
    conn.close()
    return jsonify({'dlc': [{**dict(d), 'owned': bool(d['owned'])} for d in dlc],
                    'report': report})


@app.route('/api/games/<int:game_id>/decider-chat', methods=['GET', 'POST'])
def api_game_decider_chat(game_id: int):
    """Save (POST) or list (GET) decider conversations tied to a game."""
    conn = get_db()
    try:
        if request.method == 'POST':
            data = request.json or {}
            cid = decider.save_chat(conn, game_id, data.get('slot_id'),
                                    data.get('slot_label'), data.get('messages') or [])
            conn.commit()
            return jsonify({'success': True, 'id': cid}), 201
        return jsonify({'chats': decider.list_chats(conn, game_id)})
    finally:
        conn.close()


@app.route('/api/games/<int:game_id>/dlc/refresh-psn', methods=['POST'])
def api_refresh_psn_dlc(game_id: int):
    """Clear the PSN add-on marker for one game and kick a scrape to re-check it."""
    conn = get_db()
    row = conn.execute(
        "SELECT external_id FROM game_external_ids "
        "WHERE game_id = ? AND source = 'playstation'", (game_id,)).fetchone()
    if row is None:
        conn.close()
        return jsonify({"error": "no PlayStation id for this game"}), 404
    conn.execute("UPDATE games SET psn_addons_synced_at = NULL WHERE id = ?", (game_id,))
    conn.commit()
    conn.close()
    ok, msg = scrape_service.start("playstation")
    return jsonify({"started": ok, "message": msg}), (200 if ok else 409)


@app.route('/api/games/<int:game_id>/igdb', methods=['POST'])
def api_pin_igdb(game_id):
    """Pin a game's IGDB identity from an igdb.com/games/<slug> URL: sets igdb_id,
    refreshes the cover, and re-fetches DLC."""
    import config
    import igdb_dlc
    data = request.get_json(silent=True) or {}
    slug = igdb_dlc.slug_from_igdb_url((data.get('url') or '').strip())
    if not slug:
        return jsonify({'error': 'Not an IGDB game URL'}), 400
    conn = get_db()
    if not conn.execute("SELECT 1 FROM games WHERE id = ?", (game_id,)).fetchone():
        conn.close()
        return jsonify({'error': 'Game not found'}), 404
    client_id, secret = config.get_twitch_credentials()
    if not client_id:
        conn.close()
        return jsonify({'error': 'IGDB credentials not configured'}), 400
    try:
        token = igdb_dlc.get_access_token(client_id, secret)
        report = igdb_dlc.enrich_game(conn, game_id, client_id, token, slug=slug)
        conn.commit()
    except requests.RequestException as exc:
        conn.rollback()
        conn.close()
        return jsonify({'error': f'IGDB request failed: {exc}'}), 502
    if not report['matched']:
        conn.close()
        return jsonify({'error': 'No IGDB game found for that URL'}), 404
    conn.execute("UPDATE games SET igdb_locked = 1, needs_igdb_review = 0, "
                 "igdb_review_reason = NULL WHERE id = ?", (game_id,))
    conn.commit()
    game = conn.execute(
        "SELECT id, title, cover_url, igdb_id FROM games WHERE id = ?", (game_id,)).fetchone()
    dlc = conn.execute(
        "SELECT id, name, kind, owned, source FROM dlc WHERE game_id = ? ORDER BY kind, name",
        (game_id,)).fetchall()
    conn.close()
    return jsonify({'game': dict(game),
                    'dlc': [{**dict(d), 'owned': bool(d['owned'])} for d in dlc],
                    'report': report})


@app.route('/api/games/<int:game_id>/igdb-candidates', methods=['GET'])
def api_igdb_candidates(game_id):
    """Shaped IGDB identity candidates for the Fix-match modal, plus the game's
    current cover (bundle-first, junk/duplicate-filtered)."""
    import config
    import igdb_dlc
    import igdb_match
    conn = get_db()
    row = conn.execute(
        "SELECT title, cover_url, collection_name, igdb_id FROM games WHERE id = ?", (game_id,)).fetchone()
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
    token = igdb_dlc.get_access_token(client_id, secret)
    cands = igdb_match.candidates_for(
        row['title'], igdb_match.platform_ids_for(plat_short),
        row['collection_name'], client_id, token)
    shaped = igdb_match.modal_candidates(cands, row['title'], current_cover=row['cover_url'])
    for c in shaped:
        c['platforms_label'] = ' · '.join(igdb_match.platform_labels(c.get('platforms') or []))
    stored = igdb_match.fetch_entry(row['igdb_id'], client_id, token) if row['igdb_id'] else None
    current_label = ' · '.join(igdb_match.platform_labels((stored or {}).get('platforms') or []))
    return jsonify({'candidates': shaped,
                    'current': {'cover_url': row['cover_url'], 'title': row['title'],
                                'platforms_label': current_label}})


@app.route('/api/games/<int:game_id>/igdb-pick', methods=['POST'])
def api_igdb_pick(game_id):
    """Apply a chosen IGDB identity: set igdb_id + cover_url, lock, clear review."""
    data = request.get_json(silent=True) or {}
    igdb_id = data.get('igdb_id')
    if igdb_id is None:
        return jsonify({'error': 'igdb_id is required'}), 400
    cover_url = (data.get('cover_url') or '').strip() or None
    conn = get_db()
    if not conn.execute("SELECT 1 FROM games WHERE id = ?", (game_id,)).fetchone():
        conn.close()
        return jsonify({'error': 'Game not found'}), 404
    conn.execute(
        "UPDATE games SET igdb_id = ?, cover_url = COALESCE(?, cover_url), "
        "igdb_locked = 1, needs_igdb_review = 0, igdb_review_reason = NULL, "
        "updated_at = CURRENT_TIMESTAMP WHERE id = ?", (igdb_id, cover_url, game_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/games/<int:game_id>/igdb-keep', methods=['POST'])
def api_igdb_keep(game_id):
    """Keep the current IGDB match as-is: lock it and clear the review flag without
    changing igdb_id or cover_url (the 'this one is fine' action)."""
    conn = get_db()
    if not conn.execute("SELECT 1 FROM games WHERE id = ?", (game_id,)).fetchone():
        conn.close()
        return jsonify({'error': 'Game not found'}), 404
    conn.execute(
        "UPDATE games SET igdb_locked = 1, needs_igdb_review = 0, "
        "igdb_review_reason = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (game_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/games/<int:game_id>/steam', methods=['POST'])
def api_pin_steam(game_id):
    """Pin a game's Steam identity from a store.steampowered.com/app/<appid> URL.

    Writes game_external_ids(source='steam', external_id=str(appid), source_title=<title>).
    Does NOT run DLC enrichment (Steam's per-DLC appdetails is rate-limited at
    200/5min; DLC defers to the next Steam scrape per SP3 decision).
    """
    import steam_dlc
    data    = request.get_json(silent=True) or {}
    appid   = steam_dlc.appid_from_steam_url((data.get('url') or '').strip())
    if not appid:
        return jsonify({'error': 'Not a Steam store URL'}), 400
    conn     = get_db()
    game_row = conn.execute(
        "SELECT id, title, cover_url, igdb_id FROM games WHERE id = ?", (game_id,)).fetchone()
    if not game_row:
        conn.close()
        return jsonify({'error': 'Game not found'}), 404
    conn.execute(
        "INSERT OR IGNORE INTO game_external_ids "
        "    (game_id, source, external_id, source_title) "
        "VALUES (?, 'steam', ?, ?)",
        (game_id, str(appid), game_row['title']))
    conn.commit()
    game = dict(game_row)
    conn.close()
    return jsonify({'appid': appid, 'game': game})


TRAIT_ENUMS = {
    "session_length": {"short", "long"},
    "series_role": {"mainline", "spinoff"},
}


@app.route('/api/games/<int:game_id>', methods=['PUT'])
def api_update_game(game_id):
    """Update game rating, status, priority, title, cover_url, etc."""
    conn = get_db()
    data = request.json

    try:
        # Update game table fields (title, cover_url, time_to_beat_override_minutes, traits)
        if ('title' in data or 'cover_url' in data or 'time_to_beat_override_minutes' in data
                or 'session_length' in data or 'series_role' in data
                or 'input_lag_override' in data):
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
            if 'time_to_beat_override_minutes' in data:
                game_updates.append("time_to_beat_override_minutes = ?")
                v = data['time_to_beat_override_minutes']
                game_params.append(int(v) if v not in (None, "") else None)
            if 'input_lag_override' in data:
                # streamable override: NULL = auto (derive from genres), 1 = plays
                # fine streamed, 0 = lag-sensitive (exclude from streamed slots).
                v = data['input_lag_override']
                if v in (None, ""):
                    game_updates.append("input_lag_override = NULL")
                else:
                    game_updates.append("input_lag_override = ?")
                    game_params.append(1 if int(v) else 0)
            for trait in ('session_length', 'series_role'):
                if trait in data:
                    v = data[trait]
                    if v in (None, ""):
                        game_updates.append(f"{trait} = NULL")
                        game_updates.append(f"{trait}_source = NULL")
                    elif v in TRAIT_ENUMS[trait]:
                        game_updates.append(f"{trait} = ?")
                        game_params.append(v)
                        game_updates.append(f"{trait}_source = 'manual'")
                    # invalid enum value: ignored
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

            # A game moved to a finished status (completed/100/dropped) is no
            # longer a candidate, so drop it from any Pick slot it occupies —
            # matching the slate's own Complete/100%/Dropped buttons.
            if data.get('status') in slots.FINISHED_STATUSES:
                slots.free_slots_for_game(conn, game_id)

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

        # Update platforms if provided (full replace). Preserve each platform's
        # existing `format` across the delete+reinsert so editing membership never
        # wipes the per-platform physical/digital values set elsewhere. The set of
        # platforms inserted is unchanged from before — only `format` is carried.
        if 'platforms' in data:
            existing_fmt = {
                r['short_name']: r['format']
                for r in conn.execute(
                    "SELECT p.short_name, gp.format FROM game_platforms gp "
                    "JOIN platforms p ON p.id = gp.platform_id WHERE gp.game_id = ?",
                    (game_id,)).fetchall()
            }
            conn.execute("DELETE FROM game_platforms WHERE game_id = ?", (game_id,))
            for platform_short_name in data['platforms']:
                platform = conn.execute(
                    "SELECT id FROM platforms WHERE short_name = ?",
                    (platform_short_name,)
                ).fetchone()
                if platform:
                    conn.execute(
                        "INSERT OR IGNORE INTO game_platforms (game_id, platform_id, format) "
                        "VALUES (?, ?, ?)",
                        (game_id, platform['id'], existing_fmt.get(platform_short_name))
                    )

        # Single-platform add (mobile scan "I bought the <platform> copy"). Appends
        # one platform with its format + records the UPC, without touching the
        # existing full-`platforms` replace path above.
        add = data.get('add_platform')
        if add and add.get('short_name'):
            prow = conn.execute(
                "SELECT id FROM platforms WHERE short_name = ?", (add['short_name'],)
            ).fetchone()
            if prow:
                conn.execute(
                    "INSERT OR IGNORE INTO game_platforms (game_id, platform_id, format) "
                    "VALUES (?, ?, ?)", (game_id, prow['id'], add.get('format')))
                existing = conn.execute(
                    "SELECT format FROM game_platforms WHERE game_id = ? AND platform_id = ?",
                    (game_id, prow['id'])).fetchone()
                new_fmt = add.get('format')
                cur_fmt = existing['format']
                # 'both' is sticky (already own physical+digital); owning a different
                # single format than before upgrades to 'both'; otherwise take the new
                # format (or keep the current when no new one was given).
                if cur_fmt == 'both' or (cur_fmt and new_fmt and cur_fmt != new_fmt):
                    final_fmt = 'both'
                else:
                    final_fmt = new_fmt or cur_fmt
                conn.execute(
                    "UPDATE game_platforms SET format = ? WHERE game_id = ? AND platform_id = ?",
                    (final_fmt, game_id, prow['id']))
            if add.get('upc'):
                barcode.registry_put(conn, add['upc'], title=None,
                                     platform=add['short_name'], game_id=game_id)

        # Per-platform format setter (web format editor): set physical/digital for
        # already-owned platforms without touching membership. Unknown platforms and
        # invalid format values are ignored.
        fmts = data.get('platform_formats')
        if isinstance(fmts, dict):
            for short_name, fmt in fmts.items():
                if fmt not in ('physical', 'digital'):
                    continue
                prow = conn.execute(
                    "SELECT id FROM platforms WHERE short_name = ?", (short_name,)
                ).fetchone()
                if prow:
                    conn.execute(
                        "UPDATE game_platforms SET format = ? "
                        "WHERE game_id = ? AND platform_id = ?",
                        (fmt, game_id, prow['id']))

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


@app.route('/api/games/<int:game_id>/not-a-game', methods=['POST'])
def api_not_a_game(game_id):
    """Mark a game 'not a game': record a durable exclusion, then delete the row.

    Records every (source, external_id) the game carries plus its normalized title
    to the per-user excluded_games.json, so a future re-import skips it forever.
    """
    conn = get_db()
    try:
        g = conn.execute("SELECT id, title FROM games WHERE id = ?", (game_id,)).fetchone()
        if not g:
            return jsonify({'error': 'Game not found'}), 404
        normalized = import_scraped.match_key(g['title'])
        ext_rows = conn.execute(
            "SELECT source, external_id FROM game_external_ids WHERE game_id = ?",
            (game_id,)).fetchall()
        if ext_rows:
            entries = [{'source': r['source'], 'external_id': r['external_id'],
                        'normalized_title': normalized, 'title': g['title']} for r in ext_rows]
        else:
            entries = [{'source': None, 'external_id': None,
                        'normalized_title': normalized, 'title': g['title']}]
        import_scraped.add_excluded_games(entries)
        conn.execute("DELETE FROM games WHERE id = ?", (game_id,))
        conn.commit()
        return jsonify({'success': True, 'excluded': len(entries)})
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
    titles_by_id = {game["id"]: game["title"] for game in games}
    grouped = dedup.group_candidates(groups["candidates"])
    for g in grouped:
        placeholders = ",".join("?" * len(g["members"]))
        rows = conn.execute(
            f"SELECT s.id, s.name FROM user_ratings ur JOIN series s ON s.id = ur.series_id "
            f"WHERE ur.game_id IN ({placeholders})", g["members"]).fetchall()
        if rows:
            (sid, sname), _ = Counter((r["id"], r["name"]) for r in rows).most_common(1)[0]
            g["existing_series_id"], g["existing_series_name"] = sid, sname
        else:
            g["existing_series_id"], g["existing_series_name"] = None, None
        g["inferred_name"] = dedup.infer_series_name(
            [titles_by_id[m] for m in g["members"] if m in titles_by_id])
    conn.close()
    return jsonify({"definite": groups["definite"],
                    "candidates": groups["candidates"],
                    "groups": grouped, "games": games})


@app.route('/api/games/merge', methods=['POST'])
def api_merge_games():
    """Merge drop games into a survivor (dedup)."""
    data = request.json or {}
    survivor_id = data.get('survivor_id')
    drop_ids = data.get('drop_ids') or []
    if not survivor_id or not drop_ids:
        return jsonify({'error': 'survivor_id and drop_ids are required'}), 400

    conn = get_db()
    try:
        result = dedup.merge_games(
            conn, survivor_id, drop_ids,
            title=data.get('title'), curation=data.get('curation'))
        dedup.refresh_normalized_titles(conn)
        return jsonify({'success': True, 'survivor_id': result['survivor_id']})
    except sqlite3.IntegrityError as e:
        return jsonify({'error': str(e)}), 400
    finally:
        conn.close()


@app.route('/api/duplicates/dismiss', methods=['POST'])
def api_dismiss_duplicate():
    """Record pair(s) as confirmed-distinct so dedup never re-asks.

    Accepts a single pair {game_id_a, game_id_b} or bulk {pairs: [[a, b], ...]}
    (used by Mark-all-safe and Remove-from-group). Unknown ids fail the FK and
    are rejected with 400.
    """
    data = request.json or {}
    raw_pairs = data['pairs'] if data.get('pairs') \
        else [[data.get('game_id_a'), data.get('game_id_b')]]

    pairs = []
    for p in raw_pairs:
        if not p or len(p) != 2 or not p[0] or not p[1] or p[0] == p[1]:
            return jsonify({'error': 'each pair needs two distinct game ids'}), 400
        pairs.append((min(p[0], p[1]), max(p[0], p[1])))

    conn = get_db()
    try:
        conn.executemany(
            "INSERT OR IGNORE INTO not_duplicates (game_id_lo, game_id_hi) VALUES (?, ?)",
            pairs)
        conn.commit()
        return jsonify({'success': True, 'count': len(pairs)})
    except sqlite3.IntegrityError as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        conn.close()


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


@app.route('/api/series/from-group', methods=['POST'])
def api_series_from_group():
    """Create-or-find a series by name and assign the given games to it (in order).

    {name, game_ids, remember} -> {success, series_id, created, assigned}. When
    `remember`, the name is added to the durable per-user series-pattern table.
    """
    data = request.json or {}
    name = (data.get('name') or '').strip()
    game_ids = data.get('game_ids') or []
    if not name or not game_ids:
        return jsonify({'error': 'name and game_ids are required'}), 400

    conn = get_db()
    try:
        # Intentional: case-insensitive find-or-create is the contract here (no 409
        # like api_create_series) — the dedup modal wants idempotent assignment.
        row = conn.execute("SELECT id FROM series WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
        if row:
            series_id, created = row['id'], False
        else:
            conn.execute("INSERT INTO series (name) VALUES (?)", (name,))
            series_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            created = True

        order = conn.execute(
            "SELECT MAX(series_order) FROM user_ratings WHERE series_id = ?", (series_id,)
        ).fetchone()[0] or 0
        assigned = 0
        for gid in game_ids:
            cur = conn.execute("SELECT series_id FROM user_ratings WHERE game_id = ?", (gid,)).fetchone()
            if cur and cur['series_id'] == series_id:
                continue
            order += 1
            conn.execute(
                "INSERT INTO user_ratings (game_id, series_id, series_order, series_source) "
                "VALUES (?, ?, ?, 'manual') "
                "ON CONFLICT(game_id) DO UPDATE SET series_id = excluded.series_id, "
                "series_order = excluded.series_order, series_source = 'manual', "
                "updated_at = CURRENT_TIMESTAMP",
                (gid, series_id, order))
            assigned += 1
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        conn.close()

    if data.get('remember'):
        add_series_pattern(name, name)

    return jsonify({'success': True, 'series_id': series_id,
                    'created': created, 'assigned': assigned})


@app.route('/api/series/<int:series_id>', methods=['DELETE'])
def api_delete_series(series_id):
    """Delete a series (games are kept, just unlinked)."""
    conn = get_db()

    # Unlink games from this series
    conn.execute("UPDATE user_ratings SET series_id = NULL, series_order = NULL, "
                 "series_source = NULL WHERE series_id = ?", (series_id,))
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


# Below this similarity to the query, an IGDB result isn't a confident match.
_IGDB_NAME_MATCH_THRESHOLD = 0.6


def _is_word_prefix(prefix: str, text: str) -> bool:
    """True if `prefix` spans whole leading words of `text` (case-insensitive)."""
    p, t = prefix.lower(), text.lower()
    return t == p or (t.startswith(p) and not t[len(p)].isalnum())


def pick_igdb_series_name(query: str, results: list[dict]) -> str | None:
    """Best canonical franchise/collection name from IGDB results for `query`.

    Prefers an exact (case-insensitive) match, then a result that is a word-prefix
    of the query (the franchise the series belongs to — longest wins), then the
    highest-similarity name at or above the confidence threshold, else None. Pure.
    """
    q = (query or "").strip().lower()
    names = [r["name"] for r in results if isinstance(r, dict) and r.get("name")]
    if not q or not names:
        return None
    for name in names:
        if name.lower() == q:
            return name
    prefixes = [name for name in names if _is_word_prefix(name, q)]
    if prefixes:
        return max(prefixes, key=len)
    best, best_score = None, 0.0
    for name in names:
        score = difflib.SequenceMatcher(None, q, name.lower()).ratio()
        if score > best_score:
            best, best_score = name, score
    return best if best_score >= _IGDB_NAME_MATCH_THRESHOLD else None


@app.route('/api/series/igdb-suggest')
def api_series_igdb_suggest():
    """Suggest the canonical franchise/collection name for a series name via IGDB.

    GET ?name=... -> {"suggestion": <name or null>}. Best-effort: returns
    {"suggestion": null} when IGDB is unconfigured or nothing matches.
    """
    from fetch_covers import get_access_token
    import requests

    name = (request.args.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name is required'}), 400

    client_id, client_secret = get_twitch_credentials()
    if not client_id or not client_secret:
        return jsonify({'suggestion': None})

    try:
        access_token = get_access_token(client_id, client_secret)
        headers = {'Client-ID': client_id, 'Authorization': f'Bearer {access_token}',
                   'Content-Type': 'text/plain'}
        query = f'search "{name.replace(chr(34), "")}"; fields name; limit 5;'
        results = []
        for endpoint in ('franchises', 'collections'):
            resp = requests.post(f'https://api.igdb.com/v4/{endpoint}',
                                 headers=headers, data=query, timeout=10)
            if resp.ok:
                results.extend(resp.json())
        return jsonify({'suggestion': pick_igdb_series_name(name, results)})
    except requests.RequestException as e:
        log.warning("igdb-suggest failed for %r: %s", name, e)
        return jsonify({'suggestion': None})


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
        INSERT INTO user_ratings (game_id, series_id, series_order, series_source)
        VALUES (?, ?, ?, 'manual')
        ON CONFLICT(game_id) DO UPDATE SET
            series_id = excluded.series_id,
            series_order = excluded.series_order,
            series_source = 'manual',
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
        SET series_id = NULL, series_order = NULL, series_source = NULL, updated_at = CURRENT_TIMESTAMP
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


@app.route('/api/slots')
def api_slots():
    """Full slate state: slot definitions + current games + ranked candidates."""
    conn = get_db()
    state = slots.get_slots_state(conn)
    recent = slots.recently_finished(conn)
    conn.close()
    return jsonify({'slots': state, 'recently_finished': recent})


@app.route('/api/slots', methods=['POST'])
def api_create_slot():
    """Create a new slot."""
    data = request.get_json() or {}
    conn = get_db()
    next_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM slots").fetchone()[0]
    conn.execute(
        "INSERT INTO slots (label, sort_order, platforms, max_session_minutes, "
        "min_session_minutes, streamable_only, prioritize_started, context_notes, "
        "focus_series_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (data.get('label', 'New slot'), next_order,
         json.dumps(data.get('platforms', [])),
         data.get('max_session_minutes'), data.get('min_session_minutes'),
         1 if data.get('streamable_only') else 0,
         1 if data.get('prioritize_started', 1) else 0,
         data.get('context_notes'), data.get('focus_series_id')))
    conn.commit()
    conn.close()
    return jsonify({'ok': True}), 201


@app.route('/api/slots/<int:slot_id>', methods=['PATCH'])
def api_update_slot(slot_id: int):
    """Update a slot's definition (label / constraints / notes)."""
    data = request.get_json() or {}
    fields, params = [], []
    for key in ('label', 'max_session_minutes', 'min_session_minutes', 'context_notes',
                'sort_order', 'focus_series_id'):
        if key in data:
            fields.append(f"{key} = ?")
            params.append(data[key])
    if 'platforms' in data:
        fields.append("platforms = ?")
        params.append(json.dumps(data['platforms']))
    if 'streamable_only' in data:
        fields.append("streamable_only = ?")
        params.append(1 if data['streamable_only'] else 0)
    if 'prioritize_started' in data:
        fields.append("prioritize_started = ?")
        params.append(1 if data['prioritize_started'] else 0)
    if 'completionist' in data:
        fields.append("completionist = ?")
        params.append(1 if data['completionist'] else 0)
    if not fields:
        return jsonify({'error': 'no fields'}), 400
    params.append(slot_id)
    conn = get_db()
    conn.execute(f"UPDATE slots SET {', '.join(fields)} WHERE id = ?", params)
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@app.route('/api/slots/reorder', methods=['POST'])
def api_reorder_slots():
    """Persist slot grid order from drag-and-drop. Body: {slot_ids: [...]}."""
    data = request.get_json() or {}
    slot_ids = data.get('slot_ids') or []
    if not slot_ids:
        return jsonify({'error': 'slot_ids required'}), 400
    conn = get_db()
    slots.reorder(conn, slot_ids)
    conn.commit()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/slots/<int:slot_id>', methods=['DELETE'])
def api_delete_slot(slot_id: int):
    """Delete a slot definition."""
    conn = get_db()
    conn.execute("DELETE FROM slots WHERE id = ?", (slot_id,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@app.route('/api/slots/<int:slot_id>/pin', methods=['POST'])
def api_pin_slot(slot_id: int):
    """Assign a game (+ goal) to a slot."""
    data = request.get_json() or {}
    game_id = data.get('game_id')
    if not game_id:
        return jsonify({'error': 'game_id required'}), 400
    conn = get_db()
    slots.pin_game(conn, slot_id, game_id, data.get('goal'))
    conn.close()
    return jsonify({'ok': True})


@app.route('/api/slots/<int:slot_id>/outcome', methods=['POST'])
def api_slot_outcome(slot_id: int):
    """Apply an outcome: beat (+chase/new_goal) / complete / dropped / swap."""
    data = request.get_json() or {}
    outcome = data.get('outcome')
    if outcome not in ('beat', 'complete', 'dropped', 'swap'):
        return jsonify({'error': 'invalid outcome'}), 400
    conn = get_db()
    slots.apply_outcome(conn, slot_id, outcome,
                        chase=bool(data.get('chase')), new_goal=data.get('new_goal'))
    conn.close()
    return jsonify({'ok': True})


@app.route('/api/slots/<int:slot_id>/goal', methods=['PATCH'])
def api_slot_goal(slot_id: int):
    """Edit the plaintext goal for a slot's current game."""
    data = request.get_json() or {}
    conn = get_db()
    conn.execute("UPDATE slots SET goal = ? WHERE id = ?", (data.get('goal'), slot_id))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@app.route('/api/slots/<int:slot_id>/dismiss', methods=['POST'])
def api_slot_dismiss(slot_id: int):
    """Hide a suggested game from this slot until its game is replaced."""
    data = request.get_json() or {}
    game_id = data.get('game_id')
    if not game_id:
        return jsonify({'error': 'game_id required'}), 400
    conn = get_db()
    slots.dismiss_suggestion(conn, slot_id, game_id)
    conn.close()
    return jsonify({'ok': True})


@app.route('/api/slots/<int:slot_id>/chat', methods=['POST'])
def api_slot_chat(slot_id: int):
    """One blocking decider turn for a slot. Body: {messages:[...]}. Returns
    {reply, suggestions:[game,...]} or a 400 {error} when no API key is configured."""
    data = request.get_json() or {}
    messages = data.get('messages') or []
    conn = get_db()
    row = conn.execute("SELECT * FROM slots WHERE id = ?", (slot_id,)).fetchone()
    if row is None:
        conn.close()
        return jsonify({'error': 'slot not found'}), 404
    result = decider.decide(conn, dict(row), messages)
    if 'error' in result:
        conn.close()
        return jsonify({'error': result['error']}), 400
    games = []
    for gid in result['suggestions']:
        g = conn.execute("SELECT * FROM games WHERE id = ?", (gid,)).fetchone()
        if g:
            games.append(dict(g))
    conn.close()
    return jsonify({'reply': result['reply'], 'suggestions': games})


@app.route('/api/hltb/refresh', methods=['POST'])
def api_hltb_refresh():
    """Batch-enrich games lacking HLTB durations."""
    conn = get_db()
    result = hltb.enrich_missing(conn)
    conn.close()
    return jsonify(result)


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

    # DLC ownership counts
    stats['dlc_total'] = conn.execute("SELECT COUNT(*) FROM dlc").fetchone()[0]
    stats['dlc_owned'] = conn.execute("SELECT COUNT(*) FROM dlc WHERE owned = 1").fetchone()[0]

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
        'has_credentials': bool(config.get('twitch_client_id') and config.get('twitch_client_secret')),
        'anthropic_api_key': '••••••••' if config.get('anthropic_api_key') else '',
        'decider_model': config.get('decider_model', 'claude-sonnet-4-6'),
        'decider_models': [{'id': mid, 'label': label} for mid, label in DECIDER_MODELS],
        'has_anthropic_key': bool(config.get('anthropic_api_key')),
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

    if 'anthropic_api_key' in data and data['anthropic_api_key'] != '••••••••':
        updates['anthropic_api_key'] = data['anthropic_api_key'].strip()

    if 'decider_model' in data:
        updates['decider_model'] = data['decider_model'].strip()

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


@app.route('/api/barcode/resolve')
def api_barcode_resolve():
    """Resolve a scanned UPC to candidate games (cache -> UPCitemdb -> IGDB)."""
    upc = (request.args.get('upc') or '').strip()
    if not upc:
        return jsonify({'error': 'upc required'}), 400

    client_id, client_secret = get_twitch_credentials()
    token = None
    if client_id:
        try:
            from fetch_covers import get_access_token
            token = get_access_token(client_id, client_secret)
        except Exception as exc:   # best-effort: a token failure just skips IGDB matching
            app.logger.warning("IGDB token fetch failed during barcode resolve: %s", exc)
            client_id = None

    conn = get_db()
    result = barcode.resolve(conn, upc, client_id=client_id, token=token)
    conn.close()
    return jsonify(result)


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
            fields name, slug, cover.url, platforms, first_release_date;
            limit 8;
        '''

        response = requests.post(
            'https://api.igdb.com/v4/games',
            headers=headers,
            data=igdb_query
        )
        response.raise_for_status()
        results = response.json()

        from datetime import datetime as _datetime

        # Format results
        games = []
        for game in results:
            cover_url = None
            if game.get('cover') and game['cover'].get('url'):
                cover_url = game['cover']['url'].replace('t_thumb', 't_cover_big')
                if not cover_url.startswith('http'):
                    cover_url = 'https:' + cover_url

            slug = game.get('slug') or ''
            year = None
            ts = game.get('first_release_date')
            if ts:
                try:
                    year = _datetime.utcfromtimestamp(int(ts)).year
                except (ValueError, OverflowError, OSError):
                    year = None
            games.append({
                'name': game.get('name', ''),
                'slug': slug,
                'cover_url': cover_url,
                'igdb_url': f'https://www.igdb.com/games/{slug}' if slug else '',
                # IGDB platform ids mapped to the short_names we model (for the
                # Add Game modal's tab-scoped single-platform auto-select).
                'platforms': igdb_match.short_names_for(game.get('platforms') or []),
                'year': year,
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


@app.route('/api/scrape/start', methods=['POST'])
def api_scrape_start():
    """Start a web-driven vendor library scrape in the background."""
    vendor = (request.json or {}).get('vendor', '')
    if vendor not in scrape_service.VENDORS:
        return jsonify({'error': f'unknown vendor: {vendor}'}), 400
    ok, message = scrape_service.start(vendor)
    if ok:
        return jsonify({'success': True, 'message': message})
    return jsonify({'error': message}), 409  # already running


@app.route('/api/scrape/continue', methods=['POST'])
def api_scrape_continue():
    """Signal that the user has logged in and the scrape may proceed."""
    scrape_service.signal_continue()
    return jsonify({'success': True})


@app.route('/api/scrape/cancel', methods=['POST'])
def api_scrape_cancel():
    """Request cancellation of the running scrape."""
    scrape_service.cancel()
    return jsonify({'success': True})


@app.route('/api/scrape/status')
def api_scrape_status():
    """Current scrape phase/progress for the UI poller."""
    return jsonify(scrape_service.status())


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

    # use_reloader=False: the "scrape now" feature runs a long-lived background
    # thread (browser -> import -> IGDB enrich -> ownership). The dev auto-reloader
    # restarts the server whenever a .py file changes, which would kill that thread
    # mid-run and reset the scrape to "idle". Keep debug error pages, drop the reloader.
    # Bind to 127.0.0.1 by default (secure). Set HOST=0.0.0.0 to expose on the
    # LAN for the Android companion app over Wi-Fi (trusted home network only).
    app.run(debug=True, use_reloader=False,
            host=os.environ.get("HOST", "127.0.0.1"), port=5000)
