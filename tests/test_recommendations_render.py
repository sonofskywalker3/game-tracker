"""The picks-tab template renders without Jinja/template errors."""
def test_recommendations_page_renders(client):
    resp = client.get("/recommendations")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'id="slate"' in body
    assert "loadSlate" in body
    assert 'id="slot-settings-modal"' in body
