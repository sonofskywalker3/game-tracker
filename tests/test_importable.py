import importlib

import pytest


def test_app_imports_without_browser():
    """The cloud box installs the playwright package but not the browser
    binaries; importing the app (and its scrape imports) must not require them."""
    mod = importlib.import_module("app")
    assert hasattr(mod, "app")
    assert hasattr(mod, "ensure_db")


def test_wsgi_exposes_app():
    wsgi = importlib.import_module("wsgi")
    assert wsgi.app is importlib.import_module("app").app


def test_wsgi_import_fails_closed_on_half_configured_oauth(monkeypatch):
    """Production runs under gunicorn (`gunicorn wsgi:app`), which imports
    wsgi.py and never executes app.py's `if __name__ == '__main__':` block.
    check_oauth_config() must therefore also run at wsgi import time, or a
    half-configured OAuth deploy boots with the auth gate silently open."""
    wsgi = importlib.import_module("wsgi")

    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    try:
        with pytest.raises(RuntimeError):
            importlib.reload(wsgi)
    finally:
        # Clean up before reverting env (monkeypatch teardown happens after
        # this test returns) so wsgi is left import-clean for other tests.
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
        importlib.reload(wsgi)


def test_wsgi_import_succeeds_when_oauth_unconfigured(monkeypatch):
    wsgi = importlib.import_module("wsgi")

    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    importlib.reload(wsgi)  # must not raise
