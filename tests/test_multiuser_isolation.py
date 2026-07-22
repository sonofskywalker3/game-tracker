"""Cross-user isolation for the /api/games* routes.

Started in Task 6 (list + by-id 404 + one cross-library write route) and
expanded into the full isolation sweep in Task 9. Proves a user can neither see
nor mutate another user's games; unowned game ids yield 404, never 403.
"""
from __future__ import annotations

from tests.helpers_multiuser import (
    client_as,
    seed_collection_membership,
    seed_decider_chat,
    seed_dlc,
    seed_game,
    seed_slot,
    seed_tag,
    set_status,
)


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


# --- Task 7: the remaining roots + their children -------------------------


def test_user_cannot_see_another_users_slots(mu_db):
    """slots root: /api/slots must show only the acting user's slots."""
    seed_slot(mu_db, user_id=1, label="A-Slot")
    seed_slot(mu_db, user_id=2, label="B-Slot")
    labels = [s["label"] for s in client_as(2).get("/api/slots").get_json()["slots"]]
    assert "B-Slot" in labels and "A-Slot" not in labels


def test_user_cannot_delete_another_users_slot(mu_db):
    """slots root single-row write: deleting an unowned slot is a 404 no-op."""
    sid = seed_slot(mu_db, user_id=1, label="A-Slot")
    assert client_as(2).delete(f"/api/slots/{sid}").status_code == 404
    still = mu_db.execute("SELECT 1 FROM slots WHERE id = ?", (sid,)).fetchone()
    assert still is not None


def test_user_cannot_patch_another_users_slot(mu_db):
    """slots root single-row write: PATCHing an unowned slot is a 404 no-op."""
    sid = seed_slot(mu_db, user_id=1, label="A-Slot")
    resp = client_as(2).patch(f"/api/slots/{sid}", json={"label": "hijacked"})
    assert resp.status_code == 404
    label = mu_db.execute("SELECT label FROM slots WHERE id = ?", (sid,)).fetchone()["label"]
    assert label == "A-Slot"


def test_user_cannot_see_another_users_tags(mu_db):
    """tags root: /api/tags must list only the acting user's tags."""
    seed_tag(mu_db, user_id=1, name="A-Tag")
    seed_tag(mu_db, user_id=2, name="B-Tag")
    names = [t["name"] for t in client_as(2).get("/api/tags").get_json()]
    assert "B-Tag" in names and "A-Tag" not in names


def test_user_cannot_see_another_users_ratings_in_slot_candidates(mu_db):
    """user_ratings (child of games): another user's in-progress game must never
    surface in this user's slate candidate pool (rank_candidates reads ratings)."""
    a_game = seed_game(mu_db, user_id=1, title="A-Playing")
    set_status(mu_db, a_game, "playing")
    b_game = seed_game(mu_db, user_id=2, title="B-Playing")
    set_status(mu_db, b_game, "playing")
    seed_slot(mu_db, user_id=2, label="B-Slot")
    state = client_as(2).get("/api/slots").get_json()["slots"]
    candidate_ids = {c["game"]["id"] for s in state for c in s["candidates"]}
    assert b_game in candidate_ids and a_game not in candidate_ids


def test_user_cannot_see_another_users_collections_membership(mu_db):
    """game_collections (child): the collection catalog is shared, but a user only
    sees collections their OWN games belong to, with only their own members."""
    a_game = seed_game(mu_db, user_id=1, title="A-FF")
    b_game = seed_game(mu_db, user_id=2, title="B-FF")
    seed_collection_membership(mu_db, a_game, 39, "Final Fantasy")
    seed_collection_membership(mu_db, b_game, 39, "Final Fantasy")
    # user 1 owns a collection user 2 does not populate.
    seed_collection_membership(mu_db, a_game, 77, "A-Only Series")

    cl = client_as(2)
    cols = cl.get("/api/collections").get_json()["collections"]
    by_id = {c["id"]: c for c in cols}
    assert 77 not in by_id                      # user 2 has no games there
    assert by_id[39]["owned_count"] == 1        # only user 2's own member counted

    detail = cl.get("/api/collections/39").get_json()
    member_ids = {g["id"] for g in detail["games"]}
    assert member_ids == {b_game}               # never user 1's member


def test_user_cannot_see_another_users_decider_chat(mu_db):
    """decider_chats root: chats hang off a game, so an unowned game's chats are a
    404, and a user's own game shows only their chats."""
    a_game = seed_game(mu_db, user_id=1, title="A-Only")
    seed_decider_chat(mu_db, a_game, user_id=1, slot_label="A-Chat")
    b_game = seed_game(mu_db, user_id=2, title="B-Only")
    seed_decider_chat(mu_db, b_game, user_id=2, slot_label="B-Chat")

    cl = client_as(2)
    assert cl.get(f"/api/games/{a_game}/decider-chat").status_code == 404
    chats = cl.get(f"/api/games/{b_game}/decider-chat").get_json()["chats"]
    assert [c["slot_label"] for c in chats] == ["B-Chat"]


def test_user_cannot_see_another_users_profile(mu_db):
    """user_profile root: each user gets their own row; one user's PUT never
    touches another's, and a user reads only their own values."""
    client_as(1).put("/api/profile", json={"work_start_min": 111})
    client_as(2).put("/api/profile", json={"work_start_min": 222})
    assert client_as(1).get("/api/profile").get_json()["work_start_min"] == 111
    assert client_as(2).get("/api/profile").get_json()["work_start_min"] == 222


def test_user_cannot_see_another_users_duplicates(mu_db):
    """not_duplicates / dedup: the duplicate finder is scoped, so one user's dup
    pair never appears in another user's dedup modal (and their own does).

    A "definite" duplicate needs two rows with the same base_key but distinct
    stored normalized_title (the per-user UNIQUE) -- a leading region tag does
    exactly that: clean_title strips it (so base_key matches) while
    normalize_title keeps it (so the stored keys differ)."""
    seed_game(mu_db, user_id=1, title="Halo")
    seed_game(mu_db, user_id=1, title="(English) Halo")   # user 1's dup pair
    b1 = seed_game(mu_db, user_id=2, title="Portal")
    b2 = seed_game(mu_db, user_id=2, title="(English) Portal")  # user 2's own

    dupes = client_as(2).get("/api/duplicates").get_json()
    referenced = {gid for grp in dupes["definite"] for gid in grp}
    assert referenced == {b1, b2}                        # only user 2's games


def test_set_dlc_owned_404_for_unowned_dlc(mu_db):
    """WRITE HOLE: toggling ownership of a DLC whose parent game belongs to
    another user must 404 and leave the row untouched."""
    a_game = seed_game(mu_db, user_id=1, title="A-Only")
    dlc_id = seed_dlc(mu_db, a_game, "Season Pass", owned=0)
    resp = client_as(2).post(f"/api/dlc/{dlc_id}/owned", json={"owned": True})
    assert resp.status_code == 404
    owned = mu_db.execute("SELECT owned FROM dlc WHERE id = ?", (dlc_id,)).fetchone()["owned"]
    assert owned == 0


def test_delete_dlc_404_for_unowned_dlc(mu_db):
    """WRITE HOLE: deleting a DLC whose parent game belongs to another user must
    404 and leave the row in place."""
    a_game = seed_game(mu_db, user_id=1, title="A-Only")
    dlc_id = seed_dlc(mu_db, a_game, "Season Pass")
    assert client_as(2).delete(f"/api/dlc/{dlc_id}").status_code == 404
    assert mu_db.execute("SELECT 1 FROM dlc WHERE id = ?", (dlc_id,)).fetchone() is not None
