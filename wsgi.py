# wsgi.py
"""Gunicorn entrypoint: `gunicorn wsgi:app`. Exposes the Flask app object.

DB schema migration is an explicit deploy step (the systemd unit runs
`ensure_db()` before starting gunicorn), NOT an import-time side effect —
importing this module must be free of side effects so tests and tooling can
import it safely.
"""
from app import app, ensure_db

if __name__ == "__main__":
    ensure_db()
    app.run()
