"""Keep named tools from the model while they stay registered.

A call of a hidden tool by name still runs, as in-process.
"""

from collections.abc import Mapping

from langchain.agents.middleware.types import AgentState
from langgraph.runtime import Runtime

from openswe.middleware.model_call_shape import CallShape
from openswe.middleware.trace import OpenSWEMiddleware


class ExcludeToolsMiddleware(OpenSWEMiddleware):
    """Keep the named tools off every model call."""

    state_schema = AgentState

    def __init__(self, *, excluded: frozenset[str]) -> None:
        self._excluded = excluded

    async def ashape_model_call(
        self, call: CallShape, state: Mapping[str, object], runtime: Runtime
    ) -> CallShape:
        return call.hiding(self._excluded)
