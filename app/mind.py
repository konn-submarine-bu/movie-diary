"""The 「你收藏的」 agent's own mind: views, tastes and open questions formed only from what the user saved.

The mind is rebuilt incrementally. Each refresh shows the model its current mind plus the pieces that were
added, changed or removed since the last refresh, and the model returns the whole new mind. Every view must
name the pieces that formed it; views whose sources are all gone are dropped. The user's notes are shown as
the user's reactions: the mind may agree or disagree with them, but a note alone cannot be a source.
"""
from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from .db import Database
from .errors import AppError
from .llm import LLM
from .llmjson import extract_json

MAX_VIEWS = 12
MAX_TASTES = 8
MAX_DOUBTS = 6
MAX_LEN = 160
BATCH = 40           # pieces digested per model call; larger backlogs take several calls
PIECE_CHARS = 1200   # each piece is cut to this length when digesting

DIGEST = """你是一个由别人上传的文字塑造出来的思考者。你没有别的经历，你读过的东西就是这些收藏，你的看法完全从它们长出来。
用户是另一个人，他把读到的东西交给你；他附的批注是他自己的反应，不是你的。你可以同意、可以不同意，但不能把他的批注当成你看法的来源。

下面是你现在的想法，以及这次新增、修改或删除的收藏。读完后，返回你更新后的全部想法。

要求：
- views：你相信的东西，每条一句话，用第一人称，要有立场，不要写成对收藏的摘要。最多 {max_views} 条。
  每条必须在 sources 里列出形成它的收藏编号（至少一个）。新收藏可以加强、修正或推翻旧看法；被删掉的收藏不再算来源。
  如果你不同意用户在某条批注里的说法，可以在 disagrees_with_note 里写那条收藏的编号。
- tastes：你偏爱什么样的文字、观点或表达，最多 {max_tastes} 条，同样要有 sources。
- doubts：你还拿不准、或者收藏之间互相矛盾的地方，最多 {max_doubts} 条，sources 列出相关收藏。
- 收藏太少时，宁可少写，不要编。每条不超过 60 字。
- 用中文，不使用 emoji。

只返回一个 JSON 对象：
{{"views": [{{"text": "...", "sources": [1, 2], "disagrees_with_note": []}}],
  "tastes": [{{"text": "...", "sources": [3]}}],
  "doubts": [{{"text": "...", "sources": [1, 4]}}]}}"""


