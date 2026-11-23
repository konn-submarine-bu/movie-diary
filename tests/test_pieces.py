from __future__ import annotations

import io

from PIL import Image

from app import pieces
from tests.conftest import text


def png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 100, 50)).save(buf, "PNG")
    return buf.getvalue()


def add(env, **body):
    res = env.client.post("/api/pieces", json={"text": "人生没有白走的路。", **body})
    assert res.status_code == 201, res.text
    return res.json()


def test_text_piece_is_saved_with_optional_source(env):
    p = add(env, title="如何看待努力", platform="知乎", url="https://www.zhihu.com/question/1", note="想起去年")
    assert p["kind"] == "text" and p["platform"] == "知乎" and p["note"] == "想起去年" and p["image_url"] == ""
    listed = env.client.get("/api/pieces").json()
    assert [x["id"] for x in listed["pieces"]] == [p["id"]]
    assert "知乎" in listed["platforms"]


def test_text_piece_only_needs_text(env):
    p = add(env)
    assert p["title"] == p["platform"] == p["url"] == p["note"] == ""


def test_text_piece_rejects_empty_duplicate_bad_url_and_long_fields(env):
    assert env.client.post("/api/pieces", json={"text": "   "}).json()["error_code"] == "piece_empty"
    add(env)
    assert env.client.post("/api/pieces", json={"text": "人生没有白走的路。"}).json()["error_code"] == "piece_already_exists"
    res = env.client.post("/api/pieces", json={"text": "另一句", "url": "javascript:alert(1)"})
    assert res.status_code == 422 and res.json()["error_code"] == "piece_url_invalid"
    res = env.client.post("/api/pieces", json={"text": "另一句", "note": "x" * 501})
    assert res.json()["error_code"] == "piece_too_long"


def test_image_piece_is_stored_served_and_deleted_with_file(env, tmp_path):
    res = env.client.post("/api/pieces/image", params={"platform": "网易云音乐", "title": "歌词截图"},
                          content=png(), headers={"Content-Type": "image/png"})
    assert res.status_code == 201, res.text
    p = res.json()
    assert p["kind"] == "image" and p["filename"].endswith(".jpg") and p["platform"] == "网易云音乐"
    assert env.client.get(p["image_url"]).status_code == 200
    stored = tmp_path / "pieces" / p["filename"]
    assert stored.is_file()
    assert env.client.delete(f"/api/pieces/{p['id']}").status_code == 204
    assert not stored.exists()
    assert env.client.delete(f"/api/pieces/{p['id']}").json()["error_code"] == "piece_not_found"


def test_image_piece_rejects_non_image(env):
    res = env.client.post("/api/pieces/image", content=b"not an image", headers={"Content-Type": "image/png"})
    assert res.status_code == 422 and res.json()["error_code"] == "image_invalid"
    assert env.client.get("/api/pieces").json()["pieces"] == []


def test_patch_updates_note_and_keeps_text_required(env):
    p = add(env)
    res = env.client.patch(f"/api/pieces/{p['id']}", json={"note": "其实我不同意"})
    assert res.status_code == 200 and res.json()["note"] == "其实我不同意"
    assert env.client.patch(f"/api/pieces/{p['id']}", json={"text": ""}).json()["error_code"] == "piece_empty"


def test_chat_sees_pieces_and_notes_and_keeps_its_own_history(env):
    add(env, platform="知乎", title="关于努力", note="我觉得太鸡汤")
    entry = env.add_entry(1)
    env.llm.replies = [text("你存过一句「人生没有白走的路。」（#1 · 知乎 · 关于努力）")]
    res = env.client.post("/api/pieces/chat", json={"message": "我收藏过什么讲努力的？"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["message"]["role"] == "assistant"
    assert body["message"]["meta"] == {"pieces_seen": 1, "pieces_total": 1}
    call = env.llm.calls[0]
    assert call["tools"] is None
    system = call["messages"][0]["content"]
    assert "人生没有白走的路。" in system and "用户批注：我觉得太鸡汤" in system and "#1 · 知乎 · 关于努力" in system

    env.llm.replies = [text("好。")]
    env.client.post("/api/pieces/chat", json={"message": "继续"})
    sent = env.llm.calls[1]["messages"]
    assert [m["role"] for m in sent[1:]] == ["user", "assistant", "user"]

    assert len(env.client.get("/api/pieces/chat").json()["messages"]) == 4
    assert env.client.get("/api/chat").json()["messages"] == []
    assert env.client.get(f"/api/entries/{entry['id']}/chat").json()["messages"] == []
    assert env.client.delete("/api/pieces/chat").status_code == 204
    assert env.client.get("/api/pieces/chat").json()["messages"] == []


def test_failed_chat_saves_nothing(env):
    env.llm.replies = [text("   ")]
    res = env.client.post("/api/pieces/chat", json={"message": "你好"})
    assert res.json()["error_code"] == "llm_empty_response"
    assert env.client.get("/api/pieces/chat").json()["messages"] == []


def test_context_keeps_newest_pieces_within_budget(monkeypatch):
    monkeypatch.setattr(pieces, "CONTEXT_BUDGET", 50)
    rows = [{"id": i, "kind": "text", "text": "字" * 30, "title": "", "platform": "", "url": "", "note": ""}
            for i in (3, 2, 1)]
    block, used = pieces.context(rows)
    assert used == 1 and "#3" in block and "#2" not in block
    assert pieces.context([]) == ("（还没有任何收藏）", 0)
