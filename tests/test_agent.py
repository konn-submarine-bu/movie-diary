from __future__ import annotations

import json

from app.errors import AppError
from tests.conftest import BRIEF_JSON, text, tool


def chat(env, message, entry_id=None):
    path = "/api/chat" if entry_id is None else f"/api/entries/{entry_id}/chat"
    return env.client.post(path, json={"message": message})


def tool_results(env):
    """Tool messages the LLM saw on its most recent call."""
    return [json.loads(m["content"]) for m in env.llm.calls[-1]["messages"] if m["role"] == "tool"]


def test_plain_reply_is_persisted(env):
    env.llm.replies = [text("听起来这部片戳中了你。最打动你的是哪个画面？")]
    res = chat(env, "刚看完，有点难过")
    assert res.status_code == 200
    assert res.json()["message"]["content"].startswith("听起来")
    hist = env.client.get("/api/chat").json()["messages"]
    assert [m["role"] for m in hist] == ["user", "assistant"]


def test_agent_searches_then_adds_movie(env):
    env.llm.replies = [
        tool("search_movie", {"query": "花样年华"}),
        tool("add_to_diary", {"tmdb_id": 2, "rating": 5, "note": "错过比拥有更长久"}, "c2"),
        text("已经记进日记了。"),
    ]
    res = chat(env, "我今天看了花样年华，五星，错过比拥有更长久").json()
    assert res["diary_changed"] is True
    entries = env.client.get("/api/entries").json()["entries"]
    assert len(entries) == 1 and entries[0]["rating"] == 5 and entries[0]["note"] == "错过比拥有更长久"


def test_web_search_collects_sources(env):
    env.llm.replies = [tool("web_search", {"query": "花样年华 解读"}), text("据 [解读](https://example.com/a)，片子写的是克制。")]
    res = chat(env, "帮我找找花样年华的解读").json()
    assert res["message"]["meta"]["sources"] == [{"title": "《花样年华》解读", "url": "https://example.com/a"}]
    assert env.search.queries == ["花样年华 解读"]


def test_tool_error_is_passed_to_llm_with_code(env):
    from app.errors import AppError
    env.search.error = AppError("search_unreachable", "Cannot reach fake search. ConnectError: boom", 502)
    env.llm.replies = [tool("web_search", {"query": "x"}), text("搜索失败了：search_unreachable。")]
    chat(env, "查一下")
    seen = tool_results(env)[0]
    assert seen["error_code"] == "search_unreachable"
    assert "ConnectError" in seen["error"]


def test_bad_tool_arguments_do_not_crash(env):
    env.llm.replies = [tool("search_movie", {}), text("我没拿到片名，能再说一次吗？")]
    res = chat(env, "找电影")
    assert res.status_code == 200
    assert tool_results(env)[0]["error_code"] == "tool_arguments_invalid"


def test_invalid_rating_from_llm_is_rejected(env):
    entry = env.add_entry(1)
    env.llm.replies = [tool("update_diary", {"entry_id": entry["id"], "rating": 10}), text("评分只能是 1 到 5。")]
    chat(env, "给十分")
    assert tool_results(env)[0]["error_code"] == "rating_invalid"
    assert env.client.get(f"/api/entries/{entry['id']}").json()["rating"] is None


def test_adding_duplicate_via_agent_reports_exists(env):
    env.add_entry(1)
    env.llm.replies = [tool("add_to_diary", {"tmdb_id": 1}), text("已经在日记里了。")]
    chat(env, "再记一次")
    assert tool_results(env)[0]["error_code"] == "entry_already_exists"


def test_step_limit_returns_error_not_hang(env):
    env.llm.replies = [tool("web_search", {"query": "a"}, f"c{i}") for i in range(4)]
    res = chat(env, "一直搜")
    assert res.status_code == 502
    assert res.json()["error_code"] == "agent_step_limit"
    assert env.client.get("/api/chat").json()["messages"] == []


def test_entry_chat_injects_entry_context(env):
    entry = env.add_entry(1, note="想起外婆家")
    env.llm.replies = [text("外婆家的哪个细节最先浮现？")]
    chat(env, "我总想起一些旧事", entry["id"])
    system = env.llm.calls[0]["messages"][0]["content"]
    assert "路过人间" in system and "想起外婆家" in system


def test_chats_are_scoped_per_entry(env):
    a, b = env.add_entry(1), env.add_entry(2)
    env.llm.replies = [text("A 的回复"), text("B 的回复")]
    chat(env, "关于 A", a["id"])
    chat(env, "关于 B", b["id"])
    assert [m["content"] for m in env.client.get(f"/api/entries/{a['id']}/chat").json()["messages"]] == ["关于 A", "A 的回复"]
    assert env.client.get("/api/chat").json()["messages"] == []


def test_llm_error_surfaces_code_and_description(env):
    class Boom:
        async def chat(self, messages, tools=None):
            raise AppError("llm_unreachable", "Cannot reach LLM_BASE_URL. APIConnectionError: refused", 502)
    from fastapi.testclient import TestClient
    from app.main import create_app
    from tests.conftest import FakeTMDB
    client = TestClient(create_app(env.settings, Boom(), env.images, FakeTMDB(), env.search))
    res = client.post("/api/chat", json={"message": "hi"})
    assert res.status_code == 502
    assert res.json()["error_code"] == "llm_unreachable"
    assert "APIConnectionError" in res.json()["error"]


def test_make_poster_tool_attaches_poster_to_reply(env):
    entry = env.add_entry(2)
    env.llm.replies = [
        tool("make_poster", {"entry_id": entry["id"], "quote": "那些消逝了的岁月，仿佛隔着一块积着灰尘的玻璃"}),
        text(BRIEF_JSON),  # consumed by the poster brief call
        text("海报做好了。"),
    ]
    res = chat(env, "把这句台词做成海报", entry["id"]).json()
    assert len(res["message"]["posters"]) == 1
    hist = env.client.get(f"/api/entries/{entry['id']}/chat").json()["messages"]
    assert hist[-1]["posters"][0]["quote"].startswith("那些消逝了的岁月")
