from __future__ import annotations

import io
import json

from PIL import Image

from app import pieces
from app.errors import AppError
from tests.conftest import text, tool

OCR = json.dumps({"text": "我曾经跨过山和大海\n也穿过人山和人海", "caption": "网易云音乐歌词页截图"}, ensure_ascii=False)


def png(w: int = 64, h: int = 48) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 100, 50)).save(buf, "PNG")
    return buf.getvalue()


def add(env, **body):
    res = env.client.post("/api/pieces", json={"text": "人生没有白走的路。", **body})
    assert res.status_code == 201, res.text
    return res.json()


def add_image(env, reply: str = OCR, **params):
    env.llm.replies.append(text(reply))
    res = env.client.post("/api/pieces/image", params=params, content=png(), headers={"Content-Type": "image/png"})
    assert res.status_code == 201, res.text
    return res.json()


def images_in(message) -> int:
    content = message["content"]
    return sum(1 for p in content if p.get("type") == "image_url") if isinstance(content, list) else 0


# ---- storing ----------------------------------------------------------------
def test_text_piece_is_saved_with_optional_source(env):
    p = add(env, title="如何看待努力", platform="知乎", url="https://www.zhihu.com/question/1", note="想起去年")
    assert p["kind"] == "text" and p["platform"] == "知乎" and p["note"] == "想起去年" and p["image_url"] == ""
    assert p["ocr_status"] == ""
    listed = env.client.get("/api/pieces").json()
    assert [x["id"] for x in listed["pieces"]] == [p["id"]]
    assert "知乎" in listed["platforms"]


def test_text_piece_rejects_empty_duplicate_bad_url_and_long_fields(env):
    assert env.client.post("/api/pieces", json={"text": "   "}).json()["error_code"] == "piece_empty"
    add(env)
    assert env.client.post("/api/pieces", json={"text": "人生没有白走的路。"}).json()["error_code"] == "piece_already_exists"
    res = env.client.post("/api/pieces", json={"text": "另一句", "url": "javascript:alert(1)"})
    assert res.status_code == 422 and res.json()["error_code"] == "piece_url_invalid"
    assert env.client.post("/api/pieces", json={"text": "另一句", "note": "x" * 501}).json()["error_code"] == "piece_too_long"


def test_patch_updates_note_and_keeps_text_required(env):
    p = add(env)
    res = env.client.patch(f"/api/pieces/{p['id']}", json={"note": "其实我不同意"})
    assert res.status_code == 200 and res.json()["note"] == "其实我不同意"
    assert env.client.patch(f"/api/pieces/{p['id']}", json={"text": ""}).json()["error_code"] == "piece_empty"
    assert env.client.patch(f"/api/pieces/{p['id']}", json={"caption": "x"}).json()["error_code"] == "piece_not_image"


# ---- image transcription ------------------------------------------------------
def test_image_upload_is_transcribed_and_file_removed_on_delete(env, tmp_path):
    p = add_image(env, platform="网易云音乐", title="平凡之路")
    assert p["kind"] == "image" and p["ocr_status"] == "ok" and p["ocr_error"] == ""
    assert p["text"].startswith("我曾经跨过山和大海") and p["caption"] == "网易云音乐歌词页截图"
    call = env.llm.calls[-1]
    assert call["tools"] is None
    assert images_in(call["messages"][1]) == 1
    assert env.client.get(p["image_url"]).status_code == 200
    stored = tmp_path / "pieces" / p["filename"]
    assert env.client.delete(f"/api/pieces/{p['id']}").status_code == 204
    assert not stored.exists()


def test_failed_transcription_keeps_the_piece_and_can_be_retried(env):
    p = add_image(env, reply="抱歉，我看不清")
    assert p["ocr_status"] == "failed" and p["ocr_error"].startswith("image_transcribe_failed")
    assert p["text"] == ""
    env.llm.replies.append(text(OCR))
    again = env.client.post(f"/api/pieces/{p['id']}/transcribe").json()
    assert again["ocr_status"] == "ok" and "人山和人海" in again["text"]


def test_transcription_edits_are_kept(env):
    p = add_image(env)
    res = env.client.patch(f"/api/pieces/{p['id']}", json={"text": "改过的字", "caption": "改过的描述"}).json()
    assert res["text"] == "改过的字" and res["caption"] == "改过的描述"


def test_image_upload_rejects_non_image(env):
    res = env.client.post("/api/pieces/image", content=b"not an image", headers={"Content-Type": "image/png"})
    assert res.status_code == 422 and res.json()["error_code"] == "image_invalid"
    assert env.client.get("/api/pieces").json()["pieces"] == []
    assert env.llm.calls == []


