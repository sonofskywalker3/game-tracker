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
    assert own.remainder("The Witcher 3 - Hearts of Stone", "the witcher 3") == "hearts of stone"
    assert own.remainder("Celeste", "celeste") == ""


# --- match_equal (equality only; no containment) ---

def test_remainder_strips_shared_game_prefix():
    # PS4 store titles use a SHORT game prefix that isn't the full parent title;
    # the remainder must still reduce to the same DLC name as the full-title form.
    assert own.remainder("Ys VIII - Bottled Potion Set",
                         "ys viii lacrimosa of dana") == "bottled potion set"
    assert own.remainder("Ys VIII: Lacrimosa of DANA - Bottled Potion Set",
                         "ys viii lacrimosa of dana") == "bottled potion set"


def _ext(conn, gid, source, external_id):
    conn.execute("INSERT INTO game_external_ids (game_id, source, external_id) VALUES (?,?,?)",
                 (gid, source, external_id))


def test_ps4_ps5_dlc_prefix_variants_reconcile_to_one_row(temp_db):
    """Same DLC owned on PS4 and PS5 has different store-title prefixes
    ('Ys VIII: Lacrimosa of DANA - X' vs 'Ys VIII - X'). Both must collapse to a
    single DLC row carrying both vendor ids, regardless of import order."""
    for first_ps4 in (False, True):
        conn = models.get_db()
        conn.execute("DELETE FROM dlc")
        conn.execute("DELETE FROM dlc_external_ids")
        conn.execute("DELETE FROM games")
        conn.execute("DELETE FROM game_external_ids")
        gid = _seed(conn, title="Ys VIII: Lacrimosa of Dana", dlc_names=())
        _ext(conn, gid, "playstation", "UP1063-PPSA06812_00-0000000000000000")  # PS5 base
        _ext(conn, gid, "playstation", "UP1063-CUSA08565_00-0000000000000000")  # PS4 base
        conn.commit()
        ps5 = _addon("Ys VIII: Lacrimosa of DANA - Bottled Potion Set",
                     source="playstation", external_id="UP1063-PPSA06812_00-YS08JPDLC00N0062")
        ps4 = _addon("Ys VIII - Bottled Potion Set",
                     source="playstation", external_id="UP1063-CUSA08565_00-YSVIIIDLC00N0062")
        own.mark_ownership(conn, [ps4, ps5] if first_ps4 else [ps5, ps4])
        conn.commit()
        n_dlc = conn.execute("SELECT COUNT(*) FROM dlc WHERE game_id=?", (gid,)).fetchone()[0]
        n_ext = conn.execute("SELECT COUNT(*) FROM dlc_external_ids").fetchone()[0]
        conn.close()
        assert n_dlc == 1, f"first_ps4={first_ps4}: expected 1 DLC row, got {n_dlc}"
        assert n_ext == 2, f"first_ps4={first_ps4}: expected 2 vendor ids, got {n_ext}"


def test_dedup_dlc_merges_prefix_variant_duplicates(temp_db):
    conn = models.get_db()
    gid = _seed(conn, title="Ys VIII: Lacrimosa of Dana", dlc_names=())
    conn.execute("INSERT INTO dlc (game_id,name,source,owned) VALUES (?,?, 'igdb',0)",
                 (gid, "Bottled Potion Set"))
    clean_id = conn.execute("SELECT id FROM dlc WHERE name='Bottled Potion Set'").fetchone()[0]
    conn.execute("INSERT INTO dlc (game_id,name,source,owned) VALUES (?,?, 'playstation',1)",
                 (gid, "Ys VIII - Bottled Potion Set"))
    pref_id = conn.execute("SELECT id FROM dlc WHERE name LIKE 'Ys VIII%'").fetchone()[0]
    conn.execute("INSERT INTO dlc_external_ids (dlc_id,source,external_id) VALUES (?,?,?)",
                 (clean_id, "playstation", "UP1063-PPSA06812_00-YS08JPDLC00N0062"))
    conn.execute("INSERT INTO dlc_external_ids (dlc_id,source,external_id) VALUES (?,?,?)",
                 (pref_id, "playstation", "UP1063-CUSA08565_00-YSVIIIDLC00N0062"))
    conn.commit()
    report = own.dedup_dlc(conn, dry_run=False)
    conn.commit()
    rows = conn.execute("SELECT id,name,owned FROM dlc WHERE game_id=?", (gid,)).fetchall()
    assert len(rows) == 1                          # collapsed to one row
    assert rows[0]["name"] == "Bottled Potion Set"  # IGDB-named clean survivor kept
    assert rows[0]["owned"] == 1                    # ownership OR'd in
    assert conn.execute("SELECT COUNT(*) FROM dlc_external_ids WHERE dlc_id=?",
                        (rows[0]["id"],)).fetchone()[0] == 2   # both vendor ids moved over
    assert len(report) == 1
    conn.close()


