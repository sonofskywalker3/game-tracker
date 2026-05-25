import dlc_ownership as own
import models


def _lib(*titles):
    """Build a [(game_id, normalized_title)] library from display titles."""
    return [(i + 1, models.normalize_title(models.clean_title(t))) for i, t in enumerate(titles)]


# --- parent_of (unchanged behavior) ---

def test_parent_of_exact_prefix():
    lib = _lib("The Witcher 3: Wild Hunt", "Other Game")
    assert own.parent_of("The Witcher 3: Wild Hunt - Hearts of Stone", lib) == 1


def test_parent_of_longest_prefix_wins():
    lib = _lib("Final Fantasy", "Final Fantasy XV")
    assert own.parent_of("Final Fantasy XV - Episode Ardyn", lib) == 2


def test_parent_of_no_prefix_is_none():
    lib = _lib("Hades", "Celeste")
    assert own.parent_of("Stardew Valley - Some Pack", lib) is None


def test_parent_of_cross_game_tie_is_ambiguous():
    lib = [(1, "spirit"), (2, "spirit")]
    assert own.parent_of("Spirit Extra Pack", lib) is own.AMBIGUOUS


def test_parent_of_exact_title_match():
    lib = _lib("Celeste")
    assert own.parent_of("Celeste", lib) == 1


def test_remainder_strips_parent_prefix():
    assert own._remainder("The Witcher 3 - Hearts of Stone", "the witcher 3") == "hearts of stone"
    assert own._remainder("Celeste", "celeste") == ""


# --- match_equal (equality only; no containment) ---

def test_match_equal_single():
    rows = [(10, "Hearts of Stone"), (11, "Blood and Wine")]
    assert own.match_equal("hearts of stone", rows) == 10


def test_match_equal_multiple_is_ambiguous():
    rows = [(10, "Season Pass"), (11, "Season Pass")]
    assert own.match_equal("season pass", rows) is own.AMBIGUOUS


def test_match_equal_none_on_containment():
    rows = [(10, "Hearts of Stone")]
    assert own.match_equal("hearts of stone expansion", rows) is None


def test_match_equal_empty():
    assert own.match_equal("", [(10, "X")]) is None


# --- _clean_remainder (display name for a created row) ---

def test_clean_remainder_strips_parent_prefix():
    assert own._clean_remainder("The Witcher 3: Wild Hunt - Hearts of Stone",
                                "The Witcher 3: Wild Hunt") == "Hearts of Stone"


def test_clean_remainder_falls_back_to_full_title():
    assert own._clean_remainder("Some Bundle Pack", "The Witcher 3") == "Some Bundle Pack"


# --- mark_ownership engine (temp DB) ---

def _seed(conn, title="The Witcher 3: Wild Hunt", dlc_names=("Hearts of Stone",)):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(models.clean_title(title))))
    gid = conn.execute("SELECT id FROM games WHERE title=?", (title,)).fetchone()[0]
    for name in dlc_names:
        conn.execute("INSERT INTO dlc (game_id, name, source) VALUES (?, ?, 'igdb')", (gid, name))
    return gid


def _addon(title, source="nintendo", external_id="A1"):
    return {"title": title, "source": source, "external_id": external_id, "kind": "addon"}


def test_reconcile_by_name_equality_flips_and_records_id(temp_db):
    conn = models.get_db()
    _seed(conn)  # has igdb "Hearts of Stone"
    conn.commit()
    rep = own.mark_ownership(conn, [_addon("The Witcher 3: Wild Hunt - Hearts of Stone")])
    conn.commit()
    assert rep.marked == 1 and rep.reconciled == 1 and rep.created == 0
    assert conn.execute("SELECT owned FROM dlc WHERE name='Hearts of Stone'").fetchone()[0] == 1
    row = conn.execute(
        "SELECT dlc_id FROM dlc_external_ids WHERE source='nintendo' AND external_id='A1'").fetchone()
    assert row is not None
    conn.close()


def test_create_when_no_matching_row(temp_db):
    conn = models.get_db()
    gid = _seed(conn, dlc_names=())  # game exists, no dlc rows (IGDB-missing case)
    conn.commit()
    rep = own.mark_ownership(conn, [_addon("The Witcher 3: Wild Hunt - Ultimate Pack")])
    conn.commit()
    assert rep.created == 1 and rep.marked == 1 and rep.reconciled == 0
    row = conn.execute("SELECT name, owned, source FROM dlc WHERE game_id=?", (gid,)).fetchone()
    assert row["name"] == "Ultimate Pack" and row["owned"] == 1 and row["source"] == "nintendo"
    ext = conn.execute("SELECT external_id FROM dlc_external_ids WHERE source='nintendo'").fetchone()
    assert ext["external_id"] == "A1"
    conn.close()


def test_idempotent_by_id_on_rerun(temp_db):
    conn = models.get_db()
    _seed(conn, dlc_names=())
    conn.commit()
    addon = _addon("The Witcher 3: Wild Hunt - Ultimate Pack")
    own.mark_ownership(conn, [addon])
    conn.commit()
    rep = own.mark_ownership(conn, [addon])
    conn.commit()
    assert rep.created == 0 and rep.marked == 0 and rep.already_owned == 1
    assert conn.execute("SELECT COUNT(*) FROM dlc").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM dlc_external_ids").fetchone()[0] == 1
    conn.close()


def test_uncertain_parent_goes_to_review_no_write(temp_db):
    conn = models.get_db()
    _seed(conn)
    conn.commit()
    rep = own.mark_ownership(conn, [_addon("Totally Unknown Game - Bonus", external_id="Z9")])
    assert rep.marked == 0 and len(rep.review) == 1 and rep.review[0].reason == "no parent game"
    assert conn.execute("SELECT COUNT(*) FROM dlc_external_ids").fetchone()[0] == 0
    assert conn.execute("SELECT owned FROM dlc").fetchone()[0] == 0
    conn.close()


def test_dry_run_writes_nothing(temp_db):
    conn = models.get_db()
    _seed(conn, dlc_names=())
    conn.commit()
    rep = own.mark_ownership(conn, [_addon("The Witcher 3: Wild Hunt - Ultimate Pack")],
                             dry_run=True)
    assert rep.created == 1 and rep.marked == 1
    assert conn.execute("SELECT COUNT(*) FROM dlc").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM dlc_external_ids").fetchone()[0] == 0
    conn.close()


def test_already_owned_not_remarked(temp_db):
    conn = models.get_db()
    gid = _seed(conn)
    conn.execute("UPDATE dlc SET owned=1 WHERE game_id=?", (gid,))  # already owned
    conn.commit()
    rep = own.mark_ownership(conn, [_addon("The Witcher 3: Wild Hunt - Hearts of Stone")])
    conn.commit()
    assert rep.marked == 0 and rep.already_owned == 1
    conn.close()


def test_marked_items_for_result_list(temp_db):
    conn = models.get_db()
    _seed(conn, dlc_names=())
    conn.commit()
    rep = own.mark_ownership(conn, [_addon("The Witcher 3: Wild Hunt - Ultimate Pack")])
    conn.commit()
    assert len(rep.marked_items) == 1
    assert rep.marked_items[0].dlc_id is not None
    conn.close()