def fingerprint(p: dict[str, Any]) -> str:
    raw = "\x1f".join(p[k] for k in ("text", "caption", "title", "platform", "note"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _piece_for_digest(p: dict[str, Any]) -> str:
    head = " · ".join(x for x in (f"#{p['id']}", p["platform"], p["title"]) if x)
    body = p["text"] or ""
    lines = [head]
    if p["kind"] == "image":
        lines.append(f"（图片）画面：{p['caption'] or '未知'}")
    if body:
        lines.append(f"原文：{body[:PIECE_CHARS]}{'…' if len(body) > PIECE_CHARS else ''}")
    if p["note"]:
        lines.append(f"用户的批注（他的反应，不是你的）：{p['note']}")
    return "\n".join(lines)


def _items(raw: Any, limit: int, valid: set[int], with_note: bool = False) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str) or not item["text"].strip():
            continue
        sources = [i for i in dict.fromkeys(item.get("sources") or []) if isinstance(i, int) and i in valid]
        if not sources:
            continue
        entry: dict[str, Any] = {"text": item["text"].strip()[:MAX_LEN], "sources": sources}
        if with_note:
            entry["disagrees_with_note"] = [i for i in dict.fromkeys(item.get("disagrees_with_note") or [])
                                            if isinstance(i, int) and i in valid]
        out.append(entry)
        if len(out) >= limit:
            break
    return out


def clean_mind(data: dict[str, Any], valid: set[int]) -> dict[str, Any]:
    return {"views": _items(data.get("views"), MAX_VIEWS, valid, with_note=True),
            "tastes": _items(data.get("tastes"), MAX_TASTES, valid),
            "doubts": _items(data.get("doubts"), MAX_DOUBTS, valid)}


def prune(mind: dict[str, Any], valid: set[int]) -> dict[str, Any]:
    """Drop deleted pieces from every source list, and items left with no source."""
    return clean_mind(mind, valid)


def render(mind: dict[str, Any]) -> str:
    """The mind as plain text for prompts."""
    def block(title: str, items: list[dict[str, Any]]) -> str:
        if not items:
            return f"{title}：（还没有）"
        lines = []
        for it in items:
            line = f"- {it['text']}（来自 {'、'.join(f'#{i}' for i in it['sources'])}）"
            if it.get("disagrees_with_note"):
                line += f"；不同意用户在 {'、'.join(f'#{i}' for i in it['disagrees_with_note'])} 的批注"
            lines.append(line)
        return f"{title}：\n" + "\n".join(lines)
    return "\n".join([block("我相信的", mind["views"]), block("我偏爱的", mind["tastes"]),
                      block("我拿不准的", mind["doubts"])])


class Mind:
    def __init__(self, db: Database, llm: LLM) -> None:
        self._db, self._llm = db, llm
        self._lock = asyncio.Lock()

    def state(self) -> dict[str, Any]:
        """The current mind plus how many pieces are waiting to be digested."""
        saved = self._db.get_mind()
        pieces = self._db.list_pieces()
        seen = saved["seen"]
        valid = {p["id"] for p in pieces}
        pending = sum(1 for p in pieces if seen.get(str(p["id"])) != fingerprint(p))
        removed = sum(1 for k in seen if int(k) not in valid)
        return {"mind": prune(saved["mind"], valid), "updated_at": saved["updated_at"],
                "pending": pending + removed, "total": len(pieces), "error": saved["error"],
                "busy": self._lock.locked()}

    async def refresh(self) -> dict[str, Any]:
        """Digest everything that changed since the last refresh. Concurrent callers wait for one run."""
        async with self._lock:
            while True:
                saved = self._db.get_mind()
                pieces = self._db.list_pieces()
                seen: dict[str, str] = saved["seen"]
                valid = {p["id"] for p in pieces}
                changed = [p for p in reversed(pieces) if seen.get(str(p["id"])) != fingerprint(p)]
                removed = [int(k) for k in seen if int(k) not in valid]
                mind = prune(saved["mind"], valid)
                if not changed and not removed:
                    break
                if not changed:  # only deletions: prune, no model call needed
                    self._db.put_mind(mind, {k: v for k, v in seen.items() if int(k) in valid}, "")
                    continue
                batch = changed[:BATCH]
                try:
                    mind = await self._digest(mind, batch, removed, valid)
                except AppError as exc:
                    self._db.put_mind(mind, {k: v for k, v in seen.items() if int(k) in valid},
                                      f"{exc.code}: {exc.message}"[:500])
                    raise
                seen = {k: v for k, v in seen.items() if int(k) in valid}
                seen.update({str(p["id"]): fingerprint(p) for p in batch})
                self._db.put_mind(mind, seen, "")
        return self.state()

    async def _digest(self, mind: dict[str, Any], batch: list[dict[str, Any]], removed: list[int],
                      valid: set[int]) -> dict[str, Any]:
        parts = ["你现在的想法：", render(mind), "", f"这次读到的收藏（{len(batch)} 条，新增或修改过）："]
        parts += [_piece_for_digest(p) + "\n" for p in batch]
        if removed:
            parts.append("这些收藏被删掉了，不再算来源：" + "、".join(f"#{i}" for i in removed))
        system = DIGEST.format(max_views=MAX_VIEWS, max_tastes=MAX_TASTES, max_doubts=MAX_DOUBTS)
        reply = await self._llm.chat([{"role": "system", "content": system},
                                      {"role": "user", "content": "\n".join(parts)}])
        return clean_mind(extract_json(reply.content, "mind_update_failed"), valid)
