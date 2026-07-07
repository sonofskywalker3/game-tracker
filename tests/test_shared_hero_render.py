"""The shared stats hero (#hero-stats) renders on every page."""
import pytest


@pytest.mark.parametrize("path", ["/", "/recommendations", "/settings"])
def test_hero_present_on_every_page(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'id="hero-stats"' in body


def test_library_still_has_mode_switcher(client):
    body = client.get("/").get_data(as_text=True)
    assert 'id="mode-switcher"' in body
