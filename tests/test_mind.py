from __future__ import annotations

import json

from app import mind as mind_mod
from tests.conftest import text


def add(env, body="人生没有白走的路。", **kw):
    res = env.client.post("/api/pieces", json={"text": body, **kw})
    assert res.status_code == 201, res.text
    return res.json()


def mind_reply(views=(), tastes=(), doubts=()):
    return text(json.dumps({"views": list(views), "tastes": list(tastes), "doubts": list(doubts)}, ensure_ascii=False))


def refresh(env):
    res = env.client.post("/api/pieces/mind/refresh")
    assert res.status_code == 200, res.text
    return res.json()


def test_empty_mind_needs_no_model_call(env):
    state = refresh(env)
    assert state["mind"] == {"views": [], "tastes": [], "doubts": []}
    assert state["pending"] == 0 and env.llm.calls == []


def test_new_pieces_are_digested_into_sourced_views(env):
    a = add(env, platform="知乎", title="关于努力", note="有点鸡汤，但我信")
    b = add(env, "努力本身不保证什么。")
    assert env.client.get("/api/pieces/mind").json()["pending"] == 2
    env.llm.replies = [mind_reply(
        views=[{"text": "我觉得过程本身有价值，但不该被包装成回报的保证。", "sources": [a["id"], b["id"]],
                "disagrees_with_note": [a["id"]]}],
        tastes=[{"text": "我偏爱不煽情的说法。", "sources": [b["id"]]}],
        doubts=[{"text": "两段说法互相拉扯，我还没想清楚。", "sources": [a["id"], b["id"]]}])]
    state = refresh(env)
    assert state["pending"] == 0 and state["error"] == ""
    assert state["mind"]["views"][0]["sources"] == [a["id"], b["id"]]
    assert state["mind"]["views"][0]["disagrees_with_note"] == [a["id"]]
    prompt = env.llm.calls[0]["messages"][1]["content"]
    assert "用户的批注（他的反应，不是你的）：有点鸡汤，但我信" in prompt
    assert prompt.index(f"#{a['id']}") < prompt.index(f"#{b['id']}")  # oldest first
    assert env.llm.calls[0]["tools"] is None
    assert refresh(env)["pending"] == 0 and len(env.llm.calls) == 1  # nothing changed, no second call


def test_items_without_valid_sources_are_dropped_and_lists_capped(env):
    a = add(env)
    env.llm.replies = [mind_reply(views=[{"text": "没有来源", "sources": []},
                                         {"text": "来源不存在", "sources": [999]},
                                         {"text": "  ", "sources": [a["id"]]},
                                         *[{"text": f"看法{i}", "sources": [a["id"], a["id"]]} for i in range(20)]])]
    views = refresh(env)["mind"]["views"]
    assert len(views) == mind_mod.MAX_VIEWS
    assert views[0] == {"text": "看法0", "sources": [a["id"]], "disagrees_with_note": []}


def test_editing_a_note_marks_the_piece_for_redigest(env):
    a = add(env)
    env.llm.replies = [mind_reply(views=[{"text": "v", "sources": [a["id"]]}])]
    refresh(env)
    env.client.patch(f"/api/pieces/{a['id']}", json={"note": "我改主意了"})
    assert env.client.get("/api/pieces/mind").json()["pending"] == 1
    env.llm.replies = [mind_reply(views=[{"text": "v2", "sources": [a["id"]]}])]
    assert refresh(env)["mind"]["views"][0]["text"] == "v2"
    assert "我改主意了" in env.llm.calls[1]["messages"][1]["content"]


def test_deleting_the_only_source_drops_the_view_without_a_model_call(env):
    a, b = add(env), add(env, "第二段。")
    env.llm.replies = [mind_reply(views=[{"text": "只靠 a", "sources": [a["id"]]},
                                         {"text": "靠 a 和 b", "sources": [a["id"], b["id"]]}])]
    refresh(env)
    env.client.delete(f"/api/pieces/{a['id']}")
    assert env.client.get("/api/pieces/mind").json()["mind"]["views"] == [
        {"text": "靠 a 和 b", "sources": [b["id"]], "disagrees_with_note": []}]
    state = refresh(env)
    assert state["pending"] == 0 and len(env.llm.calls) == 1


def test_failed_refresh_keeps_the_old_mind_and_reports_the_error(env):
    a = add(env)
    env.llm.replies = [mind_reply(views=[{"text": "旧看法", "sources": [a["id"]]}])]
    refresh(env)
    add(env, "新的一段。")
    env.llm.replies = [text("不是 JSON")]
    res = env.client.post("/api/pieces/mind/refresh")
    assert res.status_code == 502 and res.json()["error_code"] == "mind_update_failed"
    state = env.client.get("/api/pieces/mind").json()
    assert state["mind"]["views"][0]["text"] == "旧看法"
    assert state["pending"] == 1 and state["error"].startswith("mind_update_failed")


def test_large_backlog_is_digested_in_batches(env, monkeypatch):
    monkeypatch.setattr(mind_mod, "BATCH", 2)
    ids = [add(env, f"第{i}段。")["id"] for i in range(5)]
    env.llm.replies = [mind_reply(views=[{"text": f"v{i}", "sources": [ids[0]]}]) for i in range(3)]
    assert refresh(env)["pending"] == 0
    assert len(env.llm.calls) == 3


def test_chat_prompt_carries_the_mind_and_its_stance(env):
    a = add(env, note="我觉得太鸡汤")
    env.llm.replies = [mind_reply(views=[{"text": "我相信过程本身算数。", "sources": [a["id"]]}])]
    refresh(env)
    env.llm.replies = [text("我不太同意你。")]
    env.client.post("/api/pieces/chat", json={"message": "你怎么看努力？"})
    system = env.llm.calls[-1]["messages"][0]["content"]
    assert f"- 我相信过程本身算数。（来自 #{a['id']}）" in system
    assert "不是用户的助手" in system and "替我说" not in system
    assert "还没消化" not in system


def test_chat_prompt_says_when_the_mind_is_behind(env):
    add(env)
    env.llm.replies = [text("嗯。")]
    env.client.post("/api/pieces/chat", json={"message": "你好"})
    system = env.llm.calls[-1]["messages"][0]["content"]
    assert "还有 1 条收藏的变化你还没消化" in system and "我相信的：（还没有）" in system