def test_dedup_dlc_dry_run_writes_nothing(temp_db):
    conn = models.get_db()
    gid = _seed(conn, title="Ys VIII: Lacrimosa of Dana", dlc_names=())
    conn.execute("INSERT INTO dlc (game_id,name,source,owned) VALUES (?,?, 'igdb',0)",
                 (gid, "Bottled Potion Set"))
    conn.execute("INSERT INTO dlc (game_id,name,source,owned) VALUES (?,?, 'playstation',1)",
                 (gid, "Ys VIII - Bottled Potion Set"))
    conn.commit()
    report = own.dedup_dlc(conn, dry_run=True)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM dlc WHERE game_id=?", (gid,)).fetchone()[0] == 2
    assert len(report) == 1 and report[0]["dropped"] == ["Ys VIII - Bottled Potion Set"]
    conn.close()


def test_dedup_dlc_leaves_distinct_dlc_alone(temp_db):
    conn = models.get_db()
    gid = _seed(conn, title="Dying Light", dlc_names=())
    for name in ("The Following", "Hellraid", "Cuisine & Cargo"):
        conn.execute("INSERT INTO dlc (game_id,name,source,owned) VALUES (?,?, 'igdb',0)", (gid, name))
    conn.commit()
    report = own.dedup_dlc(conn, dry_run=False)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM dlc WHERE game_id=?", (gid,)).fetchone()[0] == 3
    assert report == []
    conn.close()


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
    # second run must hit the id-first path (a), not name-equality again
    rep2 = own.mark_ownership(conn, [_addon("The Witcher 3: Wild Hunt - Hearts of Stone")])
    conn.commit()
    assert rep2.marked == 0 and rep2.already_owned == 1
    assert conn.execute("SELECT COUNT(*) FROM dlc_external_ids").fetchone()[0] == 1
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
    ext = conn.execute(
        "SELECT dlc_id FROM dlc_external_ids WHERE source='nintendo' AND external_id='A1'"
    ).fetchone()
    assert ext is not None  # ext_id recorded even when the row was already owned
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


def test_create_collision_falls_back_to_reconcile(temp_db, monkeypatch):
    conn = models.get_db()
    gid = _seed(conn, dlc_names=("Bonus Pack",))  # existing igdb row, owned=0
    conn.commit()
    # Force the create branch to produce a name that collides with the existing row
    # while the add-on remainder ("mystery") won't equality-match it (path b misses).
    monkeypatch.setattr(own, "_clean_remainder", lambda addon, parent: "Bonus Pack")
    rep = own.mark_ownership(conn, [_addon("The Witcher 3: Wild Hunt - Mystery")])
    conn.commit()
    assert rep.created == 0 and rep.marked == 1 and rep.reconciled == 1
    assert conn.execute("SELECT COUNT(*) FROM dlc WHERE game_id=?", (gid,)).fetchone()[0] == 1
    assert conn.execute("SELECT owned FROM dlc WHERE name='Bonus Pack'").fetchone()[0] == 1
    ext = conn.execute(
        "SELECT external_id FROM dlc_external_ids WHERE source='nintendo'").fetchone()
    assert ext["external_id"] == "A1"
    conn.close()
