"""Cross-user isolation for the /api/games* routes.

Started in Task 6 (list + by-id 404 + one cross-library write route) and
expanded into the full isolation sweep in Task 9. Proves a user can neither see
nor mutate another user's games; unowned game ids yield 404, never 403.
"""
from __future__ import annotations

from tests.helpers_multiuser import client_as, seed_game


def test_user_cannot_list_another_users_games(mu_db):
    seed_game(mu_db, user_id=1, title="A-Only")
    seed_game(mu_db, user_id=2, title="B-Only")
    cl = client_as(2)
    titles = [g["title"] for g in cl.get("/api/games").get_json()]
    assert "B-Only" in titles and "A-Only" not in titles


def test_user_cannot_fetch_another_users_game_by_id(mu_db):
    gid = seed_game(mu_db, user_id=1, title="A-Only")
    assert client_as(2).get(f"/api/games/{gid}").status_code == 404


def test_reorder_cannot_touch_another_users_games(mu_db):
    """A cross-library write route: user 2 reordering user 1's game must not
    create/alter a user_ratings row for it (unscoped, the INSERT ... ON CONFLICT
    would silently reorder every user's rows)."""
    gid_a = seed_game(mu_db, user_id=1, title="A-Only")
    gid_b = seed_game(mu_db, user_id=2, title="B-Only")
    # user 2 tries to reorder both their own game and user 1's game.
    resp = client_as(2).post("/api/games/reorder", json={"game_ids": [gid_b, gid_a]})
    assert resp.status_code == 200
    # user 1's game keeps its original (null) sort_order -- untouched by user 2.
    row = mu_db.execute(
        "SELECT sort_order FROM user_ratings WHERE game_id = ?", (gid_a,)
    ).fetchone()
    assert row["sort_order"] is None
    # user 2's own game WAS reordered (index 0).
    own = mu_db.execute(
        "SELECT sort_order FROM user_ratings WHERE game_id = ?", (gid_b,)
    ).fetchone()
    assert own["sort_order"] == 0
