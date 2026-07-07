"""user_profile GET/PUT endpoints."""


def test_get_profile_defaults(client):
    data = client.get("/api/profile").get_json()
    assert data["work_start_min"] is None
    assert data["meal_windows"] == []


def test_put_profile_updates_fields(client):
    r = client.put("/api/profile", json={
        "work_start_min": 540, "work_end_min": 1020, "bed_time_min": 1380,
        "meal_windows": [{"start_min": 720, "end_min": 780}],
    })
    assert r.status_code == 200
    data = client.get("/api/profile").get_json()
    assert data["work_start_min"] == 540 and data["bed_time_min"] == 1380
    assert data["meal_windows"] == [{"start_min": 720, "end_min": 780}]


def test_put_profile_rejects_out_of_range(client):
    r = client.put("/api/profile", json={"work_start_min": 5000})
    assert r.status_code == 400


def test_put_profile_partial_keeps_others(client):
    client.put("/api/profile", json={"work_start_min": 540})
    client.put("/api/profile", json={"bed_time_min": 1380})
    data = client.get("/api/profile").get_json()
    assert data["work_start_min"] == 540 and data["bed_time_min"] == 1380


def test_profile_display_mode_roundtrip(client):
    assert client.get('/api/profile').get_json()['collection_display_mode'] == 'members'
    client.put('/api/profile', json={'collection_display_mode': 'both'})
    assert client.get('/api/profile').get_json()['collection_display_mode'] == 'both'


def test_profile_display_mode_invalid_normalized(client):
    client.put('/api/profile', json={'collection_display_mode': 'bogus'})
    assert client.get('/api/profile').get_json()['collection_display_mode'] == 'members'
