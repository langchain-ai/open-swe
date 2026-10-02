"""Opt-in MCP exposure for tools that do not depend on an active agent thread."""

from collections.abc import Awaitable, Callable
from types import FunctionType
from typing import Literal, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")

Access = Literal["session", "admin"]
EXPOSED_TOOLS: dict[str, tuple[Callable[..., Awaitable[object]], Access]] = {}


def expose_mcp(
    *, access: Access = "session"
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    def register(tool: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        if not isinstance(tool, FunctionType):
            raise TypeError("MCP exposure requires a function")
        name = tool.__name__
        if name in EXPOSED_TOOLS:
            raise ValueError(f"Duplicate MCP tool: {name}")
        EXPOSED_TOOLS[name] = (tool, access)
        return tool

    return register
