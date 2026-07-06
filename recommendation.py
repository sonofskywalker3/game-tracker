"""
Game recommendation engine using weighted scoring.

Factors considered:
1. User's personal rating of similar games (by tags)
2. Critic scores (metacritic/opencritic)
3. User-set priority
4. How long the game has been in backlog
5. Tag affinity (genres the user rates highly)
"""
from datetime import datetime

from models import get_db

# Effective time-to-beat (override, else HLTB main) at or under this counts as a
# "quick session" pick.
QUICK_SESSION_MAX_MINUTES = 600


def calculate_tag_affinity(conn):
    """
    Calculate user's affinity for each tag based on ratings of completed/played games.
    Returns dict: {tag_id: affinity_score}
    """
    # Get average rating per tag from games the user has rated
    rows = conn.execute("""
        SELECT
            gt.tag_id,
            t.name,
            AVG(ur.rating) as avg_rating,
            COUNT(*) as game_count
        FROM game_tags gt
        JOIN tags t ON t.id = gt.tag_id
        JOIN user_ratings ur ON ur.game_id = gt.game_id
        WHERE ur.rating IS NOT NULL
        GROUP BY gt.tag_id
        HAVING game_count >= 1
    """).fetchall()

    affinity = {}
    for row in rows:
        # Weight by number of games rated with this tag
        weight = min(row['game_count'] / 5, 1.0)  # Cap at 5 games
        affinity[row['tag_id']] = {
            'name': row['name'],
            'score': row['avg_rating'] * weight,
            'avg_rating': row['avg_rating'],
            'count': row['game_count']
        }

    return affinity


def get_recommendations(conn, limit=10, status_filter='backlog'):
    """
    Get recommended games to play next.

    Scoring formula:
    - Base score: 50
    - Priority boost: (priority - 5) * 5 (range: -20 to +25)
    - Critic score boost: (avg_critic / 100) * 20 (range: 0 to 20)
    - Tag affinity boost: based on user's rated games with same tags
    - Backlog age boost: older games get slight priority

    Returns list of (game, score, reasons) tuples.
    """
    tag_affinity = calculate_tag_affinity(conn)

    # Get all backlog games with their data
    games = conn.execute("""
        SELECT
            g.id,
            g.title,
            g.cover_url,
            g.metacritic_score,
            g.opencritic_score,
            ur.status,
            ur.priority,
            ur.rating,
            ur.notes,
            ur.hours_played,
            g.created_at
        FROM games g
        JOIN user_ratings ur ON ur.game_id = g.id
        WHERE ur.status = ?
        ORDER BY g.title
    """, (status_filter,)).fetchall()

    recommendations = []

    for game in games:
        score = 50.0  # Base score
        reasons = []

        # Priority boost (-20 to +25)
        priority = game['priority'] or 5
        priority_boost = (priority - 5) * 5
        score += priority_boost
        if priority >= 7:
            reasons.append(f"High priority ({priority}/10)")
        elif priority <= 3:
            reasons.append(f"Low priority ({priority}/10)")

        # Critic score boost (0 to 20)
        critic_scores = []
        if game['metacritic_score']:
            critic_scores.append(game['metacritic_score'])
        if game['opencritic_score']:
            critic_scores.append(game['opencritic_score'])

        if critic_scores:
            avg_critic = sum(critic_scores) / len(critic_scores)
            critic_boost = (avg_critic / 100) * 20
            score += critic_boost
            if avg_critic >= 85:
                reasons.append(f"Highly rated ({avg_critic:.0f} critic score)")
            elif avg_critic >= 75:
                reasons.append(f"Well reviewed ({avg_critic:.0f} critic score)")

        # Tag affinity boost
        game_tags = conn.execute("""
            SELECT tag_id FROM game_tags WHERE game_id = ?
        """, (game['id'],)).fetchall()

        tag_boost = 0
        matching_tags = []
        for tag_row in game_tags:
            tag_id = tag_row['tag_id']
            if tag_id in tag_affinity:
                affinity = tag_affinity[tag_id]
                if affinity['avg_rating'] >= 7:
                    tag_boost += affinity['score'] * 0.5
                    matching_tags.append(affinity['name'])

        score += min(tag_boost, 15)  # Cap tag boost at 15
        if matching_tags:
            reasons.append(f"Matches your taste: {', '.join(matching_tags[:3])}")

        # Backlog age boost (slight preference for older additions)
        # This prevents newer games from always getting picked
        try:
            created = datetime.fromisoformat(game['created_at'].replace('Z', '+00:00'))
            days_in_backlog = (datetime.now() - created.replace(tzinfo=None)).days
            age_boost = min(days_in_backlog / 365 * 3, 5)  # Max 5 points after 1+ year
            score += age_boost
            if days_in_backlog > 365:
                reasons.append("Been waiting in your backlog")
        except (ValueError, TypeError, AttributeError):
            pass

        recommendations.append({
            'game': dict(game),
            'score': round(score, 1),
            'reasons': reasons
        })

    # Sort by score descending
    recommendations.sort(key=lambda x: x['score'], reverse=True)

    return recommendations[:limit]


