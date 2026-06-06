import decider
import models


def _game(conn, title="Hades"):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    return gid


def test_save_and_list_decider_chats(temp_db):
    conn = models.get_db()
    gid = _game(conn)
    msgs = [{"role": "user", "content": "quick fun thing?"},
            {"role": "assistant", "content": "Try Hades."}]
    cid = decider.save_chat(conn, gid, slot_id=3, slot_label="Quick", messages=msgs)
    conn.commit()
    assert cid and cid > 0
    chats = decider.list_chats(conn, gid)
    assert len(chats) == 1
    assert chats[0]["slot_label"] == "Quick"
    assert chats[0]["messages"] == msgs
    assert chats[0]["created_at"]
    conn.close()


def test_save_chat_strips_non_dialogue_and_skips_empty(temp_db):
    conn = models.get_db()
    gid = _game(conn, "X")
    # notices / empty content are not real dialogue -> dropped; empty save -> None
    assert decider.save_chat(conn, gid, None, None,
                             [{"role": "notice", "content": "<p>err</p>"},
                              {"role": "user", "content": ""}]) is None
    assert decider.save_chat(conn, gid, None, None, []) is None
    assert decider.list_chats(conn, gid) == []
    conn.close()


def test_list_chats_newest_first(temp_db):
    conn = models.get_db()
    gid = _game(conn)
    decider.save_chat(conn, gid, 1, "A", [{"role": "user", "content": "first"}])
    decider.save_chat(conn, gid, 2, "B", [{"role": "user", "content": "second"}])
    conn.commit()
    labels = [c["slot_label"] for c in decider.list_chats(conn, gid)]
    assert labels == ["B", "A"]   # newest first
    conn.close()
