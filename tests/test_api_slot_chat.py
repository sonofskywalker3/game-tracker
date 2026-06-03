import models
import app as app_module


def _client():
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def _make_slot(conn):
    conn.execute("INSERT INTO slots (label, sort_order, platforms) VALUES ('Quick', 9, '[]')")
    conn.commit()
    return conn.execute("SELECT id FROM slots ORDER BY id DESC LIMIT 1").fetchone()[0]


def test_chat_returns_reply_and_resolved_suggestions(temp_db, monkeypatch):
    conn = models.get_db()
    sid = _make_slot(conn)
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (7, 'Hades', 'hades')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(app_module.decider, "decide",
                        lambda *a, **k: {"reply": "Try Hades.", "suggestions": [7]})
    resp = _client().post(f"/api/slots/{sid}/chat",
                          json={"messages": [{"role": "user", "content": "fun?"}]})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["reply"] == "Try Hades."
    assert data["suggestions"][0]["id"] == 7 and data["suggestions"][0]["title"] == "Hades"


def test_chat_no_key_returns_400(temp_db, monkeypatch):
    conn = models.get_db()
    sid = _make_slot(conn)
    conn.close()
    monkeypatch.setattr(app_module.decider, "decide", lambda *a, **k: {"error": "no_api_key"})
    resp = _client().post(f"/api/slots/{sid}/chat", json={"messages": []})
    assert resp.status_code == 400 and resp.get_json()["error"] == "no_api_key"
