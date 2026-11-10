from __future__ import annotations


def test_create_fills_metadata_from_tmdb(env):
    entry = env.add_entry(2, rating=5, note="克制到令人心碎")
    assert entry["director"] == "王家卫"
    assert entry["genres"] == ["爱情"]
    assert entry["rating"] == 5


def test_duplicate_entry_reports_code(env):
    env.add_entry(1)
    resp = env.client.post("/api/entries", json={"tmdb_id": 1, "title": "路过人间", "year": 2021})
    assert resp.status_code == 409
    body = resp.json()
    assert body["error_code"] == "entry_already_exists"
    assert "路过人间" in body["error"]


def test_patch_and_get(env):
    entry = env.add_entry(1)
    resp = env.client.patch(f"/api/entries/{entry['id']}", json={"rating": 4, "note": "想起外婆家"})
    assert resp.status_code == 200
    got = env.client.get(f"/api/entries/{entry['id']}").json()
    assert got["rating"] == 4 and got["note"] == "想起外婆家" and got["posters"] == []


def test_invalid_rating_is_rejected_with_code(env):
    entry = env.add_entry(1)
    resp = env.client.patch(f"/api/entries/{entry['id']}", json={"rating": 9})
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "request_invalid"
    assert "rating" in resp.json()["error"]


def test_missing_entry_is_404_with_code(env):
    resp = env.client.get("/api/entries/999")
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "entry_not_found"


def test_delete_cascades_messages_and_posters(env, tmp_path):
    entry = env.add_entry(1)
    from tests.conftest import BRIEF_JSON, text
    env.llm.replies = [text(BRIEF_JSON)]
    poster = env.client.post(f"/api/entries/{entry['id']}/posters", json={"quote": "有些告别没有声音"}).json()
    assert (tmp_path / "posters" / poster["filename"]).is_file()
    assert env.client.delete(f"/api/entries/{entry['id']}").status_code == 204
    assert not (tmp_path / "posters" / poster["filename"]).exists()
    assert env.client.get(f"/api/entries/{entry['id']}").status_code == 404


def test_unknown_api_route_does_not_fall_through_to_html(env):
    resp = env.client.get("/api/nope")
    assert resp.status_code == 404
