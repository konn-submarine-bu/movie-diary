from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from .config import Settings
from .errors import AppError, describe

_DDG_LINK = re.compile(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_DDG_SNIPPET = re.compile(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', re.S)
_TAG = re.compile(r"<[^>]+>")


def _clean(fragment: str) -> str:
    return html.unescape(_TAG.sub("", fragment)).strip()


def _unwrap_ddg(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(target)
    return href


def parse_duckduckgo(page: str, limit: int) -> list[dict[str, str]]:
    links = _DDG_LINK.findall(page)
    snippets = _DDG_SNIPPET.findall(page)
    results = []
    for i, (href, title) in enumerate(links[:limit]):
        url = _unwrap_ddg(href)
        if not url.startswith(("http://", "https://")):
            continue
        results.append({
            "title": _clean(title),
            "url": url,
            "snippet": _clean(snippets[i]) if i < len(snippets) else "",
        })
    return results


class WebSearch:
    def __init__(self, settings: Settings) -> None:
        self._tavily_key = settings.tavily_api_key
        self.provider = settings.search_provider

    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        try:
            if self._tavily_key:
                return await self._tavily(query, limit)
            return await self._duckduckgo(query, limit)
        except httpx.TimeoutException as exc:
            raise AppError("search_timeout",
                           f"{self.provider} search did not answer in time. {describe(exc)}", 504) from exc
        except httpx.HTTPError as exc:
            raise AppError("search_unreachable",
                           f"Cannot reach {self.provider} search. {describe(exc)}", 502) from exc

    async def _tavily(self, query: str, limit: int) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={"api_key": self._tavily_key, "query": query, "max_results": limit},
            )
        if resp.status_code >= 400:
            raise AppError("search_request_failed",
                           f"Tavily returned HTTP {resp.status_code}: {resp.text[:200]}", 502)
        try:
            items = resp.json().get("results", [])
        except ValueError as exc:
            raise AppError("search_response_invalid",
                           f"Tavily returned a non-JSON body. {describe(exc)}", 502) from exc
        return [{"title": r.get("title", ""), "url": r.get("url", ""),
                 "snippet": (r.get("content") or "")[:400]} for r in items[:limit]]

    async def _duckduckgo(self, query: str, limit: int) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0 (movie-diary)"}) as client:
            resp = await client.post("https://html.duckduckgo.com/html/", data={"q": query})
        if resp.status_code >= 400:
            raise AppError("search_request_failed",
                           f"DuckDuckGo returned HTTP {resp.status_code}", 502)
        results = parse_duckduckgo(resp.text, limit)
        if not results:
            raise AppError("search_no_results",
                           "DuckDuckGo returned no parsable results (the page layout may have changed "
                           "or the request was rate-limited). Set TAVILY_API_KEY for a stable provider.", 502)
        return results