def get_quick_picks(conn, count=3):
    """
    Get quick game suggestions for different moods/situations.
    Returns dict with categories.
    """
    picks = {}

    # "Something short" — actually-short games: the session_length trait, the
    # effective time-to-beat (user override beats HLTB), or a short-leaning genre.
    # (Formerly keyed on ur.hours_played, which defaults to 0 and made every
    # unstarted 100-hour JRPG a "quick session".)
    picks['quick_session'] = conn.execute("""
        SELECT g.id, g.title, g.cover_url, ur.priority
        FROM games g
        JOIN user_ratings ur ON ur.game_id = g.id
        LEFT JOIN game_tags gt ON gt.game_id = g.id
        LEFT JOIN tags t ON t.id = gt.tag_id
        WHERE ur.status = 'backlog'
        AND (g.session_length = 'short'
             OR COALESCE(g.time_to_beat_override_minutes, g.hltb_main_minutes) <= ?
             OR t.name IN ('Indie', 'Puzzle', 'Platformer'))
        GROUP BY g.id
        ORDER BY ur.priority DESC, RANDOM()
        LIMIT ?
    """, (QUICK_SESSION_MAX_MINUTES, count)).fetchall()

    # "Highly rated" - best critic scores
    picks['critically_acclaimed'] = conn.execute("""
        SELECT g.id, g.title, g.cover_url,
               COALESCE(g.metacritic_score, g.opencritic_score) as score
        FROM games g
        JOIN user_ratings ur ON ur.game_id = g.id
        WHERE ur.status = 'backlog'
        AND (g.metacritic_score >= 85 OR g.opencritic_score >= 85)
        ORDER BY score DESC
        LIMIT ?
    """, (count,)).fetchall()

    # "Hidden gems" - lower profile but potentially good
    picks['hidden_gems'] = conn.execute("""
        SELECT g.id, g.title, g.cover_url
        FROM games g
        JOIN user_ratings ur ON ur.game_id = g.id
        WHERE ur.status = 'backlog'
        AND g.metacritic_score IS NULL
        AND g.opencritic_score IS NULL
        ORDER BY ur.priority DESC, RANDOM()
        LIMIT ?
    """, (count,)).fetchall()

    # "Continue playing" - games in progress
    picks['continue_playing'] = conn.execute("""
        SELECT g.id, g.title, g.cover_url, ur.hours_played
        FROM games g
        JOIN user_ratings ur ON ur.game_id = g.id
        WHERE ur.status = 'playing'
        ORDER BY ur.updated_at DESC
        LIMIT ?
    """, (count,)).fetchall()

    return picks


if __name__ == "__main__":
    # Test the recommendation engine
    conn = get_db()

    print("Tag Affinity Analysis:")
    print("-" * 40)
    affinity = calculate_tag_affinity(conn)
    for tag_id, data in sorted(affinity.items(), key=lambda x: x[1]['score'], reverse=True)[:10]:
        print(f"  {data['name']}: {data['score']:.1f} (avg: {data['avg_rating']:.1f}, count: {data['count']})")

    print("\nTop Recommendations:")
    print("-" * 40)
    recs = get_recommendations(conn, limit=10)
    for i, rec in enumerate(recs, 1):
        game = rec['game']
        print(f"\n{i}. {game['title']} (Score: {rec['score']})")
        if rec['reasons']:
            for reason in rec['reasons']:
                print(f"   - {reason}")

    print("\nQuick Picks:")
    print("-" * 40)
    picks = get_quick_picks(conn)
    for category, games in picks.items():
        print(f"\n{category.replace('_', ' ').title()}:")
        for game in games:
            print(f"  - {game['title']}")

    conn.close()
