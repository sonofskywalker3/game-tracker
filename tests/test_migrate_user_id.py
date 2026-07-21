import sqlite3
import models


def _legacy_games_db():
    """A pre-migration games table with the old single-column UNIQUE."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE games (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
        normalized_title TEXT NOT NULL, UNIQUE(normalized_title))""")
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('Celeste','celeste')")
    conn.commit()
    models.migrate_users(conn)
    return conn


def test_backfills_user_id_to_owner():
    conn = _legacy_games_db()
    models.migrate_add_user_id_games(conn)
    assert conn.execute("SELECT user_id FROM games WHERE normalized_title='celeste'").fetchone()["user_id"] == 1


def test_two_users_can_own_same_title():
    conn = _legacy_games_db()
    models.migrate_add_user_id_games(conn)
    conn.execute("INSERT INTO users (google_sub,email) VALUES ('s','t@e.com')")
    uid = conn.execute("SELECT id FROM users WHERE email='t@e.com'").fetchone()["id"]
    conn.execute("INSERT INTO games (title,normalized_title,user_id) VALUES ('Celeste','celeste',?)", (uid,))
    conn.commit()  # must NOT raise UNIQUE violation
    assert conn.execute("SELECT COUNT(*) c FROM games WHERE normalized_title='celeste'").fetchone()["c"] == 2


def test_idempotent():
    conn = _legacy_games_db()
    models.migrate_add_user_id_games(conn)
    models.migrate_add_user_id_games(conn)  # second run is a no-op, no raise


def _matured_games_db():
    """A real-world games table: day-one columns PLUS several later
    ALTER TABLE ... ADD COLUMN migrations layered on top (collection_name,
    needs_igdb_review, hltb_id). Reproduces the shape of the owner's live DB,
    where migrate_add_user_id_games runs long after those ALTERs happened."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE games (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        normalized_title TEXT NOT NULL,
        cover_url TEXT,
        metacritic_score INTEGER,
        opencritic_score INTEGER,
        igdb_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        psn_addons_synced_at TIMESTAMP,
        UNIQUE(normalized_title))""")
    conn.execute("ALTER TABLE games ADD COLUMN collection_name TEXT")
    conn.execute("ALTER TABLE games ADD COLUMN needs_igdb_review INTEGER NOT NULL DEFAULT 0")
    conn.execute("ALTER TABLE games ADD COLUMN hltb_id TEXT")
    conn.execute(
        "INSERT INTO games (title, normalized_title, collection_name, "
        "needs_igdb_review, hltb_id) VALUES "
        "('Celeste','celeste','Matt Makes Games Bundle',1,'12345')"
    )
    conn.commit()
    models.migrate_users(conn)
    return conn


def test_matured_db_with_alter_added_columns_survives_rebuild():
    """The rebuild must preserve every ALTER-added column, not just the
    original 9 from init_db(). This is the exact shape of the owner's real
    games.db and is what a fresh init_db()-created test DB never exercises."""
    conn = _matured_games_db()
    models.migrate_add_user_id_games(conn)

    row = conn.execute(
        "SELECT user_id, collection_name, needs_igdb_review, hltb_id "
        "FROM games WHERE normalized_title='celeste'"
    ).fetchone()
    assert row["user_id"] == 1
    assert row["collection_name"] == "Matt Makes Games Bundle"
    assert row["needs_igdb_review"] == 1
    assert row["hltb_id"] == "12345"

    tables = [
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]
    assert "games_new" not in tables


def _legacy_roots_db():
    """A pre-migration tags/slots/decider_chats set: tags with the old
    single-column UNIQUE(name), slots and decider_chats with no user_id."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, "
        "category TEXT)"
    )
    conn.execute("CREATE TABLE slots (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("CREATE TABLE decider_chats (id INTEGER PRIMARY KEY, game_id INTEGER)")
    conn.commit()
    models.migrate_users(conn)
    return conn


def test_roots_get_user_id_backfilled_to_owner():
    conn = _legacy_roots_db()
    conn.execute("INSERT INTO tags (name) VALUES ('favorites')")
    conn.commit()
    models.migrate_add_user_id_roots(conn)
    assert conn.execute("SELECT user_id FROM tags WHERE name='favorites'").fetchone()["user_id"] == 1
    # two users can each have a 'favorites' tag
    conn.execute("INSERT INTO users (google_sub,email) VALUES ('s','t@e.com')")
    uid = conn.execute("SELECT id FROM users WHERE email='t@e.com'").fetchone()["id"]
    conn.execute("INSERT INTO tags (name,user_id) VALUES ('favorites',?)", (uid,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) c FROM tags WHERE name='favorites'").fetchone()["c"] == 2


def test_slots_and_decider_chats_get_user_id_backfilled():
    conn = _legacy_roots_db()
    conn.execute("INSERT INTO slots (name) VALUES ('Evening')")
    conn.execute("INSERT INTO decider_chats (game_id) VALUES (7)")
    conn.commit()
    models.migrate_add_user_id_roots(conn)
    assert conn.execute("SELECT user_id FROM slots WHERE name='Evening'").fetchone()["user_id"] == 1
    assert conn.execute("SELECT user_id FROM decider_chats WHERE game_id=7").fetchone()["user_id"] == 1


def test_add_user_id_col_survives_foreign_keys_on_with_existing_rows():
    """Regression: get_db() always runs with foreign_keys=ON (models.py:151),
    and slots/decider_chats are never empty on a real DB. SQLite refuses to
    ADD COLUMN ... REFERENCES ... with a non-NULL default under exactly that
    combination (FK enforcement on + pre-existing rows), so this must not
    depend on being called after another migration happens to leave
    foreign_keys OFF or ON."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE slots (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO slots (name) VALUES ('Evening')")
    conn.commit()
    models.migrate_users(conn)
    conn.execute("PRAGMA foreign_keys=ON")
    models._add_user_id_col(conn, "slots")  # must not raise
    assert conn.execute("SELECT user_id FROM slots WHERE name='Evening'").fetchone()["user_id"] == 1


