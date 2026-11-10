from __future__ import annotations

import asyncio
import io
import secrets
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageFont

from .config import Settings
from .db import Database
from .errors import AppError, describe
from .posters import H, W, _wrap, find_font
from .tmdb import TMDB

MAX_QUOTES = 4
MAX_QUOTE_LEN = 120
MIN_STILLS = 1


def layout(n: int) -> list[tuple[int, int, int, int]]:
    """Cells (left, top, right, bottom) in the upper part of the canvas; text takes the bottom ~38%."""
    area_h = int(H * 0.62)
    gap = 6
    if n <= 1:
        return [(0, 0, W, area_h)]
    if n == 2:
        h = (area_h - gap) // 2
        return [(0, 0, W, h), (0, h + gap, W, area_h)]
    if n == 3:
        top = int(area_h * 0.58)
        half = (W - gap) // 2
        return [(0, 0, W, top), (0, top + gap, half, area_h), (half + gap, top + gap, W, area_h)]
    half, h = (W - gap) // 2, (area_h - gap) // 2
    return [(0, 0, half, h), (half + gap, 0, W, h), (0, h + gap, half, area_h), (half + gap, h + gap, W, area_h)]


def fill_cell(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    img = img.convert("RGB")
    scale = max(size[0] / img.width, size[1] / img.height)
    img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
    left, top = (img.width - size[0]) // 2, (img.height - size[1]) // 2
    return img.crop((left, top, left + size[0], top + size[1]))


def compose_collage(stills: list[Image.Image], quotes: list[str], caption: str, font_path: str) -> Image.Image:
    canvas = Image.new("RGB", (W, H), (10, 10, 14))
    cells = layout(len(stills))
    for img, (l, t, r, b) in zip(stills, cells):
        canvas.paste(fill_cell(img, (r - l, b - t)), (l, t))
    base = canvas.convert("RGBA")

    shade = Image.linear_gradient("L").resize((W, int(H * 0.5))).point(lambda v: int(v * 0.96))
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    layer.paste(Image.new("RGBA", shade.size, (8, 8, 12, 255)), (0, H - shade.height), shade)
    base = Image.alpha_composite(base, layer)

    draw = ImageDraw.Draw(base)
    margin, max_w = 88, W - 176
    top_limit, bottom_limit = int(H * 0.66), H - 150
    text = "\n".join(quotes)
    for size in (50, 44, 38, 34, 30, 26, 22):
        font = ImageFont.truetype(font_path, size)
        gap = int(size * 0.55)
        wrapped = [_wrap(draw, q, font, max_w) for q in quotes]
        line_h = int(size * 1.55)
        total = sum(len(w) * line_h for w in wrapped) + gap * (len(quotes) - 1)
        if total <= bottom_limit - top_limit:
            break
    y = bottom_limit - total
    for i, lines in enumerate(wrapped):
        for line in lines:
            draw.text((margin + 2, y + 2), line, font=font, fill=(0, 0, 0, 170))
            draw.text((margin, y), line, font=font, fill=(255, 255, 255, 245))
            y += line_h
        y += gap
    small = ImageFont.truetype(font_path, 26)
    draw.line((margin, H - 118, margin + 72, H - 118), fill=(255, 255, 255, 180), width=2)
    draw.text((margin, H - 98), caption, font=small, fill=(255, 255, 255, 200))
    return base.convert("RGB")


class CollageService:
    def __init__(self, settings: Settings, db: Database, tmdb: TMDB) -> None:
        self._s, self._db, self._tmdb = settings, db, tmdb

    async def _download(self, client: httpx.AsyncClient, url: str) -> Image.Image:
        try:
            resp = await client.get(url)
        except httpx.TimeoutException as exc:
            raise AppError("still_timeout", f"Downloading a still timed out. {describe(exc)}", 504) from exc
        except httpx.HTTPError as exc:
            raise AppError("still_unreachable", f"Cannot reach the TMDB image server. {describe(exc)}", 502) from exc
        if resp.status_code >= 400:
            raise AppError("still_download_failed", f"TMDB image server returned HTTP {resp.status_code}", 502)
        try:
            img = Image.open(io.BytesIO(resp.content))
            img.load()
            return img
        except OSError as exc:
            raise AppError("still_decode_failed", f"A downloaded still is not a readable image. {describe(exc)}", 502) from exc

    async def create(self, entry: dict[str, Any], quotes: list[str]) -> dict[str, Any]:
        quotes = [q.strip() for q in quotes if q.strip()]
        if not quotes:
            raise AppError("poster_quote_empty", "Pick at least one quote", 422)
        if len(quotes) > MAX_QUOTES:
            raise AppError("poster_too_many_quotes", f"A collage holds at most {MAX_QUOTES} quotes, got {len(quotes)}", 422)
        if any(len(q) > MAX_QUOTE_LEN for q in quotes):
            raise AppError("poster_quote_too_long", f"Each quote must be at most {MAX_QUOTE_LEN} characters", 422)
        if not entry.get("tmdb_id"):
            raise AppError("entry_has_no_tmdb_id",
                           "This entry was not added from TMDB, so it has no stills to use", 422)
        font_path = find_font(self._s.poster_font)
        urls = await self._tmdb.stills(entry["tmdb_id"])
        if len(urls) < MIN_STILLS:
            raise AppError("stills_not_found", f"TMDB has no usable stills for 《{entry['title']}》", 404)
        want = min(len(urls), max(2, min(4, len(quotes) + 1)) if len(quotes) > 1 else 3)
        pool = urls[: want + 2]
        async with httpx.AsyncClient(timeout=30) as client:
            results = await asyncio.gather(*(self._download(client, u) for u in pool), return_exceptions=True)
        images = [r for r in results if isinstance(r, Image.Image)][:want]
        if not images:
            first = next(r for r in results if isinstance(r, AppError))
            raise first
        caption = f"《{entry['title']}》" + (f" · {entry['year']}" if entry.get("year") else "")
        poster = compose_collage(images, quotes, caption, font_path)
        self._s.posters_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{entry['id']}-{secrets.token_hex(6)}.png"
        poster.save(self._s.posters_dir / filename, "PNG")
        return self._db.add_poster(entry["id"], "\n".join(quotes), "剧照拼贴", f"{len(images)} stills", filename)
