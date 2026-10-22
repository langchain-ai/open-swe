"""Find the tools a run can call, with the schemas the model needs to call them."""

from collections.abc import Callable

from langchain_core.tools import BaseTool, tool
from pydantic import JsonValue

from openswe.sandboxes.tool_runtime import ToolSurface

SEARCH_TOOLS_NAME = "search_tools"
_MAX_RESULTS = 20


def search_tools_tool(
    surface: ToolSurface,
    code_name: Callable[[str], str | None],
    *,
    allowed: frozenset[str] | None = None,
) -> BaseTool:
    """``search_tools`` over ``surface``; ``code_name`` names a tool's ``tools.<name>`` in eval."""

    @tool(SEARCH_TOOLS_NAME)
    async def search_tools(
        query: str, limit: int = 5, names: list[str] | None = None
    ) -> list[dict[str, JsonValue]]:
        """Search the tools available in this run."""
        selected = frozenset(names) if names is not None else allowed
        if allowed is not None and selected is not None:
            selected &= allowed
        return [
            {
                **description.model_dump(mode="json"),
                "code_name": code_name(description.name),
            }
            for description in surface.search(query, max(1, min(limit, _MAX_RESULTS)), selected)
            if description.name != SEARCH_TOOLS_NAME
        ]

    return search_tools
