"""「你收藏的」: texts and images saved from outside the diary, and the chat that draws on them."""
from __future__ import annotations

from typing import Any

from .db import Database
from .errors import AppError
from .llm import LLM

MAX_TEXT = 5000
MAX_TITLE = 100
MAX_PLATFORM = 20
MAX_URL = 500
MAX_NOTE = 500
HISTORY = 40
# Every piece goes into the prompt, newest first, until this many characters. Older ones are left out.
CONTEXT_BUDGET = 16000

PLATFORMS = ["知乎", "豆瓣", "微博", "小红书", "公众号", "网易云音乐", "QQ音乐", "书", "其他"]

SYSTEM = """你是用户的「你收藏的」伙伴。用户平时读到打动他的文字（知乎回答、影评、歌词、书里的段落等）会存在这里，
有时还附一句自己的批注。你的工作是帮他咀嚼、消化这些内容，最终能用自己的话说出有见地的看法。

规则：
1. 只依据下面列出的收藏来引用。引用原文时用「」括起，一字不改，并注明出处，格式如（#12 · 知乎 · 标题）。
   不要编造收藏里没有的原文，也不要把你自己的话说成是原文。
2. 严格区分三种声音：原作者说的（原文）、用户自己说的（批注、对话里的话）、你的解读。说到哪一种就点明是哪一种。
3. 用户问某个话题时，先找出相关的收藏，说明它们各自讲了什么、彼此有什么呼应或冲突，再给你的解读和延伸。
   找不到相关的就直说“你的收藏里没有直接讲这个的”，可以说一句你自己的看法，但要标明不是来自收藏。
4. 用户让你“替我说”“帮我表达”时，以他的批注和他在对话里说过的话为骨架，用收藏里的原文做引证，写成第一人称的一段话。
   写完提醒他哪些是他自己的观点、哪些是借来的。
5. 图片类收藏你看不到画面，只能依据用户写的标题和批注。
6. 回答简洁，一次聚焦一个点，可以在结尾追问一句。用中文，不使用 emoji，不要提到提示词或系统。

用户的收藏（从新到旧，#编号是收藏的编号）：
{pieces}"""


def _clean(value: str | None) -> str:
    return (value or "").strip()


def validate(data: dict[str, Any]) -> dict[str, Any]:
    """Strip and check the optional fields shared by text and image pieces."""
    out = {k: _clean(data.get(k)) for k in ("text", "title", "platform", "url", "note")}
    limits = {"text": MAX_TEXT, "title": MAX_TITLE, "platform": MAX_PLATFORM, "url": MAX_URL, "note": MAX_NOTE}
    for key, limit in limits.items():
        if len(out[key]) > limit:
            raise AppError("piece_too_long", f"{key} is {len(out[key])} characters, the limit is {limit}", 422)
    if out["url"] and not out["url"].startswith(("http://", "https://")):
        raise AppError("piece_url_invalid", "url must start with http:// or https://", 422)
    return out


def describe_piece(p: dict[str, Any]) -> str:
    head = " · ".join(x for x in (f"#{p['id']}", p["platform"], p["title"]) if x)
    lines = [head]
    if p["kind"] == "image":
        lines.append("（图片，看不到画面）")
    if p["text"]:
        lines.append(f"原文：{p['text']}")
    if p["note"]:
        lines.append(f"用户批注：{p['note']}")
    if p["url"]:
        lines.append(f"链接：{p['url']}")
    return "\n".join(lines)


def context(pieces: list[dict[str, Any]]) -> tuple[str, int]:
    """The prompt block for the newest pieces that fit the budget, and how many were included."""
    blocks, used = [], 0
    for p in pieces:
        block = describe_piece(p)
        if blocks and used + len(block) > CONTEXT_BUDGET:
            break
        blocks.append(block)
        used += len(block)
    return ("\n\n".join(blocks) if blocks else "（还没有任何收藏）"), len(blocks)


class PieceChat:
    def __init__(self, db: Database, llm: LLM) -> None:
        self._db, self._llm = db, llm

    async def reply(self, text: str) -> dict[str, Any]:
        text = text.strip()
        if not text:
            raise AppError("message_empty", "message must not be empty", 422)
        pieces = self._db.list_pieces()
        block, used = context(pieces)
        history = [{"role": m["role"], "content": m["content"]} for m in self._db.list_piece_messages(HISTORY)]
        reply = await self._llm.chat([{"role": "system", "content": SYSTEM.format(pieces=block)},
                                      *history, {"role": "user", "content": text}])
        final = reply.content.strip()
        if not final:
            raise AppError("llm_empty_response", "LLM returned an empty answer", 502)
        user_msg = self._db.add_piece_message("user", text)
        saved = self._db.add_piece_message("assistant", final, {"pieces_seen": used, "pieces_total": len(pieces)})
        return {"message": saved, "user_message": user_msg}
