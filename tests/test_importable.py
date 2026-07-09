import importlib


def test_app_imports_without_browser():
    """The cloud box installs the playwright package but not the browser
    binaries; importing the app (and its scrape imports) must not require them."""
    mod = importlib.import_module("app")
    assert hasattr(mod, "app")
    assert hasattr(mod, "ensure_db")


def test_wsgi_exposes_app():
    wsgi = importlib.import_module("wsgi")
    assert wsgi.app is importlib.import_module("app").app
