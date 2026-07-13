"""Soft update check against GET <server>/api/scraper/version."""
from desktop.versioncheck import APP_VERSION, check_for_update, parse_version


def test_parse_version() -> None:
    assert parse_version("1.2.10") == (1, 2, 10)


def test_newer_version_reported() -> None:
    got = check_for_update("https://s", fetch=lambda url, timeout: '{"version": "9.9.9"}',
                           current="0.1.0")
    assert got == "9.9.9"


def test_same_or_older_returns_none() -> None:
    assert check_for_update("https://s", fetch=lambda u, timeout: '{"version": "0.0.1"}',
                            current="0.1.0") is None
    assert check_for_update("https://s", fetch=lambda u, timeout: f'{{"version": "{APP_VERSION}"}}') is None


def test_errors_return_none() -> None:
    def boom(url: str, timeout: int) -> str:
        raise OSError("offline")
    assert check_for_update("https://s", fetch=boom) is None
    assert check_for_update("https://s", fetch=lambda u, timeout: "garbage") is None
