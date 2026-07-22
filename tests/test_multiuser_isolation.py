"""Cross-user isolation for the /api/games* routes.

Started in Task 6 (list + by-id 404 + one cross-library write route) and
expanded into the full isolation sweep in Task 9. Proves a user can neither see
nor mutate another user's games; unowned game ids yield 404, never 403.
"""
from __future__ import annotations

import pytest

from tests.helpers_multiuser import (
    client_as,
    seed_bundle_review,
    seed_collection_membership,
    seed_decider_chat,
    seed_dlc,
    seed_dlc_review,
    seed_game,
    seed_registry,
    seed_slot,
    seed_tag,
    seed_upc_review,
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


# ===========================================================================
# Task 9: the parametrized cross-user isolation SWEEP (the correctness gate).
#
# Two parametrized families cover the full user-facing route inventory:
#   * READ sweep  - seed a user-1 row AND a user-2 row per route; as user 2,
#     assert user 1's marker is absent from the body and user 2's own marker is
#     present (isolation + a positive "the route still works" control in one).
#   * WRITE sweep - seed a user-1-owned row; as user 2, the single-row action
#     targeting its id returns 404 and leaves the row untouched.
# Plus standalone positive controls (shared/global data stays visible to both)
# and the picked_game_id injection test the sweep cannot surface on its own.
# The per-root reads/writes above (games/slots/tags/collections/dlc/...) remain
# as focused Task 6-8 regression tests; this sweep is the completeness checklist.
# ===========================================================================


# --- READ sweep: (label, url, seed_fn -> (user1_marker, user2_marker)) ------

def _sweep_seed_games(conn) -> tuple[str, str]:
    seed_game(conn, user_id=1, title="AAUSER1GAME")
    seed_game(conn, user_id=2, title="BBUSER2GAME")
    return "AAUSER1GAME", "BBUSER2GAME"


def _sweep_seed_slots(conn) -> tuple[str, str]:
    seed_slot(conn, user_id=1, label="AAUSER1SLOT")
    seed_slot(conn, user_id=2, label="BBUSER2SLOT")
    return "AAUSER1SLOT", "BBUSER2SLOT"


def _sweep_seed_tags(conn) -> tuple[str, str]:
    seed_tag(conn, user_id=1, name="AAUSER1TAG")
    seed_tag(conn, user_id=2, name="BBUSER2TAG")
    return "AAUSER1TAG", "BBUSER2TAG"


def _sweep_seed_dlc_review(conn) -> tuple[str, str]:
    # game_id NULL on purpose: the queue's own user_id column is the only thing
    # that can isolate a no-parent row (a parent-JOIN cannot).
    seed_dlc_review(conn, user_id=1, addon_title="AAUSER1DLCREV")
    seed_dlc_review(conn, user_id=2, addon_title="BBUSER2DLCREV")
    return "AAUSER1DLCREV", "BBUSER2DLCREV"


def _sweep_seed_bundle_review(conn) -> tuple[str, str]:
    seed_bundle_review(conn, user_id=1, game_title="AAUSER1BUNDLE")
    seed_bundle_review(conn, user_id=2, game_title="BBUSER2BUNDLE")
    return "AAUSER1BUNDLE", "BBUSER2BUNDLE"


def _sweep_seed_enrichment_review(conn) -> tuple[str, str]:
    # upc_review.game_id is NOT NULL - isolation rides the parent game's owner.
    a_game = seed_game(conn, user_id=1, title="AAUSER1UPCGAME")
    seed_upc_review(conn, a_game)
    b_game = seed_game(conn, user_id=2, title="BBUSER2UPCGAME")
    seed_upc_review(conn, b_game)
    return "AAUSER1UPCGAME", "BBUSER2UPCGAME"


def _sweep_seed_export(conn) -> tuple[str, str]:
    seed_game(conn, user_id=1, title="AAUSER1EXPORT")
    seed_game(conn, user_id=2, title="BBUSER2EXPORT")
    return "AAUSER1EXPORT", "BBUSER2EXPORT"


READ_SWEEP = [
    ("games", "/api/games", _sweep_seed_games),
    ("slots", "/api/slots", _sweep_seed_slots),
    ("tags", "/api/tags", _sweep_seed_tags),
    ("dlc_review", "/api/dlc/review", _sweep_seed_dlc_review),
    ("bundle_review", "/api/bundle-review", _sweep_seed_bundle_review),
    ("enrichment_review", "/api/enrichment/review", _sweep_seed_enrichment_review),
    ("data_export", "/api/data/export", _sweep_seed_export),
]


@pytest.mark.parametrize(
    "url,seed_fn",
    [(url, fn) for _, url, fn in READ_SWEEP],
    ids=[label for label, _, _ in READ_SWEEP],
)
def test_read_route_excludes_other_users_rows(mu_db, url, seed_fn):
    """As user 2: a user-1-owned row never appears in the body, while user 2's
    own row does (proves the route is scoped, not merely emptied)."""
    mark_u1, mark_u2 = seed_fn(mu_db)
    body = client_as(2).get(url).get_data(as_text=True)
    assert mark_u1 not in body
    assert mark_u2 in body


# --- WRITE sweep: single-row actions must 404 + leave the row untouched -----

def _seed_dlc_review_u1(conn) -> int:
    return seed_dlc_review(conn, user_id=1, addon_title="HijackDLCReview")


def _dlc_review_untouched(conn, rid: int) -> bool:
    r = conn.execute(
        "SELECT resolved_at, dismissed_at FROM dlc_review_queue WHERE id = ?",
        (rid,)).fetchone()
    return r is not None and r["resolved_at"] is None and r["dismissed_at"] is None


def _seed_bundle_review_u1(conn) -> int:
    return seed_bundle_review(conn, user_id=1, game_title="HijackBundleReview")


def _bundle_review_untouched(conn, rid: int) -> bool:
    r = conn.execute(
        "SELECT resolved_at, dismissed_at FROM bundle_review_queue WHERE id = ?",
        (rid,)).fetchone()
    return r is not None and r["resolved_at"] is None and r["dismissed_at"] is None


def _seed_upc_review_u1(conn) -> int:
    gid = seed_game(conn, user_id=1, title="HijackUPCGame")
    return seed_upc_review(conn, gid)


def _upc_review_untouched(conn, rid: int) -> bool:
    r = conn.execute("SELECT status FROM upc_review WHERE id = ?", (rid,)).fetchone()
    return r is not None and r["status"] == "pending"


WRITE_SWEEP = [
    ("dlc_review_resolve", "/api/dlc/review/{id}/resolve", {"create_new_dlc": True},
     _seed_dlc_review_u1, _dlc_review_untouched),
    ("dlc_review_dismiss", "/api/dlc/review/{id}/dismiss", {},
     _seed_dlc_review_u1, _dlc_review_untouched),
    ("bundle_review_approve", "/api/bundle-review/{id}/approve", {},
     _seed_bundle_review_u1, _bundle_review_untouched),
    ("bundle_review_dismiss", "/api/bundle-review/{id}/dismiss", {},
     _seed_bundle_review_u1, _bundle_review_untouched),
    ("enrichment_confirm", "/api/enrichment/review/{id}/confirm", {},
     _seed_upc_review_u1, _upc_review_untouched),
    ("enrichment_reject", "/api/enrichment/review/{id}/reject", {},
     _seed_upc_review_u1, _upc_review_untouched),
]


@pytest.mark.parametrize(
    "url_template,body,seed_fn,check_fn",
    [(url, body, seed, check) for _, url, body, seed, check in WRITE_SWEEP],
    ids=[label for label, _, _, _, _ in WRITE_SWEEP],
)
def test_write_route_404_for_other_users_row(mu_db, url_template, body, seed_fn, check_fn):
    """As user 2: a single-row action targeting a user-1-owned row 404s (never
    403) and leaves the row untouched."""
    rid = seed_fn(mu_db)
    resp = client_as(2).post(url_template.format(id=rid), json=body)
    assert resp.status_code == 404
    assert check_fn(mu_db, rid)


# --- Numeric read routes (counts/aggregates reflect ONLY the acting user) ---

def test_dlc_review_count_is_per_user(mu_db):
    seed_dlc_review(mu_db, user_id=1, addon_title="U1-open")
    seed_dlc_review(mu_db, user_id=2, addon_title="U2-open")
    assert client_as(2).get("/api/dlc/review/count").get_json()["count"] == 1


def test_stats_reflect_only_acting_users_library(mu_db):
    seed_game(mu_db, user_id=1, title="A-One")
    seed_game(mu_db, user_id=1, title="A-Two")
    seed_game(mu_db, user_id=2, title="B-One")
    stats = client_as(2).get("/api/stats").get_json()
    assert stats["total_games"] == 1                     # only user 2's game
    assert stats["by_status"].get("backlog") == 1


# --- The picked_game_id injection (hole B) - NOT surfaced by a naive sweep ---

def test_resolve_cannot_inject_another_users_picked_game_id(mu_db):
    """User 2 owns the review row (so the row scope passes) but supplies user 1's
    game as picked_game_id: the ownership check must reject it (404) and write no
    DLC onto user 1's game."""
    a_game = seed_game(mu_db, user_id=1, title="A-Victim")
    rid = seed_dlc_review(mu_db, user_id=2, addon_title="Injected Pass")
    resp = client_as(2).post(f"/api/dlc/review/{rid}/resolve", json={"game_id": a_game})
    assert resp.status_code == 404
    n = mu_db.execute("SELECT COUNT(*) FROM dlc WHERE game_id = ?", (a_game,)).fetchone()[0]
    assert n == 0


def test_resolve_cannot_inject_another_users_picked_dlc_id(mu_db):
    """Same injection via picked_dlc_id: user 2's review row, but the picked DLC
    hangs off user 1's game - rejected (404), user 1's DLC left un-owned."""
    a_game = seed_game(mu_db, user_id=1, title="A-Victim2")
    a_dlc = seed_dlc(mu_db, a_game, "A-Season-Pass", owned=0)
    rid = seed_dlc_review(mu_db, user_id=2, addon_title="Injected Pass 2")
    resp = client_as(2).post(f"/api/dlc/review/{rid}/resolve", json={"dlc_id": a_dlc})
    assert resp.status_code == 404
    owned = mu_db.execute("SELECT owned FROM dlc WHERE id = ?", (a_dlc,)).fetchone()["owned"]
    assert owned == 0


# --- Positive controls: shared / global data stays visible to BOTH users ----

def test_positive_platforms_visible_to_both_users(mu_db):
    """platforms is a global catalog (no user_id) - both users see it."""
    p1 = {p["short_name"] for p in client_as(1).get("/api/platforms").get_json()}
    p2 = {p["short_name"] for p in client_as(2).get("/api/platforms").get_json()}
    assert "Switch" in p1 and "Switch" in p2


def test_positive_collections_catalog_visible_when_user_owns_member(mu_db):
    """The collections CATALOG is shared; a user sees any collection their own
    games belong to (scoping must not over-restrict shared data)."""
    b_game = seed_game(mu_db, user_id=2, title="B-CollGame")
    seed_collection_membership(mu_db, b_game, 39, "Final Fantasy")
    cols = client_as(2).get("/api/collections").get_json()["collections"]
    assert 39 in {c["id"] for c in cols}


def test_positive_barcode_registry_identity_visible_to_both(mu_db):
    """barcode_registry is a SHARED global UPC->identity cache: the same scanned
    UPC resolves to the same title/igdb for both users (ownership is derived
    per-user, but identity is shared)."""
    seed_registry(mu_db, upc="99887766", igdb_id=4242,
                  title="Shared Registry Title", platform="PS")
    for uid in (1, 2):
        body = client_as(uid).get("/api/barcode/resolve?upc=99887766").get_json()
        assert body["source"] == "cache"
        assert body["candidates"][0]["title"] == "Shared Registry Title"
