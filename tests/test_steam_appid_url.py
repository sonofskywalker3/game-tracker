"""Unit tests for steam_dlc.appid_from_steam_url (mirrors igdb_dlc.slug_from_igdb_url)."""
from __future__ import annotations

import pytest

from steam_dlc import appid_from_steam_url


@pytest.mark.parametrize("url,expected", [
    ("https://store.steampowered.com/app/1794680/Vampire_Survivors/", 1794680),
    ("https://store.steampowered.com/app/1794680/", 1794680),
    ("https://store.steampowered.com/app/1794680", 1794680),
    ("http://store.steampowered.com/app/42/", 42),
    ("https://store.steampowered.com/app/1794680/Vampire_Survivors/?snr=foo", 1794680),
    ("  https://store.steampowered.com/app/7/  ", 7),
    ("HTTPS://STORE.STEAMPOWERED.COM/APP/1794680/", 1794680),
])
def test_parses_appid(url, expected):
    assert appid_from_steam_url(url) == expected


@pytest.mark.parametrize("bad", [
    None,
    "",
    "   ",
    "https://store.steampowered.com/",
    "https://store.steampowered.com/sub/12345/",
    "https://www.igdb.com/games/vampire-survivors",
    "not a url",
    "store.steampowered.com/app/123",  # missing scheme
])
def test_rejects_non_steam_app_urls(bad):
    assert appid_from_steam_url(bad) is None