def test_tall_screenshot_is_cut_into_readable_parts(tmp_path):
    path = tmp_path / "tall.jpg"
    Image.new("RGB", (800, 6000), (255, 255, 255)).save(path)
    parts = pieces.image_parts(path)
    assert 2 <= len(parts) <= pieces.MAX_CHUNKS
    assert all(p["image_url"]["url"].startswith("data:image/jpeg;base64,") for p in parts)
    Image.new("RGB", (800, 600), (255, 255, 255)).save(path)
    assert len(pieces.image_parts(path)) == 1


# ---- search -----------------------------------------------------------------
ROWS = [
    {"id": 3, "kind": "text", "text": "一个人吃饭也没什么，孤独是常态。", "caption": "", "title": "关于独处",
     "platform": "知乎", "url": "", "note": "", "created_at": "2026-11-01T00:00:00"},
    {"id": 2, "kind": "image", "text": "", "caption": "雨夜街道的照片", "title": "", "platform": "",
     "url": "", "note": "很孤独的感觉", "created_at": "2026-10-01T00:00:00"},
    {"id": 1, "kind": "text", "text": "Love is patient.", "caption": "", "title": "", "platform": "书",
     "url": "", "note": "", "created_at": "2026-09-01T00:00:00"},
]


def test_search_ranks_by_distinct_keywords_and_covers_notes_and_captions():
    hits = pieces.search(ROWS, ["孤独", "一个人", "雨夜"])
    assert [h["id"] for h in hits] == [3, 2]
    assert hits[0]["matched"] == ["孤独", "一个人"] and "孤独" in hits[0]["snippet"]
    assert [h["id"] for h in pieces.search(ROWS, ["LOVE"])] == [1]
    assert pieces.search(ROWS, ["不存在的词"]) == []


def test_search_needs_a_keyword():
    try:
        pieces.search(ROWS, ["  "])
    except AppError as exc:
        assert exc.code == "search_keywords_empty"
    else:
        raise AssertionError("expected search_keywords_empty")


# ---- chat ---------------------------------------------------------------------
def test_chat_prompt_has_a_catalog_not_full_text(env):
    add(env, platform="知乎", title="关于努力", note="我觉得太鸡汤", text="人生没有白走的路。" + "长" * 200)
    env.llm.replies = [text("你想聊哪一段？")]
    res = env.client.post("/api/pieces/chat", json={"message": "你好"})
    assert res.status_code == 200, res.text
    system = env.llm.calls[0]["messages"][0]["content"]
    assert "#1 · 知乎 · 关于努力" in system and "批注：我觉得太鸡汤" in system
    assert "长" * 100 not in system
    assert env.llm.calls[0]["tools"] == pieces.TOOLS


def test_chat_agent_searches_then_reads_then_answers(env):
    add(env, platform="知乎", title="关于努力", note="有点鸡汤，但我信")
    env.llm.replies = [
        tool("search_pieces", {"keywords": ["努力", "白走"]}),
        tool("read_pieces", {"ids": [1]}, "c2"),
        text("你存过「人生没有白走的路。」（#1 · 知乎 · 关于努力）。"),
    ]
    res = env.client.post("/api/pieces/chat", json={"message": "我收藏过什么讲努力的？"})
    assert res.status_code == 200, res.text
    meta = res.json()["message"]["meta"]
    assert meta["searches"] == [["努力", "白走"]] and meta["read_ids"] == [1]
    assert [t["name"] for t in meta["tools"]] == ["search_pieces", "read_pieces"]
    found = json.loads(env.llm.calls[1]["messages"][-1]["content"])
    assert found["results"][0]["id"] == 1 and found["results"][0]["matched"] == ["努力", "白走"]
    full = json.loads(env.llm.calls[2]["messages"][-1]["content"])
    assert full["pieces"][0]["text"] == "人生没有白走的路。" and full["pieces"][0]["note"] == "有点鸡汤，但我信"


def test_tool_errors_are_reported_back_to_the_model(env):
    env.llm.replies = [tool("read_pieces", {"ids": [99]}), tool("view_image", {"piece_id": 99}, "c2"),
                       tool("search_pieces", {"keywords": []}, "c3"), text("没找到。")]
    assert env.client.post("/api/pieces/chat", json={"message": "看看 #99"}).status_code == 200
    assert json.loads(env.llm.calls[1]["messages"][-1]["content"]) == {"pieces": [], "not_found": [99]}
    assert json.loads(env.llm.calls[2]["messages"][-1]["content"])["error_code"] == "piece_not_found"
    assert json.loads(env.llm.calls[3]["messages"][-1]["content"])["error_code"] == "search_keywords_empty"


