"""
Fetch cover art from IGDB (Twitch) API for games missing covers.

Setup:
1. Go to https://dev.twitch.tv/console
2. Register an application
3. Get your Client ID and generate a Client Secret
4. Run: python fetch_covers.py --client-id YOUR_ID --client-secret YOUR_SECRET
"""
import argparse
import json
import logging
import re
import time
from pathlib import Path

import requests

import config
from models import clean_title, get_db, normalize_title

logger = logging.getLogger(__name__)

# IGDB API endpoints
TWITCH_AUTH_URL = "https://id.twitch.tv/oauth2/token"
IGDB_API_URL = "https://api.igdb.com/v4"

# Cache file for auth token
TOKEN_CACHE = Path(__file__).parent / ".igdb_token.json"

# IGDB is the cover source of record (portrait box art ~264x352).
IGDB_HOST = "images.igdb.com"
# Cover hosts whose art is the wrong shape (wide hero art). On an IGDB miss these
# are nulled rather than kept; other vendor covers are left alone. Extensible.
WIDE_ART_HOSTS = frozenset({"assets.nintendo.com"})


def cover_host(url):
    """Return the host of an http(s) cover URL, or None if empty/non-URL."""
    if not url or not str(url).startswith("http"):
        return None
    return url.split("/")[2]


def needs_cover(url, *, upgrade):
    """True if a game needs a cover fetched.

    Always true when the cover is missing. In upgrade mode, also true when the
    cover is from a non-IGDB host (vendor art we want to replace with IGDB).
    """
    host = cover_host(url)
    if host is None:
        return True
    return upgrade and host != IGDB_HOST


def should_null_on_miss(url):
    """When IGDB has no match, null only known-bad wide art; keep other covers."""
    return cover_host(url) in WIDE_ART_HOSTS


def clean_search_title(title):
    """Normalize a title for IGDB search.

    Drops quotes (which break IGDB's `search "..."` query -> HTTP 400), a trailing
    platform parenthetical, common edition suffixes, and trademark symbols.
    """
    t = (title or "").replace('"', '').replace('“', '').replace('”', '')
    # Remove platform indicators like "(Switch)" at the end
    t = re.sub(r'\s*\([^)]*\)\s*$', '', t)
    # Remove common edition suffixes
    t = re.sub(
        r'\s*[-–:]\s*(Deluxe|Ultimate|Complete|Gold|Game of the Year|GOTY|'
        r'Remastered|HD|Definitive|Anniversary|Enhanced|Special)\s*'
        r'(Edition|Bundle|Collection)?.*$',
        '', t, flags=re.IGNORECASE)
    # Remove trademark symbols
    t = t.replace('™', '').replace('®', '').replace('©', '')
    return t.strip()


def get_access_token(client_id, client_secret, force_refresh=False):
    """Get OAuth access token from Twitch, with caching."""

    # Check cache first
    if not force_refresh and TOKEN_CACHE.exists():
        try:
            cache = json.loads(TOKEN_CACHE.read_text())
            if cache.get('expires_at', 0) > time.time() + 3600:  # 1 hour buffer
                return cache['access_token']
        except (json.JSONDecodeError, KeyError):
            pass

    # Request new token
    response = requests.post(TWITCH_AUTH_URL, params={
        'client_id': client_id,
        'client_secret': client_secret,
        'grant_type': 'client_credentials'
    })
    response.raise_for_status()
    data = response.json()

    # Cache token
    cache = {
        'access_token': data['access_token'],
        'expires_at': time.time() + data['expires_in']
    }
    TOKEN_CACHE.write_text(json.dumps(cache))

    return data['access_token']


def _igdb_search(clean_title, client_id, access_token):
    """POST a `search "<clean_title>"` query to IGDB; return the results list.

    Retries once on HTTP 429. `clean_title` must already be cleaned by the caller.
    Shared by the cover search and the canonical-name lookup.
    """
    headers = {
        'Client-ID': client_id,
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'text/plain'
    }
    query = f'''
        search "{clean_title}";
        fields name, cover.url;
        limit 5;
    '''
    response = requests.post(f"{IGDB_API_URL}/games", headers=headers, data=query)

    if response.status_code == 429:
        # Rate limited, wait and retry
        time.sleep(1)
        return _igdb_search(clean_title, client_id, access_token)

    response.raise_for_status()
    return response.json()


