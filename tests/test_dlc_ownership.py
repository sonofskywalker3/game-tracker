import dlc_ownership as own
import models


def _lib(*titles):
    """Build a [(game_id, normalized_title)] library from display titles."""
    return [(i + 1, models.normalize_title(models.clean_title(t))) for i, t in enumerate(titles)]


def test_parent_of_exact_prefix():
    lib = _lib("The Witcher 3: Wild Hunt", "Other Game")
    assert own.parent_of("The Witcher 3: Wild Hunt - Hearts of Stone", lib) == 1


def test_parent_of_longest_prefix_wins():
    lib = _lib("Final Fantasy", "Final Fantasy XV")
    # game_id 2 ("final fantasy xv") is the longer prefix of the add-on
    assert own.parent_of("Final Fantasy XV - Episode Ardyn", lib) == 2


def test_parent_of_no_prefix_is_none():
    lib = _lib("Hades", "Celeste")
    assert own.parent_of("Stardew Valley - Some Pack", lib) is None


def test_parent_of_cross_game_tie_is_ambiguous():
    # Two different games normalize to the same prefix string.
    lib = [(1, "spirit"), (2, "spirit")]
    assert own.parent_of("Spirit Extra Pack", lib) is own.AMBIGUOUS


def test_parent_of_exact_title_match():
    lib = _lib("Celeste")
    assert own.parent_of("Celeste", lib) == 1


def test_remainder_strips_parent_prefix():
    assert own._remainder("The Witcher 3 - Hearts of Stone", "the witcher 3") == "hearts of stone"
    assert own._remainder("Celeste", "celeste") == ""


def test_match_dlc_equality():
    rows = [(10, "Hearts of Stone"), (11, "Blood and Wine")]
    assert own.match_dlc("hearts of stone", rows) == (10, "equality")


def test_match_dlc_containment():
    rows = [(10, "Hearts of Stone")]
    # add-on remainder carries extra words around the dlc name
    assert own.match_dlc("hearts of stone expansion", rows) == (10, "containment")


def test_match_dlc_multiple_equality_is_ambiguous():
    rows = [(10, "Season Pass"), (11, "Season Pass")]
    assert own.match_dlc("season pass", rows) == (own.AMBIGUOUS, "equality")


def test_match_dlc_none():
    rows = [(10, "Hearts of Stone")]
    assert own.match_dlc("totally different", rows) == (None, None)


def test_match_dlc_empty_remainder():
    assert own.match_dlc("", [(10, "X")]) == (None, None)


def test_classify_apply_on_unique_parent_and_equality():
    lib = _lib("The Witcher 3: Wild Hunt")
    dlc_by_game = {1: [(10, "Hearts of Stone")]}
    m = own.classify("The Witcher 3: Wild Hunt - Hearts of Stone", lib, dlc_by_game)
    assert m.action == "apply" and m.game_id == 1 and m.dlc_id == 10


def test_classify_hold_on_containment_only():
    lib = _lib("The Witcher 3: Wild Hunt")
    dlc_by_game = {1: [(10, "Hearts of Stone")]}
    m = own.classify("The Witcher 3: Wild Hunt - Hearts of Stone Expansion", lib, dlc_by_game)
    assert m.action == "hold" and m.dlc_id == 10


def test_classify_hold_on_ambiguous_parent():
    lib = [(1, "spirit"), (2, "spirit")]
    m = own.classify("Spirit Extra Pack", lib, {1: [(10, "extra pack")]})
    assert m.action == "hold" and m.game_id is None


def test_classify_unmatched_no_parent():
    m = own.classify("Stardew Valley - Pack", _lib("Hades"), {})
    assert m.action == "unmatched" and m.game_id is None


def test_classify_unmatched_parent_without_dlc():
    lib = _lib("Hades")
    m = own.classify("Hades - Soundtrack", lib, {})
    assert m.action == "unmatched" and m.game_id == 1


def test_classify_unmatched_no_dlc_name_match():
    lib = _lib("Hades")
    m = own.classify("Hades - Soundtrack", lib, {1: [(10, "Cosmetic Pack")]})
    assert m.action == "unmatched" and m.game_id == 1


def _seed(conn, title="The Witcher 3: Wild Hunt", dlc_names=("Hearts of Stone",)):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(models.clean_title(title))))
    gid = conn.execute("SELECT id FROM games WHERE title=?", (title,)).fetchone()[0]
    for name in dlc_names:
        conn.execute("INSERT INTO dlc (game_id, name, source) VALUES (?, ?, 'igdb')", (gid, name))
    return gid


def test_mark_ownership_applies_equality_match(temp_db):
    conn = models.get_db()
    gid = _seed(conn)
    conn.commit()
    rep = own.mark_ownership(conn, [{"title": "The Witcher 3: Wild Hunt - Hearts of Stone"}])
    conn.commit()
    assert rep.marked == 1 and not rep.held and not rep.unmatched
    owned = conn.execute("SELECT owned FROM dlc WHERE game_id=? AND name='Hearts of Stone'",
                         (gid,)).fetchone()[0]
    assert owned == 1
    conn.close()


def test_mark_ownership_holds_unless_include_flagged(temp_db):
    conn = models.get_db()
    _seed(conn)
    conn.commit()
    addon = {"title": "The Witcher 3: Wild Hunt - Hearts of Stone Expansion"}  # containment only
    rep = own.mark_ownership(conn, [addon])
    assert rep.marked == 0 and len(rep.held) == 1
    assert conn.execute("SELECT owned FROM dlc").fetchone()[0] == 0
    rep2 = own.mark_ownership(conn, [addon], include_flagged=True)
    conn.commit()
    assert rep2.marked == 1
    assert conn.execute("SELECT owned FROM dlc").fetchone()[0] == 1
    conn.close()


def test_mark_ownership_reports_unmatched_creates_nothing(temp_db):
    conn = models.get_db()
    _seed(conn)
    conn.commit()
    rep = own.mark_ownership(conn, [{"title": "The Witcher 3: Wild Hunt - Soundtrack"}])
    assert rep.marked == 0 and len(rep.unmatched) == 1
    assert conn.execute("SELECT COUNT(*) FROM dlc").fetchone()[0] == 1  # nothing inserted
    conn.close()


def test_mark_ownership_dry_run_writes_nothing(temp_db):
    conn = models.get_db()
    _seed(conn)
    conn.commit()
    rep = own.mark_ownership(conn, [{"title": "The Witcher 3: Wild Hunt - Hearts of Stone"}],
                             dry_run=True)
    assert rep.marked == 1
    assert conn.execute("SELECT owned FROM dlc").fetchone()[0] == 0  # not written
    conn.close()


def test_mark_ownership_idempotent(temp_db):
    conn = models.get_db()
    _seed(conn)
    conn.commit()
    addon = {"title": "The Witcher 3: Wild Hunt - Hearts of Stone"}
    own.mark_ownership(conn, [addon])
    conn.commit()
    rep = own.mark_ownership(conn, [addon])
    conn.commit()
    assert rep.marked == 0 and rep.already_owned == 1
    conn.close()