def test_tags_rebuild_preserves_data_and_rejects_per_user_dupes():
    conn = _legacy_roots_db()
    conn.execute("INSERT INTO tags (id, name, category) VALUES (42, 'RPG', 'genre')")
    conn.commit()
    models.migrate_add_user_id_roots(conn)

    row = conn.execute("SELECT id, name, category, user_id FROM tags WHERE name='RPG'").fetchone()
    assert row["id"] == 42
    assert row["category"] == "genre"
    assert row["user_id"] == 1

    try:
        conn.execute("INSERT INTO tags (name, category, user_id) VALUES ('RPG', 'genre', 1)")
        conn.commit()
        assert False, "expected IntegrityError for duplicate (user_id, name)"
    except sqlite3.IntegrityError:
        pass


def test_roots_migration_idempotent():
    conn = _legacy_roots_db()
    conn.execute("INSERT INTO tags (name) VALUES ('favorites')")
    conn.commit()
    models.migrate_add_user_id_roots(conn)
    models.migrate_add_user_id_roots(conn)  # second run is a no-op, no raise
    assert conn.execute("SELECT COUNT(*) c FROM tags").fetchone()["c"] == 1


def _matured_roots_db():
    """A tags table with an extra ALTER-added column, seeded before the
    user_id rebuild — reproduces the shape of a real, previously-migrated DB."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, "
        "category TEXT)"
    )
    conn.execute("ALTER TABLE tags ADD COLUMN color TEXT")
    conn.execute(
        "INSERT INTO tags (name, category, color) VALUES ('Roguelike', 'genre', '#ff0000')"
    )
    conn.execute("CREATE TABLE slots (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("CREATE TABLE decider_chats (id INTEGER PRIMARY KEY, game_id INTEGER)")
    conn.commit()
    models.migrate_users(conn)
    return conn


def test_tags_matured_schema_with_alter_added_columns_survives_rebuild():
    conn = _matured_roots_db()
    models.migrate_add_user_id_roots(conn)

    row = conn.execute(
        "SELECT user_id, category, color FROM tags WHERE name='Roguelike'"
    ).fetchone()
    assert row["user_id"] == 1
    assert row["category"] == "genre"
    assert row["color"] == "#ff0000"

    tables = [
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]
    assert "tags_new" not in tables


def _profile_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    models.migrate_users(conn)
    models.migrate_user_profile(conn)
    return conn


def test_user_profile_backfilled_to_owner_and_per_user_unique():
    conn = _profile_db()
    models.migrate_user_profile_per_user(conn)

    row = conn.execute("SELECT user_id FROM user_profile WHERE id = 1").fetchone()
    assert row["user_id"] == 1

    # a second user can insert their own profile row
    conn.execute("INSERT INTO users (google_sub,email) VALUES ('s','t@e.com')")
    uid = conn.execute("SELECT id FROM users WHERE email='t@e.com'").fetchone()["id"]
    conn.execute("INSERT INTO user_profile (id, user_id) VALUES (2, ?)", (uid,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) c FROM user_profile").fetchone()["c"] == 2

    # a second row for user_id=1 violates UNIQUE(user_id)
    try:
        conn.execute("INSERT INTO user_profile (id, user_id) VALUES (3, 1)")
        conn.commit()
        assert False, "expected IntegrityError for duplicate user_id"
    except sqlite3.IntegrityError:
        pass


def test_user_profile_migration_idempotent():
    conn = _profile_db()
    models.migrate_user_profile_per_user(conn)
    models.migrate_user_profile_per_user(conn)  # second run is a no-op, no raise
    assert conn.execute("SELECT COUNT(*) c FROM user_profile").fetchone()["c"] == 1


def test_user_profile_matured_schema_survives_rebuild():
    """collection_display_mode is an ALTER-added column (models.py ~1427);
    the rebuild must preserve it and its data, not just the day-one columns."""
    conn = _profile_db()
    conn.execute(
        "UPDATE user_profile SET work_start_min = 540, collection_display_mode = 'grid' "
        "WHERE id = 1"
    )
    conn.commit()
    models.migrate_user_profile_per_user(conn)

    row = conn.execute(
        "SELECT user_id, work_start_min, collection_display_mode FROM user_profile WHERE id = 1"
    ).fetchone()
    assert row["user_id"] == 1
    assert row["work_start_min"] == 540
    assert row["collection_display_mode"] == "grid"

    tables = [
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]
    assert "user_profile_new" not in tables
