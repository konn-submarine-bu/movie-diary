from __future__ import annotations

import asyncio
import difflib
import json
import re
from typing import Any

from .db import ACTIVE, Database
from .errors import AppError
from .llm import LLM
from .llmjson import extract_json
from .personas import name_of

CODE = "impression_summary_invalid"
AI_KINDS = ("one", "feel", "rate", "idea", "word")
MAX_NEW = 3
MAX_LIVE = 24
SHORT_MAX = 40
WORD_MAX = 12
TEXT_MAX = 600

PROMPT = """你在帮用户整理看完一部电影后的「印象」。你会看到他和朋友的对话，要从中提炼出几个小气泡。

气泡类型（kind）：
- one：一句话印象，整部片给用户留下的核心感觉，只能有一个。
- feel：具体的感受。
- rate：用户明确给出的评价或打分，必须带 stars（1-5 的整数）。用户没说过评价就不要生成。
- idea：这部片触发的灵感、想做的事。
- word：用户用过或认同的印象词，两到六个字。

规则：
1. 只依据用户自己说的话。用户没表达过的感受、评价、灵感不要替他编。标了角色名的发言（阿焦、陆长镜、小满、宋不然）
   和助手的话都不是用户说的，只能当背景，不能当作用户的感受提炼；除非用户在后面明确表示认同。
2. short 是气泡上显示的短句，不超过 20 个字，尽量保留用户原话的味道；text 是完整的一两句话，不超过 150 字。
3. source_msg_ids 填依据的消息编号（对话里每条消息前的 #编号），只能填用户消息或你确实用到的消息。
4. 已有气泡里 status 为 ok 或 edit 的是用户确认过的，不要改，也不要重复提出相近内容。
5. 「用户驳回过」的内容不要再提出相同或相近的。
6. status 为 draft 的气泡可以用 update 改写得更准确。
7. 没有值得整理的新内容就返回空列表。一次最多新增 3 个。

只输出一个 JSON 对象，不要任何其他文字：
{"impressions":[
 {"op":"add","kind":"feel","short":"...","text":"...","source_msg_ids":[12]},
 {"op":"add","kind":"rate","short":"...","stars":4,"source_msg_ids":[14]},
 {"op":"update","id":3,"short":"...","text":"...","source_msg_ids":[16]}
]}"""


def _norm(s: str) -> str:
    return re.sub(r"[\s，。,.!！?？、:：;；「」『』\"'“”…]+", "", s).lower()


def similar(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    if a == b or (min(len(a), len(b)) >= 4 and (a in b or b in a)):
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def _speaker(m: dict[str, Any]) -> str:
    if m["role"] == "user":
        return "用户"
    return name_of(m["meta"].get("persona", "")) or "助手"


def _clip(value: Any, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


class ImpressionService:
    def __init__(self, db: Database, llm: LLM) -> None:
        self._db, self._llm = db, llm
        self._locks: dict[int, asyncio.Lock] = {}

    async def refresh(self, entry: dict[str, Any]) -> dict[str, Any]:
        lock = self._locks.setdefault(entry["id"], asyncio.Lock())
        async with lock:
            return await self._refresh(entry)

    async def _refresh(self, entry: dict[str, Any]) -> dict[str, Any]:
        eid = entry["id"]
        msgs = self._db.list_messages(eid, 60)
        last = self._db.last_summarized(eid)
        existing = self._db.list_impressions(eid)
        if not any(m["id"] > last and m["role"] == "user" for m in msgs):
            return {"impressions": existing, "added": 0, "updated": 0, "message": "没有新的对话内容可整理"}

        prompt = self._prompt(entry, msgs, last, existing)
        ops: list[Any] = []
        for attempt in range(2):
            reply = await self._llm.chat([{"role": "system", "content": PROMPT}, {"role": "user", "content": prompt}])
            try:
                data = extract_json(reply.content, CODE)
                ops = data.get("impressions", [])
                if not isinstance(ops, list):
                    raise AppError(CODE, "LLM JSON field impressions is not a list", 502)
                break
            except AppError:
                if attempt == 1:
                    raise
        added, updated = self._apply(eid, ops, {m["id"] for m in msgs}, existing)
        self._db.set_last_summarized(eid, msgs[-1]["id"])
        if added or updated:
            note = f"新增 {added} 个气泡" + (f"，改写 {updated} 个" if updated else "")
        else:
            note = "这一轮没有新的印象"
        return {"impressions": self._db.list_impressions(eid), "added": added, "updated": updated, "message": note}

    @staticmethod
    def _prompt(entry: dict[str, Any], msgs: list[dict[str, Any]], last: int, existing: list[dict[str, Any]]) -> str:
        live = [{"id": i["id"], "kind": i["kind"], "status": i["status"], "short": i["short"]}
                for i in existing if i["status"] != "rej"]
        rejected = [i["short"] for i in existing if i["status"] == "rej"]
        lines = [f"#{m['id']} {_speaker(m)}{'[新]' if m['id'] > last else ''}: {m['content']}" for m in msgs]
        head = f"电影：《{entry['title']}》({entry['year'] or '年份不详'})，导演 {entry['director'] or '不详'}"
        return (f"{head}\n\n已有气泡：{json.dumps(live, ensure_ascii=False)}\n\n"
                f"用户驳回过：{json.dumps(rejected, ensure_ascii=False)}\n\n"
                "对话（[新] 是上次整理之后新增的，只需要从新内容里提炼，旧内容仅作背景）：\n" + "\n".join(lines))

    def _apply(self, eid: int, ops: list[Any], msg_ids: set[int], existing: list[dict[str, Any]]) -> tuple[int, int]:
        added = updated = 0
        by_id = {i["id"]: i for i in existing}
        pool = list(existing)
        for op in ops:
            if not isinstance(op, dict):
                continue
            src = [i for i in op.get("source_msg_ids", []) if isinstance(i, int) and i in msg_ids] \
                if isinstance(op.get("source_msg_ids"), list) else []
            kind = op.get("kind")
            short = _clip(op.get("short"), SHORT_MAX)
            text = _clip(op.get("text"), TEXT_MAX)
            stars = op.get("stars") if isinstance(op.get("stars"), int) and not isinstance(op.get("stars"), bool) else None
            if op.get("op") == "update":
                target = by_id.get(op.get("id")) if isinstance(op.get("id"), int) else None
                if target and target["status"] == "draft" and short:
                    keep_stars = stars if target["kind"] == "rate" and stars and 1 <= stars <= 5 else target["stars"]
                    if self._db.revise_impression(target["id"], short, text or target["text"], keep_stars, src):
                        updated += 1
                continue
            if op.get("op") != "add" or kind not in AI_KINDS or not short or added >= MAX_NEW:
                continue
            if kind == "word" and len(short) > WORD_MAX:
                continue
            if kind == "rate" and not (stars and 1 <= stars <= 5):
                continue
            if sum(1 for i in pool if i["status"] != "rej") >= MAX_LIVE:
                break
            if any(similar(short, i["short"]) for i in pool):
                continue
            if kind in ("one", "rate"):
                same = [i for i in pool if i["kind"] == kind and i["status"] != "rej"]
                if any(i["status"] in ACTIVE for i in same):
                    continue
                if same:
                    draft = same[0]
                    if self._db.revise_impression(draft["id"], short, text or draft["text"], stars, src):
                        updated += 1
                    continue
            imp, _ = self._db.add_impression(eid, kind, short, text, stars if kind == "rate" else None, "draft", src)
            pool.append(imp)
            added += 1
        return added, updated
