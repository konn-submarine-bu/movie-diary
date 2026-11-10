from __future__ import annotations

import base64
import io
import json
import re
import secrets
from pathlib import Path
from typing import Any, Protocol

import httpx
from PIL import Image, ImageDraw, ImageFont

from .config import Settings
from .db import Database
from .errors import AppError, describe
from .llm import LLM

W, H = 1024, 1536
HEX = re.compile(r"#[0-9a-fA-F]{6}")
NO_LINE_START = set("，。！？、；：”’）》」』…,.!?;:)")
DEFAULT_PALETTE = ["#14182f", "#5a4a78"]
FONT_CANDIDATES = [
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "C:/Windows/Fonts/msyh.ttc",
]

BRIEF_SYSTEM = """你是电影海报的美术指导。给定一部电影、其中的一句台词以及观众自己的感受，
提炼这句台词的意境，并写出一段用于文生图模型的画面描述。

只输出一个 JSON 对象，不要任何其他文字：
{"mood": "用 10-20 字概括意境", "palette": ["#rrggbb", "#rrggbb"], "image_prompt": "..."}

image_prompt 要求：
- 描述画面：主体、场景、光线、色调、构图、镜头感，电影剧照质感，竖版 2:3 构图。
- 用意象和氛围表达台词，不要复刻具体剧照，不要出现真实演员的面孔或可辨认的人物肖像。
- 画面里不要出现任何文字、字母、字幕、水印或标志（文字由程序另外排版）。
- 画面下方三分之一保持相对暗和简洁，留给文字排版。
palette 是画面主色调的两个十六进制颜色，从暗到亮。"""


class ImageGenerator(Protocol):
    async def generate(self, prompt: str) -> bytes: ...


