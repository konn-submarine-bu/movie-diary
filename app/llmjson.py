from __future__ import annotations

import json
import re
from typing import Any

from .errors import AppError, describe


def extract_json(text: str, code: str) -> dict[str, Any]:
    """Pull the first JSON object out of a model reply, tolerating code fences and chatter around it."""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise AppError(code, f"LLM did not return a JSON object: {text[:200]!r}", 502)
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise AppError(code, f"LLM returned malformed JSON. {describe(exc)}", 502) from exc
    if not isinstance(data, dict):
        raise AppError(code, "LLM JSON is not an object", 502)
    return data
