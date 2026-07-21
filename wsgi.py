# wsgi.py
"""Gunicorn entrypoint: `gunicorn wsgi:app`. Exposes the Flask app object.

DB schema migration is an explicit deploy step (the systemd unit runs
`ensure_db()` before starting gunicorn), NOT an import-time side effect —
importing this module must be free of side effects so tests and tooling can
import it safely.
"""
from app import app, check_oauth_config, ensure_db, check_session_secret

check_oauth_config()
check_session_secret()

if __name__ == "__main__":
    ensure_db()
    app.run()
