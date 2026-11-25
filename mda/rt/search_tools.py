"""``search_tools``: the run's operations that best match what the model looks for.

Code mode keeps most tools off the model's list, so the model finds them here, with
the schemas it needs to call them and the name code mode calls them by. The run's
catalog leaves its integrations out; the backend keeps every tool, and the run reads
the set its catalog names (``Catalog.tool_set``) once per process and searches it
here (``runtime_api.tool_search``).
"""

import threading
from collections import OrderedDict
from typing import Final

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import JsonValue
from runtime_api.catalog import SEARCH_TOOLS, ToolSpec
from runtime_api.endpoint import call
from runtime_api.operations import RunRequest
from runtime_api.tool_search import DETAILED, RESOLVE, TOOL_SET, ToolIndex, ToolLookup

from rt.backend import OpenSweBackend, current_run
from rt.prompts import prompt
from rt.tools import run_id

# How many sets this process keeps read: a set never changes under its digest, and
# one may come to a megabyte.
_READ_LIMIT: Final = 16
# The sets read, least recently used first; runs on other event loops share them.
_read: OrderedDict[str, ToolIndex] = OrderedDict()
_reading = threading.Lock()


async def run_tool_index(backend: OpenSweBackend) -> ToolIndex | None:
    """Every tool the calling run may call, to search and resolve here; None when its
    catalog names no set, which no backend sends any more."""
    digest = current_run().catalog.tool_set
    if digest is None:
        return None
    with _reading:
        index = _read.get(digest)
        if index is not None:
            _read.move_to_end(digest)
            return index
    # Two runs missing the same set at once both read it, which costs a read, no more.
    kept = await call(backend, TOOL_SET, RunRequest(run_id=run_id()))
    index = ToolIndex(kept.tools, kept.excluded)
    if kept.digest == digest:
        with _reading:
            _read[digest] = index
            while len(_read) > _READ_LIMIT:
                _read.popitem(last=False)
    return index


async def resolve_tool(backend: OpenSweBackend, name: str) -> ToolSpec | None:
    """The tool the calling run calls as ``name``, with its grant: a run that searches its
    set here finds here that it has no such tool; only the grant of one it has comes
    from the backend."""
    index = await run_tool_index(backend)
    if index is not None and index.resolve(name) is None:
        return None
    lookup = ToolLookup(run_id=run_id(), name=name)
    return (await call(backend, RESOLVE, lookup)).tool


def search_tools_tool(backend: OpenSweBackend, description: str | None = None) -> BaseTool:
    """``search_tools`` over every tool the run may call."""

    async def search_tools(query: str) -> list[dict[str, JsonValue]] | str:
        index = await run_tool_index(backend)
        found = [] if index is None else index.search(query)
        if not found:
            return prompt("tools/search-tools-empty", query=query)
        return [
            (ToolIndex.match(tool) if rank < DETAILED else ToolIndex.brief(tool)).model_dump(
                mode="json"
            )
            for rank, tool in enumerate(found)
        ]

    return StructuredTool.from_function(
        coroutine=search_tools,
        name=SEARCH_TOOLS,
        # The backend's description, which says how to call what it finds.
        description=description or "Search the tools available in this run.",
    )
