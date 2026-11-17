"""Isolated demo backend used to record the README animations.

The front end is the real one. The model, TMDB lookups, search and Wikiquote are scripted stand-ins, so
recording needs no API key and never touches a real diary. Posters and stills are the real TMDB image
URLs saved in tmdb_fixture.json, so recording needs access to image.tmdb.org.

    python docs/demo/demo_server.py --port 8130 --data /tmp/movie-diary-demo
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.llm import ChatReply, ToolCall  # noqa: E402
from app.main import create_app  # noqa: E402
from app.quotes import Wikiquote, parse_quotes  # noqa: E402
from app.tmdb import TMDB  # noqa: E402

MOVIES: dict[int, dict[str, Any]] = {m["tmdb_id"]: {"poster_url": "", **m} for m in [
    {"tmdb_id": 207, "title": "死亡诗社", "original_title": "Dead Poets Society", "year": 1989,
     "director": "彼得·威尔", "genres": ["剧情"],
     "overview": "一位诗歌老师来到保守的寄宿学校，教学生用自己的声音说话。"},
    {"tmdb_id": 843, "title": "花样年华", "original_title": "In the Mood for Love", "year": 2000,
     "director": "王家卫", "genres": ["爱情", "剧情"],
     "overview": "1962 年的香港，两个租客发现各自的伴侣之间有了关系。"},
    {"tmdb_id": 11104, "title": "重庆森林", "original_title": "Chungking Express", "year": 1994,
     "director": "王家卫", "genres": ["爱情", "喜剧", "剧情"],
     "overview": "香港的两段故事：两个失恋的警察，各自遇到了一个陌生的女人。"},
    {"tmdb_id": 129, "title": "千与千寻", "original_title": "千と千尋の神隠し", "year": 2001,
     "director": "宫崎骏", "genres": ["动画", "奇幻", "家庭"],
     "overview": "十岁的千寻误入神灵世界，要在汤屋打工，才能救回变成猪的父母。"},
    {"tmdb_id": 496243, "title": "寄生虫", "original_title": "기생충", "year": 2019,
     "director": "奉俊昊", "genres": ["喜剧", "惊悚", "剧情"],
     "overview": "半地下的一家人，一步步走进富人家的生活。"},
    {"tmdb_id": 27205, "title": "盗梦空间", "original_title": "Inception", "year": 2010,
     "director": "克里斯托弗·诺兰", "genres": ["科幻", "动作", "冒险"],
     "overview": "一个能潜入别人梦境的小偷，接到了反向植入一个念头的任务。"},
    {"tmdb_id": 438631, "title": "沙丘", "original_title": "Dune", "year": 2021,
     "director": "丹尼斯·维伦纽瓦", "genres": ["科幻", "冒险"],
     "overview": "公爵之子保罗随家族来到沙漠星球厄拉科斯，卷入一场争夺香料的战争。"},
    {"tmdb_id": 313369, "title": "爱乐之城", "original_title": "La La Land", "year": 2016,
     "director": "达米恩·查泽雷", "genres": ["爱情", "喜剧", "音乐"],
     "overview": "洛杉矶，一个想当演员的女孩遇到了一个想开爵士俱乐部的钢琴手。"},
]}

FIXTURE = json.loads((Path(__file__).with_name("tmdb_fixture.json")).read_text(encoding="utf-8"))
for _id, _m in MOVIES.items():
    _m["poster_url"] = FIXTURE[str(_id)]["poster_url"]


class DemoTMDB(TMDB):
    def __init__(self) -> None:
        super().__init__(Settings(data_dir=Path("."), tmdb_api_key="demo"))

    async def search(self, query, year=None):
        q = query.lower()
        return [{k: m[k] for k in ("tmdb_id", "title", "original_title", "year", "overview", "poster_url")}
                for m in MOVIES.values() if q in m["title"].lower() or q in m["original_title"].lower()]

    async def details(self, tmdb_id):
        m = MOVIES[tmdb_id]
        return {k: m[k] for k in ("tmdb_id", "title", "original_title", "year", "director", "overview",
                                  "poster_url", "genres")}

    async def stills(self, tmdb_id, limit=8):
        return FIXTURE[str(tmdb_id)]["stills"][:limit]

    async def original_title(self, tmdb_id):
        return MOVIES[tmdb_id]["original_title"]

    async def film_info(self, tmdb_id):
        info = FILM_INFO.get(tmdb_id)
        if info is None:
            return await super().film_info(tmdb_id)
        similar = [{"tmdb_id": t, "title": MOVIES[t]["title"], "year": MOVIES[t]["year"],
                    "poster_url": MOVIES[t]["poster_url"]} for t in info["similar"]]
        return {**{k: v for k, v in info.items() if k != "similar"}, "similar": similar}


FILM_INFO: dict[int, dict[str, Any]] = {
    843: {"original_title": "花樣年華", "tagline": "那些消逝了的岁月，仿佛隔着一块积着灰尘的玻璃",
          "runtime": 98, "countries": ["中国香港", "法国"], "cast": ["梁朝伟", "张曼玉", "潘迪华", "雷凯华"],
          "keywords": ["婚外情", "邻居", "旗袍", "克制", "六十年代", "吴哥窟"],
          "ratings": [{"source": "TMDB", "value": "8.1", "votes": 2900}],
          "similar": [11104, 129]},
}

DPS_WIKITEXT = """== John Keating ==
* Carpe diem. Seize the day, boys. Make your lives extraordinary.
* No matter what anybody tells you, words and ideas can change the world.
* Boys, you must strive to find your own voice.
== Todd Anderson ==
* O Captain! My Captain!
"""


class DemoWikiquote(Wikiquote):
    async def find(self, titles):
        names = {t for _, t in titles}
        if not names & {"Dead Poets Society", "死亡诗社"}:
            return {"groups": [], "tried": [f"{lang}:{t}" for lang, t in titles]}
        url = "https://en.wikiquote.org/wiki/Dead_Poets_Society"
        quotes = [{**q, "source": url, "lang": "en"} for q in parse_quotes(DPS_WIKITEXT)]
        return {"groups": [{"lang": "en", "source": url, "quotes": quotes}], "tried": ["en:Dead Poets Society"]}


class DemoSearch:
    provider = "demo"

    async def search(self, query, limit=5):
        if "影评" in query:
            return [{"title": "示例来源 · 《花样年华》影评", "url": "https://example.com/in-the-mood-for-love-review",
                     "snippet": "全片几乎没有一个直白的表白，所有的感情都藏在擦肩而过的走廊和旗袍的颜色里。"},
                    {"title": "示例来源 · 《花样年华》长评", "url": "https://example.com/in-the-mood-for-love-essay",
                     "snippet": "王家卫用遮挡和留白拍欲言又止：门框、墙角、窗帘，人物总是被切掉一半。"}]
        return [{"title": "示例来源 · 《花样年华》结尾解读", "url": "https://example.com/in-the-mood-for-love-ending",
                 "snippet": "周慕云在吴哥窟对着墙洞低语，再用泥土封上。"}]


class DemoImages:
    async def generate(self, prompt: str) -> bytes:
        raise RuntimeError("AI poster generation is not part of the demo")


def build_scripts() -> list[tuple[str, list[tuple[str, Any]]]]:
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    return [
        ("盗梦空间", [
            ("tool", ("search_movie", {"query": "盗梦空间"})),
            ("tool", ("add_to_diary", {"tmdb_id": 27205, "rating": 4, "watched_on": yesterday,
                                        "note": "梦境一层套一层，最后那个陀螺转得我睡不着。"})),
            ("text", "记下了：《盗梦空间》，4 星，昨天看的。\n\n最后那个陀螺，你是更愿意相信它倒了，还是一直转下去？"),
        ]),
        ("吴哥窟", [
            ("tool", ("web_search", {"query": "花样年华 结尾 吴哥窟 墙洞 秘密"})),
            ("text", "电影没有给出答案。周慕云在吴哥窟对着墙洞低语，再用泥土封上，观众听不到内容。"
                     "常见的解读是：有些感情说不出口，只能交给一个不会泄露的地方。\n\n"
                     "[示例来源 · 《花样年华》结尾解读](https://example.com/in-the-mood-for-love-ending)"),
        ]),
    ]


# 单片页：整理印象和输入补全的演示脚本。整理按对话里出现的关键词，一轮一批。
SUMMARY_BATCHES = [
    ("走廊", [
        {"op": "add", "kind": "feel", "short": "擦肩而过，什么都没说", "text": "最难受的是两个人明明都懂，却一直没有说破。"},
        {"op": "add", "kind": "word", "short": "克制"},
    ]),
    ("重看", [
        {"op": "add", "kind": "one", "short": "爱情藏在没说出口的话里", "text": "爱情不在表白里，在那些没说出口的话里。"},
        {"op": "add", "kind": "rate", "short": "重看一次，更重了", "stars": 5},
        {"op": "add", "kind": "idea", "short": "写两个人擦肩的短篇", "text": "想写一个关于两个人在走廊里擦肩而过的短篇。"},
    ]),
]

HINTS = {
    "走廊": {"continuations": [
        {"label": "续写", "text": "他们都在等对方先开口，可谁也没有。"},
        {"label": "换个角度", "text": "更像是一种体面，不愿越过那条线。"},
        {"label": "更抒情", "text": "像隔着一层玻璃，看得见，碰不到。"}],
        "words": ["隐忍", "体面", "欲言又止", "心照不宣"]},
}

POEMS = {
    "戴望舒": "旗袍与雨\n\n雨落在窄窄的楼梯\n像一句没说完的话\n旗袍的蓝，是一盏\n迟迟不肯熄灭的灯\n\n"
              "我们交换了钥匙和沉默\n走廊的尽头\n有人把秘密\n折进潮湿的夜里",
    "辛波斯卡": "关于走廊的几点说明\n\n走廊是用来经过的。\n两个人侧身而过，\n这是礼貌，\n也是一种预谋。\n\n"
                "没有人能证明他们说过什么。\n墙记得，\n墙也只是墙。",
}


class DemoLLM:
    def __init__(self) -> None:
        self.scripts = build_scripts()
        self.n = 0

    def _summary(self, messages) -> ChatReply:
        body = messages[-1]["content"]
        ops = []
        for key, batch in SUMMARY_BATCHES:
            fresh = [line for line in body.splitlines() if "[新]" in line and key in line]
            if fresh:
                src = [int(fresh[-1].split()[0][1:])]
                ops = [{**op, "source_msg_ids": src} for op in batch]
                break
        return ChatReply(content=json.dumps({"impressions": ops}, ensure_ascii=False))

    async def chat(self, messages, tools=None):
        if tools is None and "小气泡" in messages[0]["content"]:
            return self._summary(messages)
        if tools is None and "写到一半" in messages[0]["content"]:
            draft = messages[-1]["content"]
            for key, hint in HINTS.items():
                if key in draft:
                    return ChatReply(content=json.dumps(hint, ensure_ascii=False))
            return ChatReply(content=json.dumps({"continuations": [], "words": []}))
        if tools is None:
            system = messages[0]["content"]
            for name, poem in POEMS.items():
                if f"文风：{name}" in system:
                    return ChatReply(content=poem)
            return ChatReply(content="夜\n\n一行\n两行")
        last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
        text = messages[last_user]["content"]
        for key, reply in MOVIE_CHAT:
            if key in text:
                step = sum(1 for m in messages[last_user + 1:] if m["role"] == "tool")
                if reply.get("choices") and step == 0:
                    self.n += 1
                    return ChatReply(tool_calls=[ToolCall(id=f"call_{self.n}", name="offer_choices",
                                                          arguments=json.dumps({"choices": reply["choices"]}, ensure_ascii=False))])
                return ChatReply(content=reply["text"])
        step = sum(1 for m in messages[last_user + 1:] if m["role"] == "tool")
        for key, steps in self.scripts:
            if key in text and step < len(steps):
                kind, payload = steps[step]
                if kind == "text":
                    return ChatReply(content=payload)
                self.n += 1
                name, args = payload
                return ChatReply(tool_calls=[ToolCall(id=f"call_{self.n}", name=name,
                                                      arguments=json.dumps(args, ensure_ascii=False))])
        return ChatReply(content="（演示环境没有为这句话准备回复。）")


MOVIE_CHAT = [
    ("走廊", {"text": "走廊那场，两个人侧身而过，什么都没说，却什么都在了。你觉得他们当时是想说，还是不想说？",
              "choices": ["想说，说不出口", "不想说破", "说不清，都有"]}),
    ("重看", {"text": "重看一遍，往往会发现第一次漏掉的东西。这一次，哪个细节让你停下来了？"}),
]


def seed(client: TestClient) -> None:
    today = date.today()
    try:
        last_year = today.replace(year=today.year - 1)
    except ValueError:
        last_year = today.replace(year=today.year - 1, day=28)
    watched = [
        (207, 8, 5, "“Carpe diem”，读诗那一课我全程起鸡皮疙瘩。", ["青春", "诗"]),
        (843, 6, 5, "旗袍和走廊，全是留白。", ["王家卫"]),
        (129, 3, 4, "名字被拿走以后，才知道名字是什么。", ["治愈"]),
        (496243, 2, 4, "楼梯的每一级，都是阶层。", ["社会"]),
    ]
    for tid, ago, rating, note, tags in watched:
        body = {"tmdb_id": tid, "title": MOVIES[tid]["title"], "year": MOVIES[tid]["year"],
                "watched_on": (today - timedelta(days=ago)).isoformat(), "rating": rating, "note": note, "tags": tags}
        assert client.post("/api/entries", json=body).status_code == 201
    old = {"tmdb_id": 11104, "title": "重庆森林", "year": 1994, "watched_on": last_year.isoformat(),
           "rating": 4, "note": "过期的凤梨罐头。", "tags": []}
    assert client.post("/api/entries", json=old).status_code == 201
    wish = {"tmdb_id": 313369, "title": "爱乐之城", "year": 2016, "status": "wishlist", "note": "听说结尾很难忘"}
    assert client.post("/api/entries", json=wish).status_code == 201


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8130)
    ap.add_argument("--data", type=Path)
    args = ap.parse_args()
    assert args.data, "--data is required"
    settings = Settings(data_dir=args.data, llm_base_url="http://demo", llm_api_key="demo", llm_model="demo",
                        image_base_url="http://demo", image_api_key="demo", image_model="demo", tmdb_api_key="demo")
    app = create_app(settings, DemoLLM(), DemoImages(), DemoTMDB(), DemoSearch(), DemoWikiquote())
    seed(TestClient(app))
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
