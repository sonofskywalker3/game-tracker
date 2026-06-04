import models
import run_igdb_audit


def test_run_audit_reports_applied_and_flagged(temp_db, monkeypatch):
    monkeypatch.setattr(run_igdb_audit.config, "get_twitch_credentials",
                        lambda: ("cid", "secret"))
    monkeypatch.setattr(run_igdb_audit.igdb_dlc, "get_access_token",
                        lambda *a, **k: "tok")
    monkeypatch.setattr(run_igdb_audit.igdb_match, "audit_igdb_matches",
                        lambda conn, **k: {"applied": [1, 2], "flagged": [3]})
    summary = run_igdb_audit.run(dry_run=False)
    assert summary == {"applied": 2, "flagged": 1}


def test_run_audit_dry_run_does_not_persist(temp_db, monkeypatch):
    # A fake audit that actually writes, to prove dry-run rolls the write back.
    conn0 = models.get_db()
    conn0.execute("INSERT INTO games (id,title,normalized_title) VALUES (1,'G','g')")
    conn0.commit()
    conn0.close()

    def fake_audit(conn, **k):
        conn.execute("UPDATE games SET igdb_locked=1 WHERE id=1")
        return {"applied": [1], "flagged": []}

    monkeypatch.setattr(run_igdb_audit.config, "get_twitch_credentials",
                        lambda: ("cid", "secret"))
    monkeypatch.setattr(run_igdb_audit.igdb_dlc, "get_access_token", lambda *a, **k: "tok")
    monkeypatch.setattr(run_igdb_audit.igdb_match, "audit_igdb_matches", fake_audit)

    run_igdb_audit.run(dry_run=True)
    conn = models.get_db()
    locked = conn.execute("SELECT COALESCE(igdb_locked,0) FROM games WHERE id=1").fetchone()[0]
    conn.close()
    assert locked == 0  # dry-run rolled back
