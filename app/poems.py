from __future__ import annotations

import re
from typing import Any

from .db import Database
from .errors import AppError
from .llm import LLM

_CN: list[dict[str, str]] = [
    {"key": "xu_zhimo", "name": "徐志摩", "tagline": "新月派 · 轻盈浪漫",
     "notes": "语言流动轻盈，比喻华丽饱满，爱用云、风、水、波光、夜色这类意象；有明显的音乐性，句式长短错落，情感热烈，收尾带一点怅惘。"},
    {"key": "dai_wangshu", "name": "戴望舒", "tagline": "象征派 · 雨巷与忧郁",
     "notes": "意象朦胧幽微，常用雨、丁香、黄昏、远方、寂寞的行人；语气低回哀婉，节奏舒缓，音韵有回环，情绪含蓄，不把话说破。"},
    {"key": "lin_huiyin", "name": "林徽因", "tagline": "清丽细腻 · 时间与心事",
     "notes": "语言清丽细腻，善用自然小景（光影、花、燕子、季节）写时间和心事；句子柔和克制，思考藏在轻轻的比喻里。"},
    {"key": "mu_dan", "name": "穆旦", "tagline": "现代主义 · 智性与痛感",
     "notes": "语言硬朗，带思辨色彩，善写矛盾与悖论；句式绵密有力度，抽象概念与具体意象交织，情感深沉克制。"},
    {"key": "ai_qing", "name": "艾青", "tagline": "土地与光明",
     "notes": "语言质朴有力，意象来自土地、河流、太阳、风雪、黎明；带着沉郁而深沉的爱，节奏自由舒展，常用排比和反复推进情感。"},
    {"key": "bei_dao", "name": "北岛", "tagline": "朦胧诗 · 冷峻锋利",
     "notes": "语言冷峻凝练，意象跳跃而锋利，多用短句、断句和警句式的收束，口吻带怀疑与追问；不泛滥抒情，靠对比和反讽制造力量。"},
    {"key": "shu_ting", "name": "舒婷", "tagline": "朦胧诗 · 温柔而坚定",
     "notes": "语气温柔而坚定，以第一人称倾诉；爱用海、船、岸、树这类意象，情感真挚细腻，深情中有独立的自觉。"},
    {"key": "gu_cheng", "name": "顾城", "tagline": "童话般的纯净与冷",
     "notes": "句子极短，口吻天真近乎孩子；意象干净（眼睛、云、草、墙、颜色），纯净之下藏着冷和孤独，常有出人意料的小比喻。"},
    {"key": "hai_zi", "name": "海子", "tagline": "麦地与远方",
     "notes": "语言质朴炽热，意象宏大又日常（麦地、太阳、村庄、河流、远方、粮食）；常用呼告与祝福的口吻，节奏简单绵长，有孤独的纯粹感。"},
    {"key": "yu_guangzhong", "name": "余光中", "tagline": "乡愁与节奏",
     "notes": "现代口语与古典韵味并用，节奏感强，句式匀称；喜欢用具体的小物件（船票、邮票、月光）承载大的时空感，抒情里带深沉的乡愁。"},
]
_FOREIGN: list[dict[str, str]] = [
    {"key": "tagore", "name": "泰戈尔", "tagline": "印度 · 飞鸟与星辰",
     "notes": "语气平静温柔，近乎祈祷与低语；意象来自河流、飞鸟、星辰、黄昏、孩子和神性的自然；句子简洁澄澈，常用第二人称向“你”倾诉，哲思藏在朴素的比喻里。"},
    {"key": "dickinson", "name": "狄金森", "tagline": "美国 · 短句与破折号",
     "notes": "句子极短，用破折号制造停顿和跳跃；把死亡、永恒、内心当作日常小事来谈，比喻出人意料而精确；语气克制、偏执、带一点冷幽默，常在末行突然转向。"},
    {"key": "rilke", "name": "里尔克", "tagline": "德语 · 沉思与物的内在",
     "notes": "沉静的沉思口吻，凝视一件具体的事物（豹、天使、钟、秋天的叶子）并写出它的内在；句子庄重绵长，孤独、时间与存在感贯穿其间，不急于下结论。"},
    {"key": "neruda", "name": "聂鲁达", "tagline": "智利 · 炽热的身体与大地",
     "notes": "感官饱满，意象繁盛而肉感（海、盐、果实、雨、石头、身体）；比喻奔放连绵，句式铺陈，情感热烈直接，爱与土地、时间缠绕在一起。"},
    {"key": "baudelaire", "name": "波德莱尔", "tagline": "法国 · 都市的美与恶",
     "notes": "在城市、颓败与忧郁中发现美；意象华丽而阴郁（黄昏、烟雾、腐花、陌生人、镜子），感官相互呼应，语气优雅而带反讽，抒情里有冷静的自我审视。"},
    {"key": "whitman", "name": "惠特曼", "tagline": "美国 · 自由体与宽阔的我",
     "notes": "自由长句，节奏像呼吸与行走；大量排比与罗列，把人群、劳动、身体、土地一并纳入；口吻开阔、坦率、充满生命力，常以“我”拥抱一切。"},
    {"key": "yeats", "name": "叶芝", "tagline": "爱尔兰 · 暮光与神话",
     "notes": "带神话与凯尔特式的暮光色彩；意象如天鹅、星光、苍白的水、旧日的爱人；节奏庄严、有咏叹感，情感深沉，把个人遗憾写成一件古老而庄重的事。"},
    {"key": "pushkin", "name": "普希金", "tagline": "俄国 · 明净与真挚",
     "notes": "语言明净流畅，真挚而节制；爱写爱情、友谊、自由与流放中的一点光亮；节奏均匀，语气亲切坦诚，悲伤里留着明朗。"},
    {"key": "borges", "name": "博尔赫斯", "tagline": "阿根廷 · 迷宫、镜与时间",
     "notes": "冷静博学的口吻，把镜子、迷宫、沙漏、图书馆、老虎当作思辨的素材；用悖论和循环谈时间与命运，语言克制精确，情感藏在智性的缝隙里。"},
    {"key": "szymborska", "name": "辛波斯卡", "tagline": "波兰 · 平静的诘问",
     "notes": "用日常的平静口吻提出深刻的问题；善用反问、列举与反讽，从微小事物（一粒沙、一个洋葱、一个陌生人）看大的命运；幽默而温柔，结尾常轻轻一击。"},
    {"key": "tsvetaeva", "name": "茨维塔耶娃", "tagline": "俄国 · 迸发与断裂",
     "notes": "语调激烈、急促，句子被破折号和断句切开；情感极端而坦荡，爱与孤独都写到极限；节奏有喘息感，常用呼告和短促的重复。"},
    {"key": "tanikawa", "name": "谷川俊太郎", "tagline": "日本 · 平易的宇宙感",
     "notes": "用最平易的口语写大的主题；句子短、干净、留白多，常从日常小事（水、天空、一张桌子）忽然拉到宇宙尺度；语气像孩子的好奇，温和而不煽情。"},
]

