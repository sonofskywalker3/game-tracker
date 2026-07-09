# wsgi.py
"""Gunicorn entrypoint: `gunicorn wsgi:app`. Ensures the DB schema is current
before the app serves, then exposes the Flask app object."""
from app import app, ensure_db

ensure_db()

if __name__ == "__main__":
    app.run()
