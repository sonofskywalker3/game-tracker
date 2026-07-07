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
