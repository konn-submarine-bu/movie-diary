from __future__ import annotations

import json

from tests.conftest import text, tool


def summary(*ops) -> object:
    return text(json.dumps({"impressions": list(ops)}, ensure_ascii=False))


def add(kind, short, **kw):
    return {"op": "add", "kind": kind, "short": short, **kw}


def chat(env, entry_id, message="婚宴那场很好笑，我却有点难受"):
    env.llm.replies = [text("好笑和难受同时发生。")]
    res = env.client.post(f"/api/entries/{entry_id}/chat", json={"message": message})
    assert res.status_code == 200, res.text
    return res.json()


def refresh(env, entry_id, *ops):
    env.llm.replies = [summary(*ops)]
    res = env.client.post(f"/api/entries/{entry_id}/impressions/refresh")
    assert res.status_code == 200, res.text
    return res.json()


def patch(env, imp_id, **body):
    res = env.client.patch(f"/api/impressions/{imp_id}", json=body)
    assert res.status_code == 200, res.text
    return res.json()


def entry_of(env, entry_id):
    return env.client.get(f"/api/entries/{entry_id}").json()


def test_refresh_creates_draft_bubbles_with_valid_sources(env):
    e = env.add_entry(1)
    msg = chat(env, e["id"])["user_message"]["id"]
    out = refresh(env, e["id"], add("feel", "好笑，也难受", text="好笑与难受同时发生", source_msg_ids=[msg, 9999]))
    assert out["added"] == 1
    imp = out["impressions"][0]
    assert imp["status"] == "draft" and imp["source_msg_ids"] == [msg]
    assert entry_of(env, e["id"])["note"] == ""


def test_refresh_without_new_user_turn_does_not_call_the_model(env):
    e = env.add_entry(1)
    chat(env, e["id"])
    refresh(env, e["id"], add("feel", "好笑，也难受"))
    env.llm.replies = []
    res = env.client.post(f"/api/entries/{e['id']}/impressions/refresh").json()
    assert res["added"] == 0 and len(res["impressions"]) == 1


def test_adopting_one_writes_entry_note_and_rejecting_clears_it(env):
    e = env.add_entry(1)
    chat(env, e["id"])
    imp = refresh(env, e["id"], add("one", "笑声里藏着沉默", text="笑声里藏着一个父亲的沉默。"))["impressions"][0]
    out = patch(env, imp["id"], status="ok")
    assert out["entry"]["note"] == "笑声里藏着一个父亲的沉默。"
    assert patch(env, imp["id"], status="rej")["entry"]["note"] == ""


def test_editing_a_draft_calibrates_it_and_writes_the_note(env):
    e = env.add_entry(1)
    chat(env, e["id"])
    imp = refresh(env, e["id"], add("one", "笑声里藏着沉默"))["impressions"][0]
    out = patch(env, imp["id"], text="我改过的版本")
    assert out["impression"]["status"] == "edit" and out["entry"]["note"] == "我改过的版本"
    out = patch(env, imp["id"], status="ok")
    assert out["entry"]["note"] == "我改过的版本"
    out = patch(env, imp["id"], text="再改一次")
    assert out["impression"]["status"] == "edit" and out["entry"]["note"] == "再改一次"


def test_adopting_a_second_one_demotes_the_first(env):
    e = env.add_entry(1)
    a = env.client.post(f"/api/entries/{e['id']}/impressions", json={"short": "甲"}).json()["impression"]
    patch(env, a["id"], status="rej")
    chat(env, e["id"])
    one = refresh(env, e["id"], add("one", "乙"))["impressions"][-1]
    patch(env, one["id"], status="ok")
    two = env.client.get(f"/api/entries/{e['id']}/impressions").json()["impressions"]
    assert [i["status"] for i in two if i["kind"] == "one"] == ["ok"]
    mine = env.client.post(f"/api/entries/{e['id']}/impressions", json={"short": "丙"}).json()
    assert mine["impression"]["kind"] == "mine" and mine["impression"]["status"] == "ok"
    assert mine["entry"]["note"] == "乙"


