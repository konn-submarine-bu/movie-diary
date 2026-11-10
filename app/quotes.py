from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import quote as urlquote

import httpx

from .errors import AppError, describe

USER_AGENT = "movie-diary/0.1 (personal movie diary; https://github.com/)"
CACHE_TTL = 6 * 3600
MAX_QUOTES = 60
MIN_LEN, MAX_LEN = 6, 220
SKIP_SECTIONS = {"cast", "see also", "external links", "references", "taglines", "dialogue", "sources",
                 "演员", "演員", "参见", "參見", "外部链接", "外部連結", "参考", "參考"}

_cache: dict[tuple[str, str], tuple[float, list[dict[str, str]]]] = {}


def clean_wikitext(text: str) -> str:
    text = re.sub(r"<ref[^>]*?/>|<ref[^>]*>.*?</ref>", "", text, flags=re.S)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("'''", "").replace("''", "")
    text = re.sub(r"\[https?://\S+\s*([^\]]*)\]", r"\1", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def parse_quotes(wikitext: str) -> list[dict[str, str]]:
    """Top-level bullet lines under each section; nested bullets are context or replies, so they are skipped."""
    out: list[dict[str, str]] = []
    section = ""
    skip = False
    seen: set[str] = set()
    for raw in wikitext.splitlines():
        head = re.match(r"^(={2,4})\s*(.+?)\s*\1\s*$", raw)
        if head:
            section = clean_wikitext(head.group(2))
            skip = section.lower() in SKIP_SECTIONS
            continue
        if skip or not raw.startswith("*") or raw.startswith("**"):
            continue
        text = clean_wikitext(raw[1:])
        if not MIN_LEN <= len(text) <= MAX_LEN or text in seen:
            continue
        seen.add(text)
        out.append({"text": text, "speaker": section})
        if len(out) >= MAX_QUOTES:
            break
    return out


class Wikiquote:
    """Reads movie quotes from Wikiquote (CC BY-SA). Lookups are cached because the API rate-limits anonymous clients."""

    def __init__(self, timeout: float = 15) -> None:
        self._timeout = timeout

    async def _api(self, client: httpx.AsyncClient, host: str, params: dict[str, Any]) -> dict[str, Any]:
        resp = await client.get(f"https://{host}/w/api.php", params={"format": "json", "redirects": 1, **params})
        if resp.status_code == 429:
            raise AppError("quotes_rate_limited", "Wikiquote is rate limiting this client. Try again in a minute.", 429)
        if resp.status_code >= 400:
            raise AppError("quotes_request_failed", f"Wikiquote returned HTTP {resp.status_code}: {resp.text[:200]}", 502)
        if "json" not in resp.headers.get("content-type", ""):
            raise AppError("quotes_rate_limited",
                           f"Wikiquote answered with a non-JSON page (usually rate limiting): {resp.text[:120]!r}", 429)
        return resp.json()

    async def _page(self, client: httpx.AsyncClient, host: str, title: str) -> dict[str, Any] | None:
        data = await self._api(client, host, {"action": "parse", "page": title, "prop": "wikitext", "redirects": 1})
        if "error" in data:
            return None
        parsed = data["parse"]
        return {"title": parsed["title"], "wikitext": parsed["wikitext"]["*"]}

    async def find(self, titles: list[tuple[str, str]]) -> dict[str, Any]:
        """titles: (language, page title) candidates. Returns one group per distinct page that has quotes."""
        groups: list[dict[str, Any]] = []
        tried: list[str] = []
        seen_pages: set[str] = set()
        async with httpx.AsyncClient(timeout=self._timeout, headers={"User-Agent": USER_AGENT}) as client:
            for lang, title in dict.fromkeys(titles):
                key = (lang, title)
                hit = _cache.get(key)
                if hit and time.time() - hit[0] < CACHE_TTL:
                    quotes = hit[1]
                else:
                    host = f"{lang}.wikiquote.org"
                    try:
                        page = await self._page(client, host, title)
                    except httpx.TimeoutException as exc:
                        raise AppError("quotes_timeout", f"Wikiquote did not answer in time. {describe(exc)}", 504) from exc
                    except httpx.HTTPError as exc:
                        raise AppError("quotes_unreachable", f"Cannot reach Wikiquote. {describe(exc)}", 502) from exc
                    quotes = []
                    if page:
                        url = f"https://{host}/wiki/{urlquote(page['title'].replace(' ', '_'))}"
                        quotes = [{**q, "source": url, "lang": lang} for q in parse_quotes(page["wikitext"])]
                    _cache[key] = (time.time(), quotes)
                tried.append(f"{lang}:{title}")
                if quotes and quotes[0]["source"] not in seen_pages:
                    seen_pages.add(quotes[0]["source"])
                    groups.append({"lang": lang, "source": quotes[0]["source"], "quotes": quotes})
        return {"groups": groups, "tried": tried}