def search_game(title, client_id, access_token, strict=False):
    """Search IGDB for a game and return cover URL if found.

    When strict, only a confident (normalized exact/containment) match is
    accepted; the loose "first result with any cover" fallback is skipped so an
    existing correct cover is never replaced by a wrong game's art.
    """

    # Clean up title for better matching (and to avoid query-breaking quotes)
    clean_title = clean_search_title(title)

    results = _igdb_search(clean_title, client_id, access_token)

    if not results:
        return None

    # Find best match
    normalized_search = normalize_title(clean_title)

    for game in results:
        normalized_result = normalize_title(game.get('name', ''))
        # Check for exact or close match
        if normalized_result == normalized_search or normalized_search in normalized_result or normalized_result in normalized_search:
            cover = game.get('cover', {})
            if cover and cover.get('url'):
                # Convert to high-res URL
                # IGDB returns //images.igdb.com/igdb/image/upload/t_thumb/xxx.jpg
                # We want t_cover_big (264x374) or t_720p (1280x720)
                url = cover['url']
                url = url.replace('t_thumb', 't_cover_big')
                if not url.startswith('http'):
                    url = 'https:' + url
                return url

    # If no confident match, take first result with cover — unless strict.
    if not strict:
        for game in results:
            cover = game.get('cover', {})
            if cover and cover.get('url'):
                url = cover['url']
                url = url.replace('t_thumb', 't_cover_big')
                if not url.startswith('http'):
                    url = 'https:' + url
                return url

    return None


def pick_canonical_name(search_title, results):
    """Return IGDB's official game name on a strict match, else None.

    Strict = the normalized IGDB name equals (preferred) or contains / is
    contained by the normalized search title — the same confident-match rule used
    for covers, with exact matches preferred so e.g. "Portal" is never renamed to
    "Portal 2". Pure: operates on already-fetched results. Returns None on no
    confident match so a title is never renamed to a wrong game.
    """
    normalized_search = normalize_title(clean_search_title(search_title))
    if not normalized_search:
        return None
    names = [(g.get("name", ""), normalize_title(g.get("name", ""))) for g in results]
    for name, normalized in names:
        if normalized and normalized == normalized_search:
            return name
    for name, normalized in names:
        if normalized and (normalized_search in normalized or normalized in normalized_search):
            return name
    return None


def search_canonical_name(title, client_id, access_token):
    """Query IGDB and return the official game name on a strict match, else None."""
    results = _igdb_search(clean_search_title(title), client_id, access_token)
    return pick_canonical_name(title, results)


def fetch_covers_generator(client_id, client_secret, limit=None, skip_existing=True,
                           upgrade_non_igdb=False):
    """
    Generator that fetches covers and yields progress updates.
    Yields dicts with: {current, total, title, status, found, not_found}

    upgrade_non_igdb: also (re)fetch games whose cover is from a non-IGDB host,
    replacing it on a confident match; wide vendor art is nulled on a miss.
    """
    try:
        access_token = get_access_token(client_id, client_secret)
    except Exception as e:
        yield {'error': f'Authentication failed: {str(e)}'}
        return

    conn = get_db()

    rows = conn.execute("SELECT id, title, cover_url FROM games ORDER BY title").fetchall()
    if skip_existing:
        games = [g for g in rows if needs_cover(g['cover_url'], upgrade=upgrade_non_igdb)]
    else:
        games = list(rows)  # --all: re-fetch everything

    if limit:
        games = games[:limit]

    total = len(games)
    found = 0
    not_found_list = []

    if total == 0:
        yield {'current': 0, 'total': 0, 'status': 'complete', 'found': 0, 'not_found': []}
        return

    for i, game in enumerate(games, 1):
        title = game['title']

        try:
            cover_url = search_game(title, client_id, access_token, strict=upgrade_non_igdb)

            if cover_url:
                conn.execute(
                    "UPDATE games SET cover_url = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (cover_url, game['id'])
                )
                conn.commit()
                found += 1
                status = 'found'
            elif should_null_on_miss(game['cover_url']):
                # IGDB has no match and the existing art is wrong-shape: drop it.
                conn.execute(
                    "UPDATE games SET cover_url = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (game['id'],)
                )
                conn.commit()
                not_found_list.append(title)
                status = 'nulled'
            else:
                not_found_list.append(title)
                status = 'not_found'

            # Rate limiting: IGDB allows 4 requests/second
            time.sleep(0.3)

        except Exception as e:
            not_found_list.append(title)
            status = f'error: {str(e)}'

        yield {
            'current': i,
            'total': total,
            'title': title,
            'status': status,
            'found': found,
            'not_found': not_found_list.copy()
        }

    conn.close()

    yield {
        'current': total,
        'total': total,
        'status': 'complete',
        'found': found,
        'not_found': not_found_list
    }


