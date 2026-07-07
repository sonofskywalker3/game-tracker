def test_links_member_to_container(tmp_path):
    import sqlite3

    import models
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.executescript(
        "CREATE TABLE games(id INTEGER PRIMARY KEY, title TEXT, collection_name TEXT);"
        "INSERT INTO games(id,title,collection_name) VALUES"
        " (1,'Mega Man Battle Network Legacy Collection Vol. 1', NULL),"
        " (2,'Mega Man Battle Network', 'Megaman Battle Network Legacy Collection Vol.1'),"
        " (3,'Mega Man', 'Mega Man Legacy Collection');"  # container deleted -> stays NULL
    )
    conn.commit()
    models.migrate_parent_collection(conn)
    models.migrate_parent_collection(conn)  # idempotent
    assert conn.execute("SELECT parent_collection_id FROM games WHERE id=2").fetchone()[0] == 1
    assert conn.execute("SELECT parent_collection_id FROM games WHERE id=3").fetchone()[0] is None
    assert conn.execute("SELECT parent_collection_id FROM games WHERE id=1").fetchone()[0] is None


def test_does_not_link_differ_only_by_number_collision(tmp_path):
    """'Dragon Quest I & II HD-2D Remake' normalizes (all-punctuation-stripped) to
    the same key as 'Dragon Quest III HD-2D Remake' ('I'+'II' -> 'iii' == 'III').
    The standalone DQ III game must NOT become a container that hides itself in
    members display mode; DQ I/II members must stay unlinked."""
    import sqlite3

    import models
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.executescript(
        "CREATE TABLE games(id INTEGER PRIMARY KEY, title TEXT, collection_name TEXT);"
        "INSERT INTO games(id,title,collection_name) VALUES"
        " (1,'Dragon Quest I HD-2D Remake', 'Dragon Quest I & II HD-2D Remake'),"
        " (2,'Dragon Quest II HD-2D Remake', 'Dragon Quest I & II HD-2D Remake'),"
        " (3,'Dragon Quest III HD-2D Remake', NULL);"
    )
    conn.commit()
    models.migrate_parent_collection(conn)
    assert conn.execute("SELECT parent_collection_id FROM games WHERE id=1").fetchone()[0] is None
    assert conn.execute("SELECT parent_collection_id FROM games WHERE id=2").fetchone()[0] is None
    assert conn.execute(
        "SELECT COUNT(*) FROM games WHERE parent_collection_id=3"
    ).fetchone()[0] == 0


def test_links_exact_match_container(tmp_path):
    """Sanity check: an exact-match container (base_keys equal, so
    titles_differ_only_by_number is False) still links normally."""
    import sqlite3

    import models
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.executescript(
        "CREATE TABLE games(id INTEGER PRIMARY KEY, title TEXT, collection_name TEXT);"
        "INSERT INTO games(id,title,collection_name) VALUES"
        " (1,'Advance Wars 1+2: Re-Boot Camp', NULL),"
        " (2,'Advance Wars', 'Advance Wars 1+2: Re-Boot Camp');"
    )
    conn.commit()
    models.migrate_parent_collection(conn)
    assert conn.execute("SELECT parent_collection_id FROM games WHERE id=2").fetchone()[0] == 1
