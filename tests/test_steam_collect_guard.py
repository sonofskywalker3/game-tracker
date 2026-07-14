"""steam.collect fails honestly when there are no config creds AND no session token."""
import pytest

from scrapers import steam


class _Resp:
    ok, status = False, 401


class _Page:
    def __init__(self):
        self.request = self

    def get(self, url, params=None):
        return _Resp()


def test_collect_raises_without_credentials_or_session(monkeypatch) -> None:
    monkeypatch.setattr(steam.config, "get_steam_credentials", lambda: (None, None))
    with pytest.raises(RuntimeError, match="Log into Steam in the browser window"):
        steam.collect(page=_Page(), captured=[])
