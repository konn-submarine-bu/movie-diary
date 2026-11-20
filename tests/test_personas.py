from __future__ import annotations

from app.personas import PERSONAS, mentioned
from tests.conftest import text


def say(env, entry_id, message, *replies):
    env.llm.replies = [text(r) for r in replies]
    return env.client.post(f"/api/entries/{entry_id}/chat", json={"message": message})


def test_personas_are_listed_with_distinct_names(env):
    res = env.client.get("/api/personas").json()["personas"]
    assert [p["name"] for p in res] == ["阿焦", "陆长镜", "小满", "宋不然"]
    assert all("notes" not in p for p in res)
    assert len({p["key"] for p in res}) == len(PERSONAS)


def test_mentioned_orders_by_first_appearance_and_dedupes():
    names = [p["name"] for p in mentioned("@小满 你看，@阿焦 怎么说？@小满 再说说")]
    assert names == ["小满", "阿焦"]
    assert [p["name"] for p in mentioned("＠宋不然 真的吗")] == ["宋不然"]
    assert mentioned("小满 没有 at 符号") == []


def test_mention_routes_to_the_character_and_not_the_main_agent(env):
    e = env.add_entry(2)
    res = say(env, e["id"], "@阿焦 那场雨戏你怎么看", "光是从左后方打的。")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["message"]["meta"] == {"persona": "ajiao"}
    assert body["message"]["content"] == "光是从左后方打的。"
    assert len(env.llm.calls) == 1
    assert env.llm.calls[0]["tools"] is None
    system = env.llm.calls[0]["messages"][0]["content"]
    assert "阿焦" in system and "摄影师" in system and "花样年华" in system
    assert body["diary_changed"] is False


def test_two_mentions_speak_in_order_and_the_second_sees_the_first(env):
    e = env.add_entry(2)
    res = say(env, e["id"], "@小满 @陆长镜 说说结尾", "我整个人沉下去了。你呢？", "结尾是前面对称场景被拆掉。")
    assert res.status_code == 200, res.text
    body = res.json()
    assert [m["meta"]["persona"] for m in body["messages"]] == ["xiaoman", "luchangjing"]
    assert body["message"]["meta"]["persona"] == "luchangjing"
    second = env.llm.calls[1]["messages"][-1]["content"]
    assert "【小满】我整个人沉下去了。你呢？" in second
    history = env.client.get(f"/api/entries/{e['id']}/chat").json()["messages"]
    assert [m["role"] for m in history] == ["user", "assistant", "assistant"]


def test_history_marks_who_said_what_for_every_speaker(env):
    e = env.add_entry(2)
    assert say(env, e["id"], "@宋不然 我觉得节奏很慢", "慢得无聊，还是慢得舍不得走？").status_code == 200
    env.llm.replies = [text("好问题。")]
    env.client.post(f"/api/entries/{e['id']}/chat", json={"message": "舍不得走"})
    sent = env.llm.calls[-1]["messages"]
    assert any(m["role"] == "assistant" and m["content"].startswith("【宋不然】") for m in sent)
    assert "【名字】前缀" in sent[0]["content"]
    assert sent[-1]["content"] == "舍不得走"


def test_a_failing_character_saves_nothing(env):
    e = env.add_entry(2)
    res = say(env, e["id"], "@阿焦 @小满 说说", "光很冷。", "   ")
    assert res.status_code == 502
    assert res.json()["error_code"] == "llm_empty_response"
    assert env.client.get(f"/api/entries/{e['id']}/chat").json()["messages"] == []


def test_global_chat_ignores_mentions(env):
    env.llm.replies = [text("在。")]
    res = env.client.post("/api/chat", json={"message": "@阿焦 在吗"})
    assert res.status_code == 200
    assert res.json()["message"]["meta"].get("persona") is None
    assert env.llm.calls[0]["tools"] is not None


def test_quote_message_adds_an_adopted_idea_bubble_with_its_speaker(env):
    e = env.add_entry(2)
    body = say(env, e["id"], "@阿焦 说说", "光是从左后方打的。").json()
    res = env.client.post(f"/api/entries/{e['id']}/impressions/from-message", json={"message_id": body["message"]["id"]})
    assert res.status_code == 201, res.text
    imp = res.json()["impression"]
    assert imp["kind"] == "idea" and imp["status"] == "ok"
    assert imp["source_persona"] == "ajiao" and imp["source_msg_ids"] == [body["message"]["id"]]
    assert imp["text"] == "光是从左后方打的。"
    listed = env.client.get(f"/api/entries/{e['id']}/impressions").json()["impressions"]
    assert [i["id"] for i in listed] == [imp["id"]]


def test_quote_message_rejects_duplicates_user_messages_and_other_entries(env):
    e1, e2 = env.add_entry(1), env.add_entry(2)
    body = say(env, e1["id"], "@小满 你好", "我鼻子酸了。").json()
    url = f"/api/entries/{e1['id']}/impressions/from-message"
    assert env.client.post(url, json={"message_id": body["message"]["id"]}).status_code == 201
    dup = env.client.post(url, json={"message_id": body["message"]["id"]})
    assert dup.status_code == 409 and dup.json()["error_code"] == "message_already_quoted"
    mine = env.client.post(url, json={"message_id": body["user_message"]["id"]})
    assert mine.status_code == 404 and mine.json()["error_code"] == "message_not_quotable"
    other = env.client.post(f"/api/entries/{e2['id']}/impressions/from-message", json={"message_id": body["message"]["id"]})
    assert other.status_code == 404


def test_quoted_bubble_can_be_rejected_and_quoted_again(env):
    e = env.add_entry(2)
    body = say(env, e["id"], "@阿焦 说说", "光是从左后方打的。").json()
    url = f"/api/entries/{e['id']}/impressions/from-message"
    imp = env.client.post(url, json={"message_id": body["message"]["id"]}).json()["impression"]
    assert env.client.patch(f"/api/impressions/{imp['id']}", json={"status": "rej"}).status_code == 200
    assert env.client.post(url, json={"message_id": body["message"]["id"]}).status_code == 201


def test_summary_prompt_names_the_speaker_and_character_lines_do_not_become_user_feelings(env):
    e = env.add_entry(2)
    say(env, e["id"], "@小满 说说", "我鼻子酸了。")
    env.llm.replies = [text('{"impressions": []}')]
    assert env.client.post(f"/api/entries/{e['id']}/impressions/refresh").status_code == 200
    system, prompt = (m["content"] for m in env.llm.calls[-1]["messages"])
    assert "小满[新]: 我鼻子酸了。" in prompt
    assert "助手[新]: 我鼻子酸了。" not in prompt
    assert "不是用户说的" in system
