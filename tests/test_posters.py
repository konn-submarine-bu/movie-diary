from __future__ import annotations

import io

from PIL import Image

from app.errors import AppError
from app.posters import DEFAULT_PALETTE, parse_brief
from tests.conftest import BRIEF_JSON, text


def make(env, quote="有些告别没有声音"):
    entry = env.add_entry(1)
    return entry, env.client.post(f"/api/entries/{entry['id']}/posters", json={"quote": quote})


def test_poster_is_generated_with_text_rendered_by_code(env, tmp_path):
    env.llm.replies = [text(BRIEF_JSON)]
    entry, res = make(env)
    assert res.status_code == 201
    poster = res.json()
    assert poster["fallback_code"] == ""
    assert "文字" in env.images.prompts[0] or "街道" in env.images.prompts[0]
    img = Image.open(tmp_path / "posters" / poster["filename"])
    assert img.size == (1024, 1536)
    assert env.client.get(poster["url"]).status_code == 200


def test_image_failure_falls_back_to_gradient_and_records_error(env, tmp_path):
    env.images.error = AppError("image_request_failed", "Image API returned HTTP 500: upstream exploded", 502)
    env.llm.replies = [text(BRIEF_JSON)]
    _, res = make(env)
    assert res.status_code == 201
    poster = res.json()
    assert poster["fallback_code"] == "image_request_failed"
    assert "HTTP 500" in poster["fallback_message"]
    assert Image.open(tmp_path / "posters" / poster["filename"]).size == (1024, 1536)


def test_unreadable_image_bytes_fall_back_with_decode_code(env):
    class Garbage:
        async def generate(self, prompt):
            return b"not an image"
    from fastapi.testclient import TestClient
    from app.main import create_app
    from tests.conftest import FakeTMDB
    env.llm.replies = [text(BRIEF_JSON)]
    client = TestClient(create_app(env.settings, env.llm, Garbage(), FakeTMDB(), env.search))
    entry = client.post("/api/entries", json={"tmdb_id": 1, "title": "路过人间", "year": 2021}).json()
    poster = client.post(f"/api/entries/{entry['id']}/posters", json={"quote": "你好"}).json()
    assert poster["fallback_code"] == "image_decode_failed"


def test_bad_brief_is_a_hard_error_not_a_silent_poster(env):
    env.llm.replies = [text("抱歉，我无法完成")]
    _, res = make(env)
    assert res.status_code == 502
    assert res.json()["error_code"] == "poster_brief_invalid"


def test_quote_validation(env):
    entry = env.add_entry(1)
    res = env.client.post(f"/api/entries/{entry['id']}/posters", json={"quote": "x" * 201})
    assert res.status_code == 422 and res.json()["error_code"] == "request_invalid"


def test_long_multiline_quote_renders(env, tmp_path):
    env.llm.replies = [text(BRIEF_JSON)]
    quote = "如果有一天我不再爱你，\n请你一定要记得，我曾经那样认真地、笨拙地爱过你。" * 2
    _, res = make(env, quote[:200])
    assert res.status_code == 201


def test_parse_brief_extracts_json_and_filters_palette():
    brief = parse_brief('好的：{"mood":"m","palette":["red","#112233"],"image_prompt":"p"} 完成')
    assert brief["palette"] == ["#112233"]
    assert parse_brief('{"image_prompt":"p"}')["palette"] == DEFAULT_PALETTE


def test_poster_can_be_deleted(env, tmp_path):
    env.llm.replies = [text(BRIEF_JSON)]
    _, res = make(env)
    poster = res.json()
    assert env.client.delete(f"/api/posters/{poster['id']}").status_code == 204
    assert not (tmp_path / "posters" / poster["filename"]).exists()
    assert env.client.delete(f"/api/posters/{poster['id']}").json()["error_code"] == "poster_not_found"


def test_style_and_extra_reach_the_brief_prompt(env):
    env.llm.replies = [text(BRIEF_JSON)]
    entry = env.add_entry(1)
    res = env.client.post(f"/api/entries/{entry['id']}/posters",
                          json={"quote": "你好", "style": "ink", "extra": "只有一个背影"})
    assert res.status_code == 201
    user_msg = env.llm.calls[0]["messages"][-1]["content"]
    assert "水墨留白" in user_msg and "只有一个背影" in user_msg


def test_no_style_or_extra_adds_nothing_to_the_brief(env):
    env.llm.replies = [text(BRIEF_JSON)]
    make(env)
    user_msg = env.llm.calls[0]["messages"][-1]["content"]
    assert "画面风格" not in user_msg and "补充要求" not in user_msg


def test_unknown_style_and_long_extra_are_rejected(env):
    entry = env.add_entry(1)
    url = f"/api/entries/{entry['id']}/posters"
    bad = env.client.post(url, json={"quote": "你好", "style": "nope"})
    assert bad.status_code == 422 and bad.json()["error_code"] == "poster_style_unknown"
    long = env.client.post(url, json={"quote": "你好", "extra": "x" * 201})
    assert long.status_code == 422 and long.json()["error_code"] == "poster_extra_too_long"


def test_style_catalog_endpoint(env):
    data = env.client.get("/api/poster-styles").json()
    assert data["max_extra"] == 200
    assert {"key": "ink", "label": "水墨留白"} in data["styles"]
