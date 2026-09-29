"""Opt-in MCP exposure for tools that do not depend on an active agent thread."""

from collections.abc import Callable
from typing import Literal, TypeVar

Access = Literal["session", "admin"]
ToolT = TypeVar("ToolT", bound=Callable[..., object])
EXPOSED_TOOLS: dict[str, tuple[ToolT, Access]] = {}


def expose_mcp(*, access: Access = "session") -> Callable[[ToolT], ToolT]:
    def register(tool: ToolT) -> ToolT:
        if tool.__name__ in EXPOSED_TOOLS:
            raise ValueError(f"Duplicate MCP tool: {tool.__name__}")
        EXPOSED_TOOLS[tool.__name__] = (tool, access)
        return tool

    return register
