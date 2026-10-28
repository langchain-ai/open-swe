"""The run's operations, which the backend keeps and reads to it.

A run's context names the operations its catalog lists, and the run reads their
specs as it starts (``LISTED``). The rest, such as every connected integration's,
stay on the backend: the run's ``search_tools`` finds them by what they do
(``SEARCH``), and the run calls one by the name a search gave it, from code mode or
directly, once the backend has said what it is (``FIND``).
"""

from pydantic import BaseModel, JsonValue

from runtime_api.catalog import OperationSpec
from runtime_api.endpoint import QUICK_CALL_SECONDS, Endpoint
from runtime_api.operations import RunRequest


class ToolSearch(RunRequest):
    query: str
    limit: int = 5


class ToolMatch(BaseModel):
    """One operation a search found, as the model reads it."""

    name: str
    description: str
    parameters: dict[str, JsonValue]
    # How code mode calls it, as ``tools.<code_name>``; None when code cannot call it.
    code_name: str | None = None


class ToolMatches(BaseModel):
    matches: list[ToolMatch] = []


class OperationLookup(RunRequest):
    # The name the run called it by: its own, or the one code mode calls it by.
    name: str


class OperationNames(RunRequest):
    names: list[str]


class ListedOperations(BaseModel):
    # Each with the run's grant of it.
    operations: list[OperationSpec] = []


class FoundOperation(BaseModel):
    # With the run's grant of it; None when the run has no operation by that name.
    operation: OperationSpec | None = None


# Each only reads, so it is safe to send again.
LISTED = Endpoint(
    "listed_operations",
    OperationNames,
    ListedOperations,
    seconds=QUICK_CALL_SECONDS,
    delivery="idempotent",
)
SEARCH = Endpoint(
    "search_tools", ToolSearch, ToolMatches, seconds=QUICK_CALL_SECONDS, delivery="idempotent"
)
FIND = Endpoint(
    "find_operation",
    OperationLookup,
    FoundOperation,
    seconds=QUICK_CALL_SECONDS,
    delivery="idempotent",
)