def fetch_all_covers(client_id, client_secret, limit=None, skip_existing=True,
                     upgrade_non_igdb=False):
    """Fetch covers for all games missing them (CLI version with print output)."""

    print("Authenticating with Twitch/IGDB...")

    for progress in fetch_covers_generator(client_id, client_secret, limit, skip_existing,
                                           upgrade_non_igdb):
        if 'error' in progress:
            print(f"Error: {progress['error']}")
            return

        if progress['status'] == 'complete':
            print(f"\n{'='*50}")
            print(f"Done! Found covers for {progress['found']}/{progress['total']} games")
            if progress['not_found']:
                print(f"\nCouldn't find covers for {len(progress['not_found'])} games:")
                for title in progress['not_found'][:20]:
                    print(f"  - {title}")
                if len(progress['not_found']) > 20:
                    print(f"  ... and {len(progress['not_found']) - 20} more")
        else:
            status_str = "Found!" if progress['status'] == 'found' else progress['status']
            print(f"[{progress['current']}/{progress['total']}] {progress['title'][:50]}... {status_str}")


def update_canonical_titles(client_id, client_secret, limit=None, dry_run=False):
    """Adopt IGDB's official game name as the display title on a strict match.

    Display-only: updates games.title, never normalized_title (recomputing the
    match key and merging the duplicates it surfaces is the dedup workstream). A
    miss keeps the existing (Part-A-cleaned) title — we never guess. Idempotent: a
    title already equal to its canonical name is skipped. Re-runnable; honors
    dry_run. Needs Twitch/IGDB creds (same as covers).
    """
    access_token = get_access_token(client_id, client_secret)
    conn = get_db()
    rows = conn.execute("SELECT id, title FROM games ORDER BY title").fetchall()
    if limit:
        rows = rows[:limit]

    renamed = 0
    for row in rows:
        title = row["title"]
        try:
            canonical = search_canonical_name(title, client_id, access_token)
        except requests.RequestException as e:
            logger.warning("lookup failed for %s: %s", title, e)
            continue

        # Run the adopted name back through clean_title so the stored value is a
        # fixed point — otherwise the startup reclean (migrate_db) would re-case an
        # ALL-CAPS official name (e.g. "DOOM" -> "Doom") and the next pass would
        # flip it back. clean_title leaves normal-case IGDB casing fixes intact.
        new_title = clean_title(canonical) if canonical else None
        if new_title and new_title != title:
            renamed += 1
            logger.info("rename: %s  ->  %s", title, new_title)
            if not dry_run:
                conn.execute(
                    "UPDATE games SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (new_title, row["id"]),
                )
                conn.commit()
        time.sleep(0.3)  # IGDB rate limit (~4 req/s)

    conn.close()
    label = "would rename" if dry_run else "renamed"
    logger.info("--- canonical titles: %s %d of %d ---", label, renamed, len(rows))


def _resolve_credentials(args):
    """Twitch creds from CLI args, falling back to config.json."""
    client_id, client_secret = args.client_id, args.client_secret
    if not client_id or not client_secret:
        cfg_id, cfg_secret = config.get_twitch_credentials()
        client_id = client_id or cfg_id
        client_secret = client_secret or cfg_secret
    return client_id, client_secret


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description='Fetch cover art / canonical titles from IGDB')
    parser.add_argument('--client-id', help='Twitch Client ID (default: config.json)')
    parser.add_argument('--client-secret', help='Twitch Client Secret (default: config.json)')
    parser.add_argument('--limit', type=int, help='Limit number of games to process')
    parser.add_argument('--all', action='store_true', help='Re-fetch all covers, not just missing ones')
    parser.add_argument('--upgrade-non-igdb', action='store_true',
                        help='Also replace covers from non-IGDB hosts (vendor art) with IGDB art')
    parser.add_argument('--canonical-titles', action='store_true',
                        help="Adopt IGDB's official game name as the display title on a strict match")
    parser.add_argument('--dry-run', action='store_true',
                        help='With --canonical-titles, preview renames without writing')

    args = parser.parse_args()

    client_id, client_secret = _resolve_credentials(args)
    if not client_id or not client_secret:
        parser.error("Twitch credentials required: pass --client-id/--client-secret "
                     "or set them in config.json")

    if args.canonical_titles:
        update_canonical_titles(client_id, client_secret, limit=args.limit, dry_run=args.dry_run)
        return

    fetch_all_covers(
        client_id,
        client_secret,
        limit=args.limit,
        skip_existing=not args.all,
        upgrade_non_igdb=args.upgrade_non_igdb,
    )


if __name__ == "__main__":
    main()
