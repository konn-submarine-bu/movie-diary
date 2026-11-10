from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    image_base_url: str = ""
    image_api_key: str = ""
    image_model: str = ""
    image_size: str = "1024x1536"
    image_prompt_lang: str = "zh"
    image_timeout: float = 600
    tmdb_api_key: str = ""
    tavily_api_key: str = ""
    poster_font: str = ""
    max_agent_steps: int = 6

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)

    @property
    def image_configured(self) -> bool:
        return bool(self.image_base_url and self.image_api_key and self.image_model)

    @property
    def tmdb_configured(self) -> bool:
        return bool(self.tmdb_api_key)

    @property
    def search_provider(self) -> str:
        return "tavily" if self.tavily_api_key else "duckduckgo"

    @property
    def posters_dir(self) -> Path:
        return self.data_dir / "posters"

    @property
    def clips_dir(self) -> Path:
        return self.data_dir / "clips"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "diary.sqlite3"

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(ROOT / ".env")
        env = os.environ.get
        llm_base = env("LLM_BASE_URL", "").rstrip("/")
        llm_key = env("LLM_API_KEY", "")
        return cls(
            data_dir=Path(env("DATA_DIR", str(ROOT / "data"))).expanduser(),
            llm_base_url=llm_base,
            llm_api_key=llm_key,
            llm_model=env("LLM_MODEL", ""),
            image_base_url=(env("IMAGE_BASE_URL") or llm_base).rstrip("/"),
            # Only reuse the chat key when images go to the same gateway; never send it to another host.
            image_api_key=env("IMAGE_API_KEY") or (llm_key if not env("IMAGE_BASE_URL") else ""),
            image_model=env("IMAGE_MODEL", ""),
            image_size=env("IMAGE_SIZE", "1024x1536"),
            image_prompt_lang=env("IMAGE_PROMPT_LANG", "zh"),
            image_timeout=float(env("IMAGE_TIMEOUT", "600")),
            tmdb_api_key=env("TMDB_API_KEY", ""),
            tavily_api_key=env("TAVILY_API_KEY", ""),
            poster_font=env("POSTER_FONT", ""),
            max_agent_steps=int(env("MAX_AGENT_STEPS", "6")),
        )