POETS: list[dict[str, str]] = [{**p, "group": "cn"} for p in _CN] + [{**p, "group": "foreign"} for p in _FOREIGN]
_BY_KEY = {p["key"]: p for p in POETS}

TRANSLATED = "这位诗人不是中文诗人：请模仿他的作品在中文译本里呈现出来的质感，不要刻意模仿原语言的韵律、格律或词汇。\n"

SYSTEM = """你是熟悉中外现代诗歌的写作者。用户会给出一部电影和他自己的观影记录，请用指定诗人的文风，为这部电影原创一首现代诗。

文风：{name}（{tagline}）
{notes}
{extra}
要求：
- 一律用现代汉语写作。只借鉴文风，也就是语言质地、意象取向、节奏和句式。不要照抄、改写或引用该诗人任何已有作品的句子，也不要在诗里写出诗人的名字。
- 以用户的感受和电影里具体的画面、人物、情节为材料，不要空泛抒情。用户写下的感受是最重要的线索。
- 6 到 12 行，每行不超过 20 个字，可以分节。
- 输出格式：第一行是诗题，空一行，然后是正文。除此之外不要任何解释、前后缀或标记。"""


def parse_poem(raw: str) -> tuple[str, str]:
    """Split an LLM reply into (title, body). The title is empty when the reply has no separate title line."""
    text = re.sub(r"^```\w*\n?|\n?```$", "", raw.strip()).strip()
    parts = re.split(r"\n\s*\n", text, maxsplit=1)
    if len(parts) == 2 and "\n" not in parts[0].strip():
        title, body = parts[0].strip(), parts[1].strip()
    else:
        title, body = "", text
    return title.strip("《》「」#* 　"), body


def style_of(key: str) -> dict[str, str] | None:
    return _BY_KEY.get(key)


class PoemService:
    def __init__(self, llm: LLM, db: Database) -> None:
        self._llm = llm
        self._db = db

    def _material(self, e: dict[str, Any]) -> str:
        lines = [f"电影：《{e['title']}》" + (f"（{e['year']}）" if e.get("year") else "")]
        if e.get("director"):
            lines.append(f"导演：{e['director']}")
        if e.get("genres"):
            lines.append("类型：" + "、".join(e["genres"]))
        if e.get("overview"):
            lines.append(f"简介：{e['overview'][:300]}")
        if e.get("rating"):
            lines.append(f"我的评分：{e['rating']} 星（满分 5 星）")
        if e.get("note"):
            lines.append(f"我的感受：{e['note']}")
        if e.get("tags"):
            lines.append("标签：" + "、".join(e["tags"]))
        quotes = [c["text"] for c in self._db.list_clips(e["id"], "quote")][:3]
        if quotes:
            lines.append("我收藏的台词：" + " / ".join(quotes))
        lines.append("请写这首诗。")
        return "\n".join(lines)

    async def write(self, entry: dict[str, Any], style_key: str) -> dict[str, Any]:
        poet = _BY_KEY.get(style_key)
        if poet is None:
            raise AppError("poem_style_unknown",
                           f"Unknown poem style {style_key!r}. Valid styles: {', '.join(_BY_KEY)}", 422)
        reply = await self._llm.chat([
            {"role": "system", "content": SYSTEM.format(
                name=poet["name"], tagline=poet["tagline"], notes=poet["notes"],
                extra=TRANSLATED if poet["group"] == "foreign" else "")},
            {"role": "user", "content": self._material(entry)},
        ])
        title, body = parse_poem(reply.content)
        if not body:
            raise AppError("poem_empty", "LLM returned no poem text", 502)
        return {"title": title, "body": body,
                "style": {k: poet[k] for k in ("key", "name", "tagline", "group")}}
