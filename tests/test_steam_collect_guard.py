"""steam.collect fails loudly without Web API creds (desktop app shows the note)."""
import pytest

from scrapers import steam


def test_collect_raises_without_credentials(monkeypatch) -> None:
    monkeypatch.setattr(steam.config, "get_steam_credentials", lambda: (None, None))
    with pytest.raises(RuntimeError, match="Steam Web API key"):
        steam.collect(page=None, captured=[])