def test_rejecting_does_not_clear_a_note_the_user_typed_by_hand(env):
    e = env.add_entry(1)
    chat(env, e["id"])
    imp = refresh(env, e["id"], add("one", "笑声里藏着沉默"))["impressions"][0]
    patch(env, imp["id"], status="ok")
    env.client.patch(f"/api/entries/{e['id']}", json={"note": "手写的感受"})
    assert patch(env, imp["id"], status="rej")["entry"]["note"] == "手写的感受"


def test_adopting_rate_writes_rating_and_needs_stars(env):
    e = env.add_entry(1)
    chat(env, e["id"])
    imp = refresh(env, e["id"], add("rate", "克制，但很重", stars=4), add("rate", "无星", stars=0))["impressions"]
    assert len(imp) == 1 and imp[0]["stars"] == 4
    assert patch(env, imp[0]["id"], status="ok")["entry"]["rating"] == 4
    assert patch(env, imp[0]["id"], stars=5)["entry"]["rating"] == 5
    assert patch(env, imp[0]["id"], status="rej")["entry"]["rating"] is None


def test_adopted_and_calibrated_bubbles_are_never_overwritten_or_duplicated(env):
    e = env.add_entry(1)
    chat(env, e["id"])
    feel = refresh(env, e["id"], add("feel", "好笑，也难受"))["impressions"][0]
    patch(env, feel["id"], status="ok")
    chat(env, e["id"], "还有一点想说")
    out = refresh(env, e["id"],
                  {"op": "update", "id": feel["id"], "short": "被模型改掉了"},
                  add("feel", "好笑也难受"),
                  add("idea", "想写一个短篇"))
    shorts = {i["short"] for i in out["impressions"]}
    assert "好笑，也难受" in shorts and "被模型改掉了" not in shorts
    assert out["added"] == 1 and out["updated"] == 0


def test_draft_can_be_rewritten_by_the_model(env):
    e = env.add_entry(1)
    chat(env, e["id"])
    d = refresh(env, e["id"], add("feel", "有点难受"))["impressions"][0]
    chat(env, e["id"], "再补充一点")
    out = refresh(env, e["id"], {"op": "update", "id": d["id"], "short": "替父亲难受", "text": "替父亲难受"})
    assert out["updated"] == 1 and out["impressions"][0]["short"] == "替父亲难受"
    assert out["impressions"][0]["status"] == "draft"


def test_rejected_content_is_not_proposed_again(env):
    e = env.add_entry(1)
    chat(env, e["id"])
    d = refresh(env, e["id"], add("word", "隐忍"))["impressions"][0]
    patch(env, d["id"], status="rej")
    chat(env, e["id"], "还有别的")
    out = refresh(env, e["id"], add("word", "隐忍"), add("word", "体面"))
    assert out["added"] == 1
    assert [i["short"] for i in out["impressions"] if i["status"] == "draft"] == ["体面"]
    assert "隐忍" in env.llm.calls[-1]["messages"][-1]["content"]


def test_at_most_three_new_bubbles_and_bad_ops_are_skipped(env):
    e = env.add_entry(1)
    chat(env, e["id"])
    out = refresh(env, e["id"], add("feel", "一"), add("idea", "二二"), add("word", "三三三"), add("word", "四四四四"),
                  add("bogus", "x"), {"op": "add", "kind": "feel"}, "junk",
                  add("word", "这个词实在是太长了太长了太长了太长了"))
    assert out["added"] == 3 and len(out["impressions"]) == 3


def test_malformed_model_output_retries_then_reports(env):
    e = env.add_entry(1)
    chat(env, e["id"])
    env.llm.replies = [text("好的，这是结果"), summary(add("feel", "难受"))]
    assert env.client.post(f"/api/entries/{e['id']}/impressions/refresh").json()["added"] == 1
    chat(env, e["id"], "再来")
    env.llm.replies = [text("不是 JSON"), text("还是不是")]
    res = env.client.post(f"/api/entries/{e['id']}/impressions/refresh")
    assert res.status_code == 502 and res.json()["error_code"] == "impression_summary_invalid"


