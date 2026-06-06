import types
import models
import decider


class FakeMessages:
    def __init__(self, text):
        self.text = text
        self.captured = {}

    def create(self, **kwargs):
        self.captured = kwargs
        block = types.SimpleNamespace(type="text", text=self.text)
        return types.SimpleNamespace(
            content=[block],
            usage=types.SimpleNamespace(cache_read_input_tokens=0, cache_creation_input_tokens=10))


class FakeClient:
    def __init__(self, text):
        self.messages = FakeMessages(text)


def _slot(conn):
    conn.execute("INSERT INTO slots (label, sort_order, platforms, max_session_minutes) "
                 "VALUES ('Quick', 9, '[\"Switch\"]', 60)")
    conn.commit()
    return dict(conn.execute("SELECT * FROM slots ORDER BY id DESC LIMIT 1").fetchone())


def _add(conn, title):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO user_ratings (game_id, status) VALUES (?, 'backlog')", (gid,))
    conn.commit()
    return gid


def test_decide_request_shape_and_parse(temp_db):
    conn = models.get_db()
    gid = _add(conn, "Hades")
    slot = _slot(conn)
    fake = FakeClient(f"Try Hades.\n<suggestions>{gid}</suggestions>")
    out = decider.decide(conn, slot, [{"role": "user", "content": "quick fun thing?"}],
                         client=fake, model="claude-sonnet-4-6")
    cap = fake.messages.captured
    assert cap["model"] == "claude-sonnet-4-6"
    assert cap["system"][1]["cache_control"] == {"type": "ephemeral"}
    assert cap["messages"][0]["role"] == "user" and "SLOT" in cap["messages"][0]["content"]
    assert cap["messages"][-1]["content"] == "quick fun thing?"
    assert out["reply"] == "Try Hades." and out["suggestions"] == [gid]
    conn.close()


def test_decide_drops_finished_and_dropped_from_suggestions(temp_db):
    conn = models.get_db()
    done = _add(conn, "Final Fantasy X")
    conn.execute("UPDATE user_ratings SET status='completed' WHERE game_id=?", (done,))
    dropped = _add(conn, "Abandoned Game")
    conn.execute("UPDATE user_ratings SET status='dropped' WHERE game_id=?", (dropped,))
    live = _add(conn, "Hades")
    conn.commit()
    slot = _slot(conn)
    fake = FakeClient(f"Some options.\n<suggestions>{done},{dropped},{live}</suggestions>")
    out = decider.decide(conn, slot, [{"role": "user", "content": "what should I play?"}],
                         client=fake, model="claude-sonnet-4-6")
    assert out["suggestions"] == [live]   # finished + dropped removed
    conn.close()


def test_decide_keeps_finished_when_user_wants_to_100(temp_db):
    conn = models.get_db()
    done = _add(conn, "Final Fantasy X")
    conn.execute("UPDATE user_ratings SET status='completed' WHERE game_id=?", (done,))
    conn.commit()
    slot = _slot(conn)
    fake = FakeClient(f"Go for it.\n<suggestions>{done}</suggestions>")
    out = decider.decide(conn, slot, [{"role": "user", "content": "I want to 100% something"}],
                         client=fake, model="claude-sonnet-4-6")
    assert out["suggestions"] == [done]   # explicit replay intent -> allowed
    conn.close()


def test_decide_no_key_returns_error(temp_db, monkeypatch):
    conn = models.get_db()
    slot = _slot(conn)
    monkeypatch.setattr(decider.config, "get_anthropic_config", lambda: (None, "claude-sonnet-4-6"))
    out = decider.decide(conn, slot, [{"role": "user", "content": "hi"}])
    assert out["error"] == "no_api_key"
    conn.close()
