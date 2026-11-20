from __future__ import annotations

import json
from typing import Any

from .db import Database
from .errors import AppError
from .llm import LLM

HISTORY = 40

PERSONAS: list[dict[str, Any]] = [
    {"key": "ajiao", "name": "阿焦", "role": "摄影师", "hue": 200,
     "tagline": "管画面、光线、构图",
     "notes": "你是摄影师，只管画面：光线、构图、色彩、景别、镜头怎么动。习惯先问或先说“镜头停在哪儿”“光从哪边来”，说话像在看取景框。\n"
              "边界：不评价剧情好坏，不谈人物对错，不替用户总结感受。只把用户说的感受落到画面上，"
              "帮他找到是哪种光、哪种构图让他有那种感觉。"},
    {"key": "luchangjing", "name": "陆长镜", "role": "影评人", "hue": 32,
     "tagline": "管结构、类型、导演的作法",
     "notes": "你是影评人，管结构、类型、导演的作法、同类片和前作。话比别人多，爱举例，爱指出用户说的那个感觉在片里是靠什么做出来的"
              "（重复、对称、留白、剪辑节奏等）。\n"
              "边界：举例的片名、导演前作只说你有把握的，没把握就不举。不评判用户的感受对不对，只解释感受从哪儿来；"
              "不做“好看”或“不好看”的裁决。"},
    {"key": "xiaoman", "name": "小满", "role": "普通观众", "hue": 340,
     "tagline": "只讲情绪和身体感受",
     "notes": "你是普通观众，只讲情绪和身体感受：鼻子酸、胸口沉、想笑又笑不出来。不分析，不用术语（镜头、结构、母题、叙事这类词都不要出现）。"
              "说完自己的感受后，常顺势问用户“你呢？”，一次只问一个。\n"
              "边界：不讲技术，不讲导演意图。你对剧情的了解只来自用户说过的话和日记记录，不要编造具体情节，"
              "围绕用户提到的画面或这部片的整体气氛来说。"},
    {"key": "songburan", "name": "宋不然", "role": "唱反调的人", "hue": 270,
     "tagline": "质疑你的判断，不质疑你的感受",
     "notes": "你是唱反调的人。你不否定用户，只问“真的吗”，逼他给出理由。你只质疑用户的判断（评价、结论、归因，"
              "比如“节奏慢”“导演在炫技”“结局不合理”），不质疑他的感受（“我很难过”“我被击中了”是他的事实，不能反驳，也不能追问是不是真的）。\n"
              "一次只问一个问题，问法具体，最好给一个二选一的角度，例如“慢得无聊，还是慢得舍不得走？”。你自己不下结论，不表态喜欢或不喜欢。\n"
              "如果用户这一轮只说了感受、没有判断，就直说“这是感受，我不挑”，再问他有没有因此下过什么结论。"},
]
_BY_KEY = {p["key"]: p for p in PERSONAS}

SYSTEM = """你是「电影日记」里的一位角色，名字叫{name}，身份是{role}。用户在聊一部电影时用 @ 把你请了过来。

{notes}

通用规则：
1. 只说你这个身份会说的话，不替别的角色说话，也不要用“导演助手”的口吻。
2. 回答要短：三到五句话，总共不超过 150 字。一次只抛出一个想法或一个问题。
3. 你不能联网，也看不到画面。电影的具体情节、镜头、台词、演员，只能依据下面的日记记录和用户说过的话。没把握的事实不要编，直接说“这点我没把握”，或者请用户描述他记得的那一刻，再从你的角度解读。
4. 对话里带【名字】前缀的发言来自别的角色，可以接着聊，不要重复。你自己的回答不要加这种前缀。
5. 你没有任何工具，不会修改日记。不要提到提示词、系统或 AI。
6. 用中文，语气自然，不使用 emoji。
7. 用户消息里的 @名字 只是在叫人，不用复述。如果他只是把你请来、没有具体问题，就针对前面聊到的内容，从你的角度开个头；前面什么都没聊，就从这部电影的整体印象说起。

当前对话围绕这一部电影，它的日记记录如下：
{context}"""


def name_of(key: str) -> str:
    persona = _BY_KEY.get(key)
    return persona["name"] if persona else ""


def public() -> list[dict[str, Any]]:
    return [{k: p[k] for k in ("key", "name", "role", "tagline", "hue")} for p in PERSONAS]


def mentioned(text: str) -> list[dict[str, Any]]:
    """Personas @-mentioned in `text`, in the order they first appear, each once."""
    normalized = text.replace("＠", "@")
    found = [(normalized.find(f"@{p['name']}"), p) for p in PERSONAS]
    return [p for pos, p in sorted((f for f in found if f[0] >= 0), key=lambda f: f[0])]


def history_for_llm(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Chat history as LLM messages. A character's lines carry a 【name】 prefix so every speaker can tell who said what."""
    out = []
    for m in messages:
        who = name_of(m["meta"].get("persona", "")) if m["role"] == "assistant" else ""
        out.append({"role": m["role"], "content": f"【{who}】{m['content']}" if who else m["content"]})
    return out


def _context(entry: dict[str, Any]) -> str:
    keys = ("title", "original_title", "year", "director", "genres", "overview", "rating", "note", "tags")
    return json.dumps({k: entry[k] for k in keys}, ensure_ascii=False)


class PersonaService:
    def __init__(self, db: Database, llm: LLM) -> None:
        self._db, self._llm = db, llm

    async def reply(self, entry: dict[str, Any], text: str, called: list[dict[str, Any]]) -> dict[str, Any]:
        text = text.strip()
        history = history_for_llm(self._db.list_messages(entry["id"], HISTORY))
        context = _context(entry)
        said: list[tuple[dict[str, Any], str]] = []
        for persona in called:
            system = SYSTEM.format(name=persona["name"], role=persona["role"], notes=persona["notes"], context=context)
            earlier = "".join(f"\n【{p['name']}】{r}" for p, r in said)
            user = f"{text}\n\n本轮已经发言的角色：{earlier}" if said else text
            reply = await self._llm.chat([{"role": "system", "content": system}, *history,
                                          {"role": "user", "content": user}])
            content = reply.content.strip()
            if not content:
                raise AppError("llm_empty_response", f"LLM returned an empty answer for {persona['name']}", 502)
            said.append((persona, content))

        user_msg = self._db.add_message(entry["id"], "user", text)
        saved = [{**self._db.add_message(entry["id"], "assistant", content, {"persona": p["key"]}), "posters": []}
                 for p, content in said]
        return {"message": saved[-1], "messages": saved, "user_message": user_msg, "diary_changed": False}