def test_existing_note_and_rating_are_shown_as_calibrated_bubbles_once(env):
    e = env.add_entry(1, rating=5, note="克制到令人心碎")
    imps = env.client.get(f"/api/entries/{e['id']}/impressions").json()["impressions"]
    assert {(i["kind"], i["status"]) for i in imps} == {("one", "edit"), ("rate", "edit")}
    assert len(env.client.get(f"/api/entries/{e['id']}/impressions").json()["impressions"]) == 2
    one = next(i for i in imps if i["kind"] == "one")
    patch(env, one["id"], status="rej")
    assert len(env.client.get(f"/api/entries/{e['id']}/impressions").json()["impressions"]) == 2


def test_position_drag_and_validation(env):
    e = env.add_entry(1)
    m = env.client.post(f"/api/entries/{e['id']}/impressions", json={"short": "我的"}).json()["impression"]
    got = patch(env, m["id"], pos_x=0.25, pos_y=0.5)["impression"]
    assert (got["pos_x"], got["pos_y"], got["status"]) == (0.25, 0.5, "ok")
    assert env.client.patch(f"/api/impressions/{m['id']}", json={"pos_x": 3}).status_code == 422
    assert env.client.patch("/api/impressions/999", json={"status": "ok"}).json()["error_code"] == "impression_not_found"
    assert env.client.delete("/api/impressions/999").status_code == 404


def test_delete_impression_and_cascade_with_entry(env):
    e = env.add_entry(1)
    m = env.client.post(f"/api/entries/{e['id']}/impressions", json={"short": "我的"}).json()["impression"]
    assert env.client.delete(f"/api/impressions/{m['id']}").status_code == 200
    assert env.client.get(f"/api/entries/{e['id']}/impressions").json()["impressions"] == []
    env.client.post(f"/api/entries/{e['id']}/impressions", json={"short": "又一个"})
    env.client.delete(f"/api/entries/{e['id']}")
    assert env.client.get(f"/api/entries/{e['id']}/impressions").status_code == 404


def test_offer_choices_is_saved_on_the_message(env):
    e = env.add_entry(1)
    env.llm.replies = [tool("offer_choices", {"choices": ["替父母", "替伟同", " "]}), text("难受是替谁？")]
    msg = env.client.post(f"/api/entries/{e['id']}/chat", json={"message": "有点难受"}).json()["message"]
    assert msg["meta"]["choices"] == ["替父母", "替伟同"]
    hist = env.client.get(f"/api/entries/{e['id']}/chat").json()["messages"]
    assert hist[-1]["meta"]["choices"] == ["替父母", "替伟同"]


def test_film_info_is_cached_and_needs_a_tmdb_id(env):
    e = env.add_entry(1)
    a = env.client.get(f"/api/entries/{e['id']}/film").json()["info"]
    env.client.get(f"/api/entries/{e['id']}/film")
    assert a["runtime"] == 99 and env.tmdb.film_calls == 1
    bare = env.client.post("/api/entries", json={"title": "无编号", "year": 2020}).json()
    res = env.client.get(f"/api/entries/{bare['id']}/film")
    assert res.status_code == 404 and res.json()["error_code"] == "film_info_unavailable"


def test_reviews_pass_search_results_through_verbatim(env):
    e = env.add_entry(2)
    out = env.client.get(f"/api/entries/{e['id']}/reviews").json()
    assert out["reviews"] == [{"title": "《花样年华》解读", "url": "https://example.com/a", "snippet": "关于克制与错过"}]
    assert "花样年华" in env.search.queries[0]


def test_compose_hints_strip_the_repeated_draft(env):
    e = env.add_entry(1)
    env.llm.replies = [text(json.dumps({
        "continuations": [{"label": "续写", "text": "他选择装作不知道"},
                          {"label": "换个角度", "text": "替父亲难受，更像无奈"}, {"label": "x", "text": " "}],
        "words": ["隐忍", "体面", "", "这个词太长了太长了太长了"]}, ensure_ascii=False))]
    out = env.client.post(f"/api/entries/{e['id']}/compose-hints", json={"draft": "替父亲难受"}).json()
    assert [c["text"] for c in out["continuations"]] == ["他选择装作不知道", "，更像无奈"]
    assert out["words"] == ["隐忍", "体面"]
    assert env.client.post(f"/api/entries/{e['id']}/compose-hints", json={"draft": ""}).status_code == 422