class OpenAICompatImages:
    """POSTs to {IMAGE_BASE_URL}/images/generations and accepts b64_json or url results."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings

    async def generate(self, prompt: str) -> bytes:
        if not self._s.image_configured:
            raise AppError("image_not_configured",
                           "IMAGE_BASE_URL, IMAGE_API_KEY and IMAGE_MODEL must be set in .env", 503)
        url = f"{self._s.image_base_url}/images/generations"
        body = {"model": self._s.image_model, "prompt": prompt, "size": self._s.image_size, "n": 1}
        headers = {"Authorization": f"Bearer {self._s.image_api_key}"}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self._s.image_timeout, connect=20)) as client:
                resp = await client.post(url, json=body, headers=headers)
                if resp.status_code >= 400:
                    raise AppError("image_request_failed",
                                   f"Image API returned HTTP {resp.status_code}: {resp.text[:300]}", 502)
                try:
                    item = resp.json()["data"][0]
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    raise AppError("image_response_invalid",
                                   f"Image API response has no data[0]. {describe(exc)}", 502) from exc
                if item.get("b64_json"):
                    try:
                        return base64.b64decode(item["b64_json"])
                    except ValueError as exc:
                        raise AppError("image_decode_failed",
                                       f"b64_json is not valid base64. {describe(exc)}", 502) from exc
                if item.get("url"):
                    dl = await client.get(item["url"])
                    if dl.status_code >= 400:
                        raise AppError("image_download_failed",
                                       f"Downloading the generated image returned HTTP {dl.status_code}", 502)
                    return dl.content
                raise AppError("image_response_invalid",
                               "Image API data[0] has neither b64_json nor url", 502)
        except httpx.TimeoutException as exc:
            raise AppError("image_timeout", f"Image API did not answer in time. {describe(exc)}", 504) from exc
        except httpx.HTTPError as exc:
            raise AppError("image_unreachable", f"Cannot reach the image API. {describe(exc)}", 502) from exc


def parse_brief(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise AppError("poster_brief_invalid", f"LLM did not return a JSON object: {text[:200]!r}", 502)
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise AppError("poster_brief_invalid", f"LLM returned malformed JSON. {describe(exc)}", 502) from exc
    prompt = str(data.get("image_prompt", "")).strip()
    if not prompt:
        raise AppError("poster_brief_invalid", "LLM JSON has an empty image_prompt", 502)
    palette = [c for c in data.get("palette", []) if isinstance(c, str) and HEX.fullmatch(c)]
    return {"mood": str(data.get("mood", "")).strip(), "palette": palette[:2] or DEFAULT_PALETTE,
            "image_prompt": prompt}


def find_font(configured: str) -> str:
    for path in ([configured] if configured else []) + FONT_CANDIDATES:
        if path and Path(path).is_file():
            return path
    raise AppError("poster_font_missing",
                   "No CJK font found. Set POSTER_FONT in .env to a .ttf/.ttc file that has Chinese glyphs", 500)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for tok in re.findall(r"[A-Za-z0-9'’]+|\s|.", para):
            if cur and draw.textlength(cur + tok, font=font) > max_w and tok not in NO_LINE_START:
                lines.append(cur.rstrip())
                cur = tok.lstrip()
            else:
                cur += tok
        lines.append(cur.rstrip())
    return lines


def gradient_background(palette: list[str]) -> Image.Image:
    top, bottom = (Image.new("RGB", (1, 1), c).getpixel((0, 0)) for c in palette)
    mask = Image.linear_gradient("L").resize((W, H))
    return Image.composite(Image.new("RGB", (W, H), bottom), Image.new("RGB", (W, H), top), mask)


def cover_crop(img: Image.Image) -> Image.Image:
    img = img.convert("RGB")
    scale = max(W / img.width, H / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    left, top = (img.width - W) // 2, (img.height - H) // 2
    return img.crop((left, top, left + W, top + H))


def compose(background: Image.Image, quote: str, caption: str, font_path: str) -> Image.Image:
    base = background.convert("RGBA")
    shade = Image.linear_gradient("L").resize((W, int(H * 0.6)))
    shade = shade.point(lambda v: int(v * 0.82))
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    layer.paste(Image.new("RGBA", shade.size, (0, 0, 0, 255)), (0, H - shade.height), shade)
    base = Image.alpha_composite(base, layer)

    draw = ImageDraw.Draw(base)
    margin, max_w, max_h = 96, W - 192, 520
    for size in (64, 56, 48, 42, 36):
        font = ImageFont.truetype(font_path, size)
        lines = _wrap(draw, quote, font, max_w)
        line_h = int(size * 1.6)
        if len(lines) * line_h <= max_h:
            break
    small = ImageFont.truetype(font_path, 28)
    y = H - 150 - len(lines) * line_h
    for line in lines:
        draw.text((margin + 2, y + 2), line, font=font, fill=(0, 0, 0, 160))
        draw.text((margin, y), line, font=font, fill=(255, 255, 255, 245))
        y += line_h
    draw.line((margin, H - 120, margin + 72, H - 120), fill=(255, 255, 255, 180), width=2)
    draw.text((margin, H - 100), caption, font=small, fill=(255, 255, 255, 200))
    return base.convert("RGB")


class PosterService:
    def __init__(self, settings: Settings, db: Database, llm: LLM, images: ImageGenerator) -> None:
        self._s, self._db, self._llm, self._images = settings, db, llm, images

    async def _brief(self, entry: dict[str, Any], quote: str) -> dict[str, Any]:
        lines = [f"电影：《{entry['title']}》" + (f"（{entry['year']}）" if entry["year"] else ""),
                 f"台词：{quote}"]
        if entry["director"]:
            lines.append(f"导演：{entry['director']}")
        if entry["overview"]:
            lines.append(f"剧情简介：{entry['overview'][:300]}")
        if entry["note"]:
            lines.append(f"观众的感受：{entry['note']}")
        reply = await self._llm.chat([{"role": "system", "content": BRIEF_SYSTEM},
                                      {"role": "user", "content": "\n".join(lines)}])
        return parse_brief(reply.content)

    async def create(self, entry: dict[str, Any], quote: str) -> dict[str, Any]:
        quote = quote.strip()
        if not quote:
            raise AppError("poster_quote_empty", "quote must not be empty", 422)
        if len(quote) > 200:
            raise AppError("poster_quote_too_long", "quote must be at most 200 characters", 422)
        font_path = find_font(self._s.poster_font)
        brief = await self._brief(entry, quote)

        fallback_code = fallback_message = ""
        try:
            raw = await self._images.generate(brief["image_prompt"])
            try:
                background = cover_crop(Image.open(io.BytesIO(raw)))
            except OSError as exc:
                raise AppError("image_decode_failed",
                               f"Generated bytes are not a readable image. {describe(exc)}", 502) from exc
        except AppError as exc:
            fallback_code, fallback_message = exc.code, exc.message
            background = gradient_background(brief["palette"])

        caption = f"《{entry['title']}》" + (f" · {entry['year']}" if entry["year"] else "")
        poster = compose(background, quote, caption, font_path)

        self._s.posters_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{entry['id']}-{secrets.token_hex(6)}.png"
        poster.save(self._s.posters_dir / filename, "PNG")
        return self._db.add_poster(entry["id"], quote, brief["mood"], brief["image_prompt"], filename,
                                   fallback_code, fallback_message)
