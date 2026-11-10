from __future__ import annotations


class AppError(Exception):
    """Error with a stable lower_snake_case code and a human-readable description."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"