def test_view_image_sends_the_stored_image_as_a_follow_up_message(env):
    p = add_image(env, title="歌词截图")
    env.llm.replies = [tool("view_image", {"piece_id": p["id"]}), text("排版很疏朗。")]
    res = env.client.post("/api/pieces/chat", json={"message": f"#{p['id']} 这张图排版怎么样"})
    assert res.status_code == 200, res.text
    sent = env.llm.calls[-1]["messages"]
    assert sent[-2]["role"] == "tool" and json.loads(sent[-2]["content"])["ok"] is True
    assert sent[-1]["role"] == "user" and images_in(sent[-1]) == 1
    assert res.json()["message"]["meta"]["viewed_ids"] == [p["id"]]


def test_user_can_send_an_image_in_chat_and_save_it_as_a_piece(env, tmp_path):
    up = env.client.post("/api/pieces/chat/image", content=png(), headers={"Content-Type": "image/png"})
    assert up.status_code == 201, up.text
    image = up.json()["image"]
    env.llm.replies = [text("这是一段关于离开的话。")]
    res = env.client.post("/api/pieces/chat", json={"message": "这段怎么理解", "image": image})
    assert res.status_code == 200, res.text
    user_msg = res.json()["user_message"]
    assert user_msg["image_url"] == f"/media/pieces/{image}"
    assert images_in(env.llm.calls[-1]["messages"][-1]) == 1
    assert env.client.get("/api/pieces").json()["pieces"] == []

    # the image is still visible to the model on the next turn
    env.llm.replies = [text("好。")]
    env.client.post("/api/pieces/chat", json={"message": "再说说"})
    history = env.llm.calls[-1]["messages"]
    assert sum(images_in(m) for m in history) == 1

    env.llm.replies = [text(OCR)]
    saved = env.client.post(f"/api/pieces/chat/{user_msg['id']}/save", json={"platform": "小红书", "note": "想留着"})
    assert saved.status_code == 201, saved.text
    piece = saved.json()
    assert piece["kind"] == "image" and piece["ocr_status"] == "ok" and piece["platform"] == "小红书"
    assert piece["filename"] != image
    again = env.client.post(f"/api/pieces/chat/{user_msg['id']}/save", json={})
    assert again.json()["error_code"] == "piece_already_exists"
    assert env.client.get("/api/pieces/chat").json()["messages"][0]["meta"]["saved_piece_id"] == piece["id"]

    # clearing the chat deletes the chat image but keeps the saved copy
    assert env.client.delete("/api/pieces/chat").status_code == 204
    assert not (tmp_path / "pieces" / image).exists()
    assert (tmp_path / "pieces" / piece["filename"]).is_file()


def test_chat_rejects_unknown_or_unsafe_image_names(env):
    for name in ("../diary.sqlite3", "chat-000000000000.jpg", "piece-abc.jpg"):
        res = env.client.post("/api/pieces/chat", json={"message": "看", "image": name})
        assert res.status_code == 404 and res.json()["error_code"] == "chat_image_not_found"
    assert env.llm.calls == []


def test_chat_keeps_its_own_history_and_failed_turns_save_nothing(env):
    entry = env.add_entry(1)
    env.llm.replies = [text("   ")]
    assert env.client.post("/api/pieces/chat", json={"message": "你好"}).json()["error_code"] == "llm_empty_response"
    assert env.client.get("/api/pieces/chat").json()["messages"] == []
    env.llm.replies = [text("你好。")]
    env.client.post("/api/pieces/chat", json={"message": "你好"})
    env.llm.replies = [text("好。")]
    env.client.post("/api/pieces/chat", json={"message": "继续"})
    sent = env.llm.calls[-1]["messages"]
    assert [m["role"] for m in sent[1:]] == ["user", "assistant", "user"]
    assert env.client.get("/api/chat").json()["messages"] == []
    assert env.client.get(f"/api/entries/{entry['id']}/chat").json()["messages"] == []


def test_chat_stops_at_the_step_limit(env):
    env.llm.replies = [tool("list_pieces", {}, f"c{i}") for i in range(env.settings.max_agent_steps)]
    res = env.client.post("/api/pieces/chat", json={"message": "都有什么"})
    assert res.status_code == 502 and res.json()["error_code"] == "agent_step_limit"
    assert env.client.get("/api/pieces/chat").json()["messages"] == []


def test_old_pieces_table_is_migrated(tmp_path):
    import sqlite3
    from app.db import Database
    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE pieces (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, text TEXT NOT NULL DEFAULT '',"
                 " filename TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', platform TEXT NOT NULL DEFAULT '',"
                 " url TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)")
    conn.execute("INSERT INTO pieces (kind, text, created_at) VALUES ('text', '旧的一条', '2026-11-01')")
    conn.commit()
    conn.close()
    db = Database(path)
    old = db.get_piece(1)
    assert old["text"] == "旧的一条" and old["caption"] == "" and old["ocr_status"] == ""
