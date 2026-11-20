"""An MCP server's refusal (an RFC 7807 problem), and finding it behind a wrapped error."""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    import httpx2


class McpProblemError(RuntimeError):
    """An MCP server's refusal, with the explanation from its problem response."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


def is_problem(response: httpx2.Response) -> bool:
    content_type = response.headers.get("content-type", "")
    return response.status_code >= HTTPStatus.BAD_REQUEST and content_type.startswith(
        "application/problem+json"
    )


def problem_detail(response: httpx2.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None
    fields = body if isinstance(body, Mapping) else {}
    parts = [fields.get("detail"), fields.get("remedy")]
    text = " ".join(part for part in parts if isinstance(part, str) and part)
    return text or f"MCP server returned HTTP {response.status_code}"


_E = TypeVar("_E", bound=BaseException)


def cause_of(error: BaseException, kind: type[_E]) -> _E | None:
    """The ``kind`` exception behind ``error``, through causes and exception groups."""
    stack: list[BaseException] = [error]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, kind):
            return current
        stack.extend(getattr(current, "exceptions", ()) or ())
        stack.extend(link for link in (current.__cause__, current.__context__) if link)
    return None
