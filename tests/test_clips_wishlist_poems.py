from __future__ import annotations

import io
import sqlite3
from datetime import date

from PIL import Image

from app.db import Database
from app.poems import POETS, parse_poem
from tests.conftest import text, tool


def png_bytes(size=(64, 48), color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


# ---- on this day ----------------------------------------------------------
def test_on_this_day_returns_earlier_years_only(env):
    env.add_entry(1, watched_on="2023-11-09", note="那年冬天")
    env.add_entry(2, watched_on="2025-11-09")
    res = env.client.get("/api/on-this-day", params={"on": "2026-11-09"}).json()
    assert [(e["title"], e["years_ago"]) for e in res["entries"]] == [("花样年华", 1), ("路过人间", 3)]


def test_on_this_day_ignores_same_year_other_days_and_wishlist(env):
    env.add_entry(1, watched_on="2026-11-09")
    env.add_entry(2, watched_on="2024-11-10")
    assert env.client.get("/api/on-this-day", params={"on": "2026-11-09"}).json()["entries"] == []
    env.client.patch(f"/api/entries/{env.client.get('/api/entries').json()['entries'][0]['id']}",
                     json={"status": "wishlist"})
    assert env.client.get("/api/on-this-day", params={"on": "2027-11-09"}).json()["entries"] == []


def test_on_this_day_rejects_bad_date_with_code(env):
    res = env.client.get("/api/on-this-day", params={"on": "11-09"})
    assert res.status_code == 422 and res.json()["error_code"] == "date_invalid"


# ---- wishlist -------------------------------------------------------------
def test_wishlist_entry_is_listed_with_status_and_no_date(env):
    entry = env.add_entry(1, status="wishlist")
    assert entry["status"] == "wishlist" and entry["watched_on"] == ""
    listed = env.client.get("/api/entries").json()["entries"]
    assert [e["status"] for e in listed] == ["wishlist"]


def test_marking_wishlist_as_watched_fills_today(env):
    entry = env.add_entry(1, status="wishlist")
    got = env.client.patch(f"/api/entries/{entry['id']}", json={"status": "watched"}).json()
    assert got["status"] == "watched" and got["watched_on"] == date.today().isoformat()


def test_marking_watched_keeps_an_explicit_date(env):
    entry = env.add_entry(1, status="wishlist")
    got = env.client.patch(f"/api/entries/{entry['id']}",
                           json={"status": "watched", "watched_on": "2025-01-02", "rating": 4}).json()
    assert got["watched_on"] == "2025-01-02" and got["rating"] == 4


def test_adding_a_wishlisted_movie_as_watched_promotes_it(env):
    first = env.add_entry(1, status="wishlist")
    again = env.client.post("/api/entries", json={
        "tmdb_id": 1, "title": "路过人间", "year": 2021, "rating": 5, "note": "终于看了",
        "watched_on": "2025-06-01"})
    assert again.status_code == 201
    body = again.json()
    assert body["id"] == first["id"] and body["status"] == "watched"
    assert body["rating"] == 5 and body["watched_on"] == "2025-06-01"
    assert len(env.client.get("/api/entries").json()["entries"]) == 1


def test_adding_a_wishlisted_movie_to_wishlist_again_is_a_duplicate(env):
    env.add_entry(1, status="wishlist")
    res = env.client.post("/api/entries", json={"tmdb_id": 1, "title": "路过人间", "year": 2021,
                                                "status": "wishlist"})
    assert res.status_code == 409 and res.json()["error_code"] == "entry_already_exists"


def test_invalid_status_is_rejected(env):
    entry = env.add_entry(1)
    res = env.client.patch(f"/api/entries/{entry['id']}", json={"status": "later"})
    assert res.status_code == 422 and res.json()["error_code"] == "request_invalid"


def test_agent_wishlist_tools(env):
    env.llm.replies = [tool("add_to_wishlist", {"tmdb_id": 2, "note": "朋友推荐"}), text("放进想看清单了。")]
    res = env.client.post("/api/chat", json={"message": "我想看花样年华"}).json()
    assert res["diary_changed"] is True
    entry = env.client.get("/api/entries").json()["entries"][0]
    assert entry["status"] == "wishlist" and entry["note"] == "朋友推荐"

    env.llm.replies = [tool("read_diary", {}), tool("read_diary", {"status": "wishlist"}, "c2"), text("好")]
    env.client.post("/api/chat", json={"message": "我的清单里有什么"})
    watched, wish = [__import__("json").loads(m["content"]) for m in env.llm.calls[-1]["messages"]
                     if m["role"] == "tool"]
    assert watched["entries"] == []
    assert [e["title"] for e in wish["entries"]] == ["花样年华"]


def test_agent_can_mark_wishlist_watched_and_duplicate_add_explains_how(env):
    entry = env.add_entry(1, status="wishlist")
    env.llm.replies = [tool("add_to_diary", {"tmdb_id": 1}), text("它已经在想看清单里了。")]
    env.client.post("/api/chat", json={"message": "我看了路过人间"})
    seen = __import__("json").loads([m for m in env.llm.calls[-1]["messages"] if m["role"] == "tool"][0]["content"])
    assert seen["error_code"] == "entry_already_exists" and "status=watched" in seen["error"]

    env.llm.replies = [tool("update_diary", {"entry_id": entry["id"], "status": "watched", "rating": 4}), text("记下了。")]
    env.client.post("/api/chat", json={"message": "看完了，四星"})
    got = env.client.get(f"/api/entries/{entry['id']}").json()
    assert got["status"] == "watched" and got["rating"] == 4 and got["watched_on"]


def test_agent_rejects_bad_status_value(env):
    entry = env.add_entry(1)
    env.llm.replies = [tool("update_diary", {"entry_id": entry["id"], "status": "maybe"}), text("参数不对。")]
    env.client.post("/api/chat", json={"message": "x"})
    seen = __import__("json").loads([m for m in env.llm.calls[-1]["messages"] if m["role"] == "tool"][0]["content"])
    assert seen["error_code"] == "status_invalid"


# ---- quote clips ----------------------------------------------------------
def test_quote_clip_roundtrip_and_duplicate(env):
    entry = env.add_entry(1)
    res = env.client.post(f"/api/entries/{entry['id']}/clips", json={"text": " 有些告别没有声音 ", "note": "开场"})
    assert res.status_code == 201
    clip = res.json()
    assert clip["kind"] == "quote" and clip["text"] == "有些告别没有声音" and clip["url"] == ""
    dup = env.client.post(f"/api/entries/{entry['id']}/clips", json={"text": "有些告别没有声音"})
    assert dup.status_code == 409 and dup.json()["error_code"] == "clip_already_exists"
    got = env.client.get(f"/api/entries/{entry['id']}").json()
    assert [c["id"] for c in got["clips"]] == [clip["id"]]
    listed = env.client.get("/api/clips", params={"kind": "quote"}).json()["clips"]
    assert listed[0]["title"] == "路过人间"
    assert env.client.delete(f"/api/clips/{clip['id']}").status_code == 204
    assert env.client.get(f"/api/entries/{entry['id']}").json()["clips"] == []


def test_blank_quote_is_rejected(env):
    entry = env.add_entry(1)
    res = env.client.post(f"/api/entries/{entry['id']}/clips", json={"text": "   "})
    assert res.status_code == 422 and res.json()["error_code"] == "clip_empty"


def test_clip_on_missing_entry_is_404(env):
    res = env.client.post("/api/entries/999/clips", json={"text": "x"})
    assert res.status_code == 404 and res.json()["error_code"] == "entry_not_found"
    assert env.client.delete("/api/clips/999").json()["error_code"] == "clip_not_found"


def test_agent_saves_quote(env):
    entry = env.add_entry(2)
    env.llm.replies = [tool("save_quote", {"entry_id": entry["id"], "text": "如果有多一张船票"}), text("收藏好了。")]
    env.client.post("/api/chat", json={"message": "把这句收藏：如果有多一张船票"})
    clips = env.client.get(f"/api/entries/{entry['id']}").json()["clips"]
    assert [c["text"] for c in clips] == ["如果有多一张船票"]


# ---- screenshot clips -----------------------------------------------------
def test_screenshot_upload_is_normalized_and_served(env, tmp_path):
    entry = env.add_entry(1)
    res = env.client.post(f"/api/entries/{entry['id']}/clips/shot", params={"note": "雨夜"},
                          content=png_bytes((3000, 1000)), headers={"Content-Type": "application/octet-stream"})
    assert res.status_code == 201, res.text
    clip = res.json()
    assert clip["kind"] == "shot" and clip["note"] == "雨夜" and clip["filename"].endswith(".jpg")
    stored = Image.open(tmp_path / "clips" / clip["filename"])
    assert stored.format == "JPEG" and max(stored.size) <= 2400
    assert env.client.get(clip["url"]).status_code == 200


def test_screenshot_rejects_non_image_empty_and_oversize(env):
    entry = env.add_entry(1)
    url = f"/api/entries/{entry['id']}/clips/shot"
    bad = env.client.post(url, content=b"definitely not an image")
    assert bad.status_code == 422 and bad.json()["error_code"] == "image_invalid"
    empty = env.client.post(url, content=b"")
    assert empty.status_code == 422 and empty.json()["error_code"] == "image_empty"
    big = env.client.post(url, content=b"0" * (10 * 1024 * 1024 + 1))
    assert big.status_code == 413 and big.json()["error_code"] == "image_too_large"
    assert env.client.get(f"/api/entries/{entry['id']}").json()["clips"] == []


def test_deleting_entry_and_clip_removes_screenshot_files(env, tmp_path):
    entry = env.add_entry(1)
    url = f"/api/entries/{entry['id']}/clips/shot"
    a = env.client.post(url, content=png_bytes()).json()
    b = env.client.post(url, content=png_bytes((80, 60))).json()
    assert env.client.delete(f"/api/clips/{a['id']}").status_code == 204
    assert not (tmp_path / "clips" / a["filename"]).exists()
    assert (tmp_path / "clips" / b["filename"]).is_file()
    assert env.client.delete(f"/api/entries/{entry['id']}").status_code == 204
    assert not (tmp_path / "clips" / b["filename"]).exists()
    assert env.client.get("/api/clips").json()["clips"] == []


# ---- poems ----------------------------------------------------------------
def test_poem_styles_are_listed_without_prompt_internals(env):
    styles = env.client.get("/api/poem-styles").json()["styles"]
    assert len(styles) == len(POETS) >= 20
    assert set(styles[0]) == {"key", "name", "tagline", "group"}
    assert len({s["key"] for s in styles}) == len(styles)
    assert {s["group"] for s in styles} == {"cn", "foreign"}
    assert all(s["name"] and s["tagline"] for s in styles)


def test_foreign_poet_prompt_asks_for_chinese_translation_texture(env):
    entry = env.add_entry(1, note="想起外婆家")
    env.llm.replies = [text("雨\n\n一行\n两行")]
    res = env.client.post(f"/api/entries/{entry['id']}/poem", json={"style": "rilke"})
    assert res.status_code == 200 and res.json()["style"]["group"] == "foreign"
    system = env.llm.calls[-1]["messages"][0]["content"]
    assert "里尔克" in system and "中文译本" in system and "现代汉语" in system
    env.llm.replies = [text("雨\n\n一行\n两行")]
    env.client.post(f"/api/entries/{entry['id']}/poem", json={"style": "dai_wangshu"})
    assert "中文译本" not in env.llm.calls[-1]["messages"][0]["content"]


def test_poem_uses_entry_material_and_chosen_style(env):
    entry = env.add_entry(2, rating=5, note="克制到令人心碎")
    env.client.post(f"/api/entries/{entry['id']}/clips", json={"text": "如果有多一张船票"})
    env.llm.replies = [text("《旗袍与走廊》\n\n窄窄的楼梯\n把一整个夏天折起来")]
    res = env.client.post(f"/api/entries/{entry['id']}/poem", json={"style": "dai_wangshu"})
    assert res.status_code == 200
    poem = res.json()
    assert poem["title"] == "旗袍与走廊" and poem["body"].startswith("窄窄的楼梯")
    assert poem["style"] == {"key": "dai_wangshu", "name": "戴望舒", "tagline": "象征派 · 雨巷与忧郁", "group": "cn"}
    sent = env.llm.calls[-1]["messages"]
    assert "戴望舒" in sent[0]["content"] and "不要照抄" in sent[0]["content"]
    assert "花样年华" in sent[1]["content"] and "克制到令人心碎" in sent[1]["content"]
    assert "如果有多一张船票" in sent[1]["content"]
    assert env.llm.calls[-1]["tools"] is None


def test_poem_unknown_style_and_empty_reply_have_codes(env):
    entry = env.add_entry(1)
    res = env.client.post(f"/api/entries/{entry['id']}/poem", json={"style": "nobody"})
    assert res.status_code == 422 and res.json()["error_code"] == "poem_style_unknown"
    env.llm.replies = [text("   ")]
    res = env.client.post(f"/api/entries/{entry['id']}/poem", json={"style": "gu_cheng"})
    assert res.status_code == 502 and res.json()["error_code"] == "poem_empty"
    assert env.client.post("/api/entries/999/poem", json={"style": "gu_cheng"}).status_code == 404


def test_parse_poem_handles_titles_fences_and_untitled():
    assert parse_poem("《雨》\n\n一行\n两行") == ("雨", "一行\n两行")
    assert parse_poem("```\n雨\n\n一行\n```") == ("雨", "一行")
    assert parse_poem("第一行\n第二行\n第三行") == ("", "第一行\n第二行\n第三行")
    assert parse_poem("") == ("", "")


# ---- migration ------------------------------------------------------------
def test_old_database_without_status_is_migrated(tmp_path):
    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE entries (id INTEGER PRIMARY KEY AUTOINCREMENT, tmdb_id INTEGER, title TEXT NOT NULL,
          original_title TEXT NOT NULL DEFAULT '', year INTEGER, director TEXT NOT NULL DEFAULT '',
          overview TEXT NOT NULL DEFAULT '', poster_url TEXT NOT NULL DEFAULT '', genres TEXT NOT NULL DEFAULT '[]',
          rating INTEGER, note TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '[]',
          watched_on TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO entries (title, watched_on, created_at, updated_at) VALUES ('旧数据', '2024-05-01', 'x', 'x');
    """)
    conn.commit()
    conn.close()
    db = Database(path)
    assert [(e["title"], e["status"]) for e in db.list_entries()] == [("旧数据", "watched")]
    db2 = Database(path)  # opening again must be a no-op
    assert len(db2.list_entries(status=None)) == 1
    db2.create_entry({"title": "想看的", "status": "wishlist"})
    assert [e["title"] for e in db2.list_entries(status="wishlist")] == ["想看的"]


# ---- config ---------------------------------------------------------------
def test_chat_key_is_not_sent_to_a_different_image_gateway(monkeypatch, tmp_path):
    from app.config import Settings
    monkeypatch.setattr("app.config.load_dotenv", lambda path: None)
    for k in ("IMAGE_BASE_URL", "IMAGE_API_KEY", "IMAGE_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_BASE_URL", "https://chat.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "chat-secret")
    monkeypatch.setenv("LLM_MODEL", "m")
    same = Settings.from_env()
    assert same.image_base_url == "https://chat.example/v1" and same.image_api_key == "chat-secret"

    monkeypatch.setenv("IMAGE_BASE_URL", "https://images.example/v1")
    monkeypatch.setenv("IMAGE_MODEL", "img")
    other = Settings.from_env()
    assert other.image_api_key == "" and not other.image_configured

    monkeypatch.setenv("IMAGE_API_KEY", "image-secret")
    assert Settings.from_env().image_api_key == "image-secret"


# ---- saved poems ----------------------------------------------------------
def test_poem_can_be_saved_listed_and_deleted(env):
    entry = env.add_entry(2)
    res = env.client.post(f"/api/entries/{entry['id']}/poems",
                          json={"style": "dai_wangshu", "title": "夜雨", "body": " 雨落在空街上\n灯还亮着 "})
    assert res.status_code == 201
    saved = res.json()
    assert saved["style_name"] == "戴望舒" and saved["body"] == "雨落在空街上\n灯还亮着"
    assert env.client.get(f"/api/entries/{entry['id']}").json()["poems"][0]["id"] == saved["id"]
    allp = env.client.get("/api/poems").json()["poems"]
    assert [(p["movie"], p["title"]) for p in allp] == [("花样年华", "夜雨")]
    assert env.client.delete(f"/api/poems/{saved['id']}").status_code == 204
    assert env.client.get("/api/poems").json()["poems"] == []
    assert env.client.delete(f"/api/poems/{saved['id']}").json()["error_code"] == "poem_not_found"


def test_saving_the_same_poem_twice_and_bad_input_have_codes(env):
    entry = env.add_entry(1)
    url = f"/api/entries/{entry['id']}/poems"
    ok = {"style": "gu_cheng", "title": "", "body": "一行\n两行"}
    assert env.client.post(url, json=ok).status_code == 201
    dup = env.client.post(url, json=ok)
    assert dup.status_code == 409 and dup.json()["error_code"] == "poem_already_saved"
    assert env.client.post(url, json={**ok, "style": "nobody", "body": "别的"}).json()["error_code"] == "poem_style_unknown"
    assert env.client.post(url, json={**ok, "body": "   "}).status_code == 422
    assert env.client.post("/api/entries/999/poems", json=ok).status_code == 404


def test_deleting_an_entry_deletes_its_poems(env):
    entry = env.add_entry(1)
    env.client.post(f"/api/entries/{entry['id']}/poems", json={"style": "gu_cheng", "body": "一行"})
    env.client.delete(f"/api/entries/{entry['id']}")
    assert env.client.get("/api/poems").json()["poems"] == []
