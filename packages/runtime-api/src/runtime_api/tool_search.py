"""The run's operations its catalog leaves out, which the backend finds for it.

A run's catalog lists only the operations the model is offered as tools. The rest,
such as every connected integration's, stay on the backend: the run's
``search_tools`` finds them by what they do (``SEARCH``), and the run calls one by
the name a search gave it, from code mode or directly, once the backend has said
what it is (``FIND``).
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


class FoundOperation(BaseModel):
    # With the run's grant of it; None when the run has no operation by that name.
    operation: OperationSpec | None = None


# Both only read, so they are safe to send again.
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
