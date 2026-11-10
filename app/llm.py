from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import openai

from .config import Settings
from .errors import AppError, describe


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class ChatReply:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLM(Protocol):
    async def chat(self, messages: list[dict[str, Any]],
                   tools: list[dict[str, Any]] | None = None) -> ChatReply: ...


class OpenAICompatLLM:
    """Chat client for any OpenAI-compatible gateway that supports tool calling."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: openai.AsyncOpenAI | None = None

    def _get_client(self) -> openai.AsyncOpenAI:
        if not self._settings.llm_configured:
            raise AppError(
                "llm_not_configured",
                "LLM_BASE_URL, LLM_API_KEY and LLM_MODEL must all be set in .env", 503)
        if self._client is None:
            self._client = openai.AsyncOpenAI(
                base_url=self._settings.llm_base_url, api_key=self._settings.llm_api_key,
                timeout=120, max_retries=1)
        return self._client

    async def chat(self, messages: list[dict[str, Any]],
                   tools: list[dict[str, Any]] | None = None) -> ChatReply:
        client = self._get_client()
        kwargs: dict[str, Any] = {"model": self._settings.llm_model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        try:
            resp = await client.chat.completions.create(**kwargs)
        except openai.AuthenticationError as exc:
            raise AppError("llm_auth_failed", f"LLM rejected the API key. {describe(exc)}", 502) from exc
        except openai.RateLimitError as exc:
            raise AppError("llm_rate_limited", f"LLM rate limit or quota hit. {describe(exc)}", 502) from exc
        except openai.APITimeoutError as exc:
            raise AppError("llm_timeout", f"LLM did not answer in time. {describe(exc)}", 504) from exc
        except openai.APIConnectionError as exc:
            raise AppError("llm_unreachable", f"Cannot reach LLM_BASE_URL. {describe(exc)}", 502) from exc
        except openai.APIStatusError as exc:
            raise AppError("llm_request_failed",
                           f"LLM returned HTTP {exc.status_code}. {describe(exc)}", 502) from exc

        if not resp.choices:
            raise AppError("llm_empty_response", "LLM response contained no choices", 502)
        msg = resp.choices[0].message
        calls = [ToolCall(id=c.id, name=c.function.name, arguments=c.function.arguments or "{}")
                 for c in (msg.tool_calls or []) if getattr(c, "function", None)]
        return ChatReply(content=msg.content or "", tool_calls=calls)
