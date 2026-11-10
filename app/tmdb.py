from __future__ import annotations

from typing import Any

import httpx

from .config import Settings
from .errors import AppError, describe

BASE = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/w500"
STILL = "https://image.tmdb.org/t/p/w1280"


def _year(date: str | None) -> int | None:
    return int(date[:4]) if date and date[:4].isdigit() else None


class TMDB:
    def __init__(self, settings: Settings) -> None:
        self._key = settings.tmdb_api_key

    @property
    def configured(self) -> bool:
        return bool(self._key)

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self._key:
            raise AppError("tmdb_not_configured", "TMDB_API_KEY is not set in .env", 503)
        headers: dict[str, str] = {}
        query = {"language": "zh-CN", **params}
        if self._key.startswith("eyJ"):  # v4 read-access token
            headers["Authorization"] = f"Bearer {self._key}"
        else:
            query["api_key"] = self._key
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{BASE}{path}", params=query, headers=headers)
        except httpx.TimeoutException as exc:
            raise AppError("tmdb_timeout", f"TMDB did not answer in time. {describe(exc)}", 504) from exc
        except httpx.HTTPError as exc:
            raise AppError("tmdb_unreachable", f"Cannot reach TMDB. {describe(exc)}", 502) from exc
        if resp.status_code == 401:
            raise AppError("tmdb_auth_failed", "TMDB rejected TMDB_API_KEY (HTTP 401)", 502)
        if resp.status_code >= 400:
            raise AppError("tmdb_request_failed",
                           f"TMDB returned HTTP {resp.status_code}: {resp.text[:200]}", 502)
        try:
            return resp.json()
        except ValueError as exc:
            raise AppError("tmdb_response_invalid",
                           f"TMDB returned a non-JSON body. {describe(exc)}", 502) from exc

    async def search(self, query: str, year: int | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"query": query}
        if year:
            params["year"] = year
        data = await self._get("/search/movie", params)
        return [
            {
                "tmdb_id": r["id"],
                "title": r.get("title") or r.get("original_title") or "",
                "original_title": r.get("original_title") or "",
                "year": _year(r.get("release_date")),
                "overview": r.get("overview") or "",
                "poster_url": f"{IMG}{r['poster_path']}" if r.get("poster_path") else "",
            }
            for r in data.get("results", [])[:8]
        ]

    async def details(self, tmdb_id: int) -> dict[str, Any]:
        d = await self._get(f"/movie/{tmdb_id}", {"append_to_response": "credits"})
        directors = [c["name"] for c in d.get("credits", {}).get("crew", []) if c.get("job") == "Director"]
        return {
            "tmdb_id": d["id"],
            "title": d.get("title") or d.get("original_title") or "",
            "original_title": d.get("original_title") or "",
            "year": _year(d.get("release_date")),
            "director": "、".join(directors),
            "overview": d.get("overview") or "",
            "poster_url": f"{IMG}{d['poster_path']}" if d.get("poster_path") else "",
            "genres": [g["name"] for g in d.get("genres", [])],
        }

    async def stills(self, tmdb_id: int, limit: int = 8) -> list[str]:
        """Landscape stills (backdrops) without baked-in text, best first. Falls back to captioned ones."""
        data = await self._get(f"/movie/{tmdb_id}/images", {"include_image_language": "null,en,zh"})
        shots = [b for b in data.get("backdrops", []) if b.get("file_path") and (b.get("width") or 0) >= 1280]
        shots.sort(key=lambda b: (b.get("iso_639_1") is not None, -(b.get("vote_average") or 0), -(b.get("width") or 0)))
        return [f"{STILL}{b['file_path']}" for b in shots[:limit]]

    async def original_title(self, tmdb_id: int) -> str:
        """English title for Wikiquote lookups. Uses language=en-US because the default is zh-CN."""
        d = await self._get(f"/movie/{tmdb_id}", {"language": "en-US"})
        return d.get("title") or d.get("original_title") or ""
