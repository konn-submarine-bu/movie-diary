from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .db import Database
from .errors import AppError
from .llm import LLM
from .llmjson import extract_json
from .tmdb import TMDB
from .websearch import WebSearch

FILM_TTL = timedelta(days=30)
HINT_CODE = "compose_hints_invalid"

HINT_PROMPT = """用户在写看完一部电影后的感受，写到一半，需要你帮他把话说得更贴切。用户可能不太会形容感受。

只依据他已经写下的内容，不要替他编造他没有表达的立场。输出一个 JSON 对象，不要其他文字：
{"continuations":[
 {"label":"续写","text":"紧接着他已写内容的后半句，自然衔接，30字内"},
 {"label":"换个角度","text":"同一件事换一种说法或另一个角度，30字内"},
 {"label":"更抒情","text":"更有文学感的表达，30字内"}],
 "words":["他可能想说却说不出的词，2到4个字，5到6个","..."]}
text 只写需要追加在原文后面的部分，不要重复他已写的内容。"""


class FilmPage:
    def __init__(self, db: Database, tmdb: TMDB, search: WebSearch, llm: LLM) -> None:
        self._db, self._tmdb, self._search, self._llm = db, tmdb, search, llm

    async def info(self, entry: dict[str, Any]) -> dict[str, Any]:
        tid = entry.get("tmdb_id")
        if not tid:
            raise AppError("film_info_unavailable", "this entry has no TMDB id, so there is nothing more to show", 404)
        cached = self._db.get_film_info(tid)
        if cached:
            fetched = datetime.fromisoformat(cached[1])
            if datetime.now(timezone.utc) - fetched < FILM_TTL:
                return cached[0]
        data = await self._tmdb.film_info(tid)
        self._db.put_film_info(tid, data)
        return data

    async def reviews(self, entry: dict[str, Any]) -> dict[str, Any]:
        year = f" {entry['year']}" if entry["year"] else ""
        results = await self._search.search(f"{entry['title']}{year} 电影 影评 解读", 6)
        reviews = [{"title": r["title"], "url": r["url"], "snippet": r["snippet"]}
                   for r in results if r.get("url", "").startswith(("http://", "https://"))]
        return {"summary": "", "reviews": reviews}

    async def hints(self, entry: dict[str, Any], draft: str) -> dict[str, Any]:
        user = (f"电影：《{entry['title']}》({entry['year'] or ''})\n"
                f"用户已写下：{draft}")
        reply = await self._llm.chat([{"role": "system", "content": HINT_PROMPT}, {"role": "user", "content": user}])
        data = extract_json(reply.content, HINT_CODE)
        conts = []
        for c in data.get("continuations", []) if isinstance(data.get("continuations"), list) else []:
            if isinstance(c, dict) and isinstance(c.get("text"), str) and c["text"].strip():
                text = c["text"].strip()
                if text.startswith(draft.strip()) and len(text) > len(draft.strip()):
                    text = text[len(draft.strip()):]
                conts.append({"text": text[:80], "label": str(c.get("label") or "续写")[:8]})
        words = [w.strip() for w in data.get("words", []) if isinstance(w, str) and 0 < len(w.strip()) <= 8] \
            if isinstance(data.get("words"), list) else []
        return {"continuations": conts[:3], "words": words[:8]}
