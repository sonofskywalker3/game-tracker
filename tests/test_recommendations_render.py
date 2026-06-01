"""The picks-tab template renders without Jinja/template errors."""
def test_recommendations_page_renders(client):
    resp = client.get("/recommendations")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'id="slate"' in body
    assert "loadSlate" in body


def test_recommendations_has_slate_and_needs_rating(client):
    body = client.get("/recommendations").get_data(as_text=True)
    assert 'id="slate"' in body
    assert 'id="needs-rating-grid"' in body
    assert "loadSlate" in body


def test_slot_settings_has_focus_series_picker():
    with open("templates/recommendations.html", encoding="utf-8") as f:
        html = f.read()
    assert "focus_series_id" in html
    assert "Focus series" in html
    assert "/api/series" in html
