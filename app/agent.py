from __future__ import annotations

import json
from typing import Any

from .clips import add_text_clip
from .config import Settings
from .db import Database
from .errors import AppError, describe
from .llm import LLM
from .posters import PosterService
from .tmdb import TMDB
from .websearch import WebSearch

SYSTEM_PROMPT = """你是用户的「电影日记」伙伴，一个懂电影、会倾听的朋友，不是影评机器。

你能做的事：
1. 陪用户聊观影感悟：先读他写下的记录，再用追问的方式聊，一次只问一个问题，不要一上来输出长篇影评。
2. 解读电影：需要事实、影评、幕后信息时调用 web_search，回答里用 [标题](链接) 标注来源；搜不到就直说，不要编造。
3. 推荐电影：基于日记里的评分和感受，每部推荐都要解释「为什么适合你」。
4. 把微弱的灵感扩展成创作指引：当用户说出一句模糊的感受，给出三个不同方向（短文、摄影、短视频脚本各一个切入点），
   每个方向两三句话，让用户选一个再展开。
5. 把电影台词做成有氛围感的海报：用户给出台词时调用 make_poster。台词必须是用户给的原话，不要改写。
6. 维护「想看清单」：用户说想看某部电影、或者你推荐后用户表示想看时，用 add_to_wishlist 放进清单。
   用户说看完了，用 update_diary 把 status 改成 watched。
7. 收藏台词：用户说出一句想留下的台词时，用 save_quote 收藏，台词必须是原话。

工具使用规则：
- 用户提到某部电影但日记里没有时，先 search_movie 确认是哪一部，再用 add_to_diary 记录（评分和感受以用户说的为准，没说就留空）。
- 用户表达了对某部已记录电影的感受时，用 update_diary 写入 note（保留用户原话的味道）。
- 工具返回 error 时，把 error_code 和原因如实告诉用户，不要假装成功。
- 用中文回答，语气自然简洁，不使用 emoji。"""

TOOLS: list[dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "search_movie",
        "description": "在 TMDB 搜索电影，返回候选列表（含 tmdb_id、年份、简介）。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "片名"},
            "year": {"type": "integer", "description": "上映年份，可选"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "联网搜索影评、解读、幕后故事等。返回标题、链接和摘要。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "read_diary",
        "description": "读取用户的观影日记。不传 entry_id 返回全部记录的概要；传了返回单部电影的完整记录。"
                       "status 传 wishlist 读想看清单，默认只读已看过的。",
        "parameters": {"type": "object", "properties": {
            "entry_id": {"type": "integer"},
            "min_rating": {"type": "integer", "description": "只看评分不低于该值的记录，1-5"},
            "status": {"type": "string", "enum": ["watched", "wishlist"]}}}}},
    {"type": "function", "function": {
        "name": "add_to_diary",
        "description": "把一部电影加入日记。先用 search_movie 拿到 tmdb_id。",
        "parameters": {"type": "object", "properties": {
            "tmdb_id": {"type": "integer"},
            "rating": {"type": "integer", "description": "1-5，用户没说就不传"},
            "note": {"type": "string", "description": "用户的一句话感受"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "watched_on": {"type": "string", "description": "YYYY-MM-DD"}},
            "required": ["tmdb_id"]}}},
    {"type": "function", "function": {
        "name": "update_diary",
        "description": "更新已有日记条目的评分、感受、标签或观影日期。",
        "parameters": {"type": "object", "properties": {
            "entry_id": {"type": "integer"},
            "rating": {"type": "integer"},
            "note": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "watched_on": {"type": "string"},
            "status": {"type": "string", "enum": ["watched", "wishlist"],
                       "description": "把想看清单里的片子标记为 watched 即表示看完了"}},
            "required": ["entry_id"]}}},
    {"type": "function", "function": {
        "name": "add_to_wishlist",
        "description": "把一部还没看的电影放进想看清单。先用 search_movie 拿到 tmdb_id。",
        "parameters": {"type": "object", "properties": {
            "tmdb_id": {"type": "integer"},
            "note": {"type": "string", "description": "为什么想看，可选"}},
            "required": ["tmdb_id"]}}},
    {"type": "function", "function": {
        "name": "save_quote",
        "description": "把用户给出的一句电影台词收藏到某部电影下。台词必须是原话。",
        "parameters": {"type": "object", "properties": {
            "entry_id": {"type": "integer"},
            "text": {"type": "string"},
            "note": {"type": "string", "description": "为什么喜欢，可选"}},
            "required": ["entry_id", "text"]}}},
    {"type": "function", "function": {
        "name": "make_poster",
        "description": "把一句电影台词做成有氛围感的海报图片。",
        "parameters": {"type": "object", "properties": {
            "entry_id": {"type": "integer"},
            "quote": {"type": "string", "description": "用户给出的台词原文"}},
            "required": ["entry_id", "quote"]}}},
]


