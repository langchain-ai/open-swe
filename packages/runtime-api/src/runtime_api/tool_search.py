"""The run's tools, which the backend keeps and reads to it.

A run's context names the tools its catalog lists, and the run reads their specs as
it starts (``TOOL_SPECS``). The rest, such as every connected integration's, stay on
the backend: the run's ``search_tools`` finds them by what they do (``SEARCH``), and
the run calls one by the name a search gave it, from code mode or directly, once the
backend has said what it is (``RESOLVE``).
"""

from pydantic import BaseModel, JsonValue

from runtime_api.catalog import ToolSpec
from runtime_api.endpoint import QUICK_CALL_SECONDS, Endpoint
from runtime_api.operations import RunRequest


class ToolSearch(RunRequest):
    query: str
    limit: int = 5


class ToolMatch(BaseModel):
    """One tool a search found, as the model reads it."""

    name: str
    description: str
    parameters: dict[str, JsonValue]
    # How code mode calls it, as ``tools.<code_name>``; None when code cannot call it.
    code_name: str | None = None


class ToolMatches(BaseModel):
    matches: list[ToolMatch] = []


class ToolLookup(RunRequest):
    # The name the run called it by: its own, or the one code mode calls it by.
    name: str


class ToolNames(RunRequest):
    names: list[str]


class ToolSpecs(BaseModel):
    # Each with the run's grant of it.
    tools: list[ToolSpec] = []


class ResolvedTool(BaseModel):
    # With the run's grant of it; None when the run has no tool by that name.
    tool: ToolSpec | None = None


# Each only reads, so it is safe to send again.
TOOL_SPECS = Endpoint(
    "tool_specs", ToolNames, ToolSpecs, seconds=QUICK_CALL_SECONDS, delivery="idempotent"
)
SEARCH = Endpoint(
    "search_tools", ToolSearch, ToolMatches, seconds=QUICK_CALL_SECONDS, delivery="idempotent"
)
RESOLVE = Endpoint(
    "resolve_tool", ToolLookup, ResolvedTool, seconds=QUICK_CALL_SECONDS, delivery="idempotent"
)
