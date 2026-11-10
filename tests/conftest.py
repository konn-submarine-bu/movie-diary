from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.errors import AppError  # noqa: E402
from app.llm import ChatReply, ToolCall  # noqa: E402
from app.main import create_app  # noqa: E402
from app.tmdb import TMDB  # noqa: E402

BRIEF_JSON = json.dumps({
    "mood": "雨夜里克制的孤独",
    "palette": ["#101830", "#6a5a90"],
    "image_prompt": "雨夜的空街道，路灯昏黄，电影质感，竖版构图",
}, ensure_ascii=False)


class FakeLLM:
    """Replays scripted replies; records every call it receives."""

    def __init__(self, replies: list[ChatReply] | None = None) -> None:
        self.replies = list(replies or [])
        self.calls: list[dict[str, Any]] = []

    async def chat(self, messages, tools=None):
        self.calls.append({"messages": messages, "tools": tools})
        if not self.replies:
            raise AssertionError("FakeLLM ran out of scripted replies")
        return self.replies.pop(0)


def text(content: str) -> ChatReply:
    return ChatReply(content=content)


def tool(name: str, args: dict[str, Any], call_id: str = "c1") -> ChatReply:
    return ChatReply(tool_calls=[ToolCall(id=call_id, name=name, arguments=json.dumps(args))])


class FakeImages:
    def __init__(self, error: AppError | None = None) -> None:
        self.error = error
        self.prompts: list[str] = []

    async def generate(self, prompt: str) -> bytes:
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        buf = io.BytesIO()
        Image.new("RGB", (512, 768), (40, 60, 90)).save(buf, "PNG")
        return buf.getvalue()


class FakeTMDB(TMDB):
    MOVIES = {
        1: {"tmdb_id": 1, "title": "路过人间", "original_title": "Passing By", "year": 2021,
            "director": "某导演", "overview": "一个关于告别的故事。", "poster_url": "",
            "genres": ["剧情"]},
        2: {"tmdb_id": 2, "title": "花样年华", "original_title": "In the Mood for Love", "year": 2000,
            "director": "王家卫", "overview": "六十年代的香港。", "poster_url": "", "genres": ["爱情"]},
    }

    def __init__(self) -> None:
        super().__init__(Settings(data_dir=Path("."), tmdb_api_key="x"))

    async def search(self, query, year=None):
        return [m for m in self.MOVIES.values() if query in m["title"]]

    async def details(self, tmdb_id):
        if tmdb_id not in self.MOVIES:
            raise AppError("tmdb_request_failed", f"TMDB returned HTTP 404 for {tmdb_id}", 502)
        return dict(self.MOVIES[tmdb_id])

    still_count = 5

    async def stills(self, tmdb_id, limit=8):
        return [f"https://image.example/{tmdb_id}/{i}.jpg" for i in range(self.still_count)]

    async def original_title(self, tmdb_id):
        return self.MOVIES[tmdb_id]["original_title"]


class FakeSearch:
    provider = "fake"

    def __init__(self, error: AppError | None = None) -> None:
        self.error = error
        self.queries: list[str] = []

    async def search(self, query, limit=5):
        self.queries.append(query)
        if self.error:
            raise self.error
        return [{"title": "《花样年华》解读", "url": "https://example.com/a", "snippet": "关于克制与错过"}]


class Env:
    def __init__(self, tmp_path: Path) -> None:
        self.settings = Settings(data_dir=tmp_path, llm_base_url="http://x", llm_api_key="k",
                                 llm_model="m", image_base_url="http://x", image_api_key="k",
                                 image_model="img", tmdb_api_key="x", max_agent_steps=4)
        self.llm = FakeLLM()
        self.images = FakeImages()
        self.search = FakeSearch()
        self.client = TestClient(create_app(self.settings, self.llm, self.images, FakeTMDB(), self.search))

    def add_entry(self, tmdb_id: int = 1, **extra: Any) -> dict[str, Any]:
        movie = FakeTMDB.MOVIES[tmdb_id]
        body = {"tmdb_id": tmdb_id, "title": movie["title"], "year": movie["year"], **extra}
        resp = self.client.post("/api/entries", json=body)
        assert resp.status_code == 201, resp.text
        return resp.json()


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)