def _brief(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: entry[k] for k in ("id", "title", "year", "rating", "note", "tags", "watched_on", "status")}


def _full(entry: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "title", "original_title", "year", "director", "genres", "overview",
            "rating", "note", "tags", "watched_on", "status")
    return {k: entry[k] for k in keys}


def _check_rating(value: Any) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 5:
        raise AppError("rating_invalid", f"rating must be an integer from 1 to 5, got {value!r}", 422)
    return value


class Agent:
    def __init__(self, settings: Settings, db: Database, llm: LLM, tmdb: TMDB,
                 search: WebSearch, posters: PosterService) -> None:
        self._s, self._db, self._llm = settings, db, llm
        self._tmdb, self._search, self._posters = tmdb, search, posters

    # ---- tools -------------------------------------------------------
    async def _run_tool(self, name: str, args: dict[str, Any], turn: dict[str, Any]) -> Any:
        db = self._db
        if name == "search_movie":
            return {"results": await self._tmdb.search(args["query"], args.get("year"))}
        if name == "web_search":
            results = await self._search.search(args["query"])
            turn["sources"].extend({"title": r["title"], "url": r["url"]} for r in results)
            return {"results": results}
        if name == "read_diary":
            if args.get("entry_id") is not None:
                entry = db.get_entry(args["entry_id"])
                if entry is None:
                    raise AppError("entry_not_found", f"No diary entry with id {args['entry_id']}", 404)
                return _full(entry)
            status = args.get("status", "watched")
            if status not in ("watched", "wishlist"):
                raise AppError("status_invalid", f"status must be watched or wishlist, got {status!r}", 422)
            return {"entries": [_brief(e) for e in db.list_entries(100, args.get("min_rating"), status)]}
        if name == "add_to_diary":
            detail = await self._tmdb.details(args["tmdb_id"])
            dup = db.find_duplicate(detail["title"], detail["year"], detail["tmdb_id"])
            if dup:
                hint = ("it is on the wishlist; use update_diary with status=watched" if dup["status"] == "wishlist"
                        else "use update_diary instead")
                raise AppError("entry_already_exists",
                               f"《{dup['title']}》 is already saved as entry {dup['id']}; {hint}", 409)
            data = {**detail, "rating": _check_rating(args.get("rating")),
                    "note": args.get("note", ""), "tags": args.get("tags", []),
                    "watched_on": args.get("watched_on", "")}
            entry = db.create_entry(data)
            turn["changed"] = True
            return _full(entry)
        if name == "add_to_wishlist":
            detail = await self._tmdb.details(args["tmdb_id"])
            dup = db.find_duplicate(detail["title"], detail["year"], detail["tmdb_id"])
            if dup:
                raise AppError("entry_already_exists",
                               f"《{dup['title']}》 is already saved as entry {dup['id']} "
                               f"(status {dup['status']})", 409)
            entry = db.create_entry({**detail, "note": args.get("note", ""), "status": "wishlist"})
            turn["changed"] = True
            return _full(entry)
        if name == "save_quote":
            if db.get_entry(args["entry_id"]) is None:
                raise AppError("entry_not_found", f"No diary entry with id {args['entry_id']}", 404)
            clip = add_text_clip(db, args["entry_id"], "quote", args["text"], args.get("note", ""))
            turn["changed"] = True
            return {"clip_id": clip["id"], "text": clip["text"]}
        if name == "update_diary":
            patch = {k: args[k] for k in ("rating", "note", "tags", "watched_on", "status") if k in args}
            if "status" in patch and patch["status"] not in ("watched", "wishlist"):
                raise AppError("status_invalid", f"status must be watched or wishlist, got {patch['status']!r}", 422)
            if "rating" in patch:
                patch["rating"] = _check_rating(patch["rating"])
            entry = db.update_entry(args["entry_id"], patch)
            if entry is None:
                raise AppError("entry_not_found", f"No diary entry with id {args['entry_id']}", 404)
            turn["changed"] = True
            return _full(entry)
        if name == "make_poster":
            entry = db.get_entry(args["entry_id"])
            if entry is None:
                raise AppError("entry_not_found", f"No diary entry with id {args['entry_id']}", 404)
            poster = await self._posters.create(entry, args["quote"])
            turn["posters"].append(poster)
            return {"poster_id": poster["id"], "mood": poster["mood"],
                    "used_gradient_fallback": bool(poster["fallback_code"]),
                    "fallback_error_code": poster["fallback_code"],
                    "fallback_error": poster["fallback_message"]}
        raise AppError("tool_unknown", f"Unknown tool {name!r}", 400)

    async def _dispatch(self, name: str, raw_args: str, turn: dict[str, Any]) -> str:
        try:
            args = json.loads(raw_args or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except ValueError as exc:
            result: Any = {"error_code": "tool_arguments_invalid",
                           "error": f"Arguments for {name} are not valid JSON. {describe(exc)}"}
        else:
            try:
                result = await self._run_tool(name, args, turn)
            except AppError as exc:
                result = {"error_code": exc.code, "error": exc.message}
            except KeyError as exc:
                result = {"error_code": "tool_arguments_invalid",
                          "error": f"{name} is missing required argument {describe(exc)}"}
        turn["tools"].append({"name": name, "ok": "error_code" not in result})
        return json.dumps(result, ensure_ascii=False)

    # ---- conversation ------------------------------------------------
    def _context(self, entry_id: int | None) -> str:
        if entry_id is None:
            n = len(self._db.list_entries(1000))
            return f"当前是全局对话，日记里共有 {n} 部电影。需要了解偏好时用 read_diary。"
        entry = self._db.get_entry(entry_id)
        if entry is None:
            raise AppError("entry_not_found", f"No diary entry with id {entry_id}", 404)
        return ("当前对话围绕这一部电影展开，它的日记记录如下：\n"
                + json.dumps(_full(entry), ensure_ascii=False))

    async def reply(self, entry_id: int | None, text: str) -> dict[str, Any]:
        text = text.strip()
        if not text:
            raise AppError("message_empty", "message must not be empty", 422)
        system = f"{SYSTEM_PROMPT}\n\n{self._context(entry_id)}"
        history = [{"role": m["role"], "content": m["content"]}
                   for m in self._db.list_messages(entry_id, 40)]
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}, *history,
                                          {"role": "user", "content": text}]
        turn: dict[str, Any] = {"sources": [], "posters": [], "tools": [], "changed": False}

        final = ""
        for _ in range(self._s.max_agent_steps):
            reply = await self._llm.chat(messages, TOOLS)
            if not reply.tool_calls:
                final = reply.content.strip()
                break
            messages.append({"role": "assistant", "content": reply.content or None, "tool_calls": [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                for c in reply.tool_calls]})
            for call in reply.tool_calls:
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": await self._dispatch(call.name, call.arguments, turn)})
        else:
            raise AppError("agent_step_limit",
                           f"The agent used all {self._s.max_agent_steps} tool-call rounds without a final "
                           "answer. Ask again with a narrower request.", 502)
        if not final:
            raise AppError("llm_empty_response", "LLM returned an empty final answer", 502)

        seen: set[str] = set()
        sources = [s for s in turn["sources"] if not (s["url"] in seen or seen.add(s["url"]))]
        meta = {"sources": sources, "poster_ids": [p["id"] for p in turn["posters"]],
                "tools": turn["tools"]}
        self._db.add_message(entry_id, "user", text)
        saved = self._db.add_message(entry_id, "assistant", final, meta)
        return {"message": {**saved, "posters": turn["posters"]}, "diary_changed": turn["changed"]}
