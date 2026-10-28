"""``search_tools``: the run's operations that best match what the model looks for.

Code mode keeps most tools off the model's list, so the model finds them here, with
the schemas it needs to call them and the name code mode calls them by. The run's
catalog leaves its integrations out; the backend keeps every tool, so the
search runs there (``runtime_api.tool_search``).
"""

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import JsonValue
from runtime_api.catalog import SEARCH_TOOLS
from runtime_api.endpoint import call
from runtime_api.tool_search import SEARCH, ToolSearch

from rt.backend import OpenSweBackend
from rt.tools import run_id


def search_tools_tool(backend: OpenSweBackend, description: str | None = None) -> BaseTool:
    """``search_tools`` over every tool the run may call."""

    async def search_tools(query: str, limit: int = 5) -> list[dict[str, JsonValue]]:
        searched = await call(
            backend, SEARCH, ToolSearch(run_id=run_id(), query=query, limit=limit)
        )
        return [match.model_dump(mode="json") for match in searched.matches]

    return StructuredTool.from_function(
        coroutine=search_tools,
        name=SEARCH_TOOLS,
        # The backend's description, which says how to call what it finds.
        description=description or "Search the tools available in this run.",
    )
