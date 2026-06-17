"""Drop no-op ``write_todos`` calls that re-emit the prior todo list.

The deepagents harness ships a ``write_todos`` tool and a system prompt that
nudges the LLM to update the todo list after every tool call.  On long
multi-turn re-reviews the model frequently re-emits a list that is
structurally identical to the prior state, and each call re-bills the whole
conversation through the model.  This middleware short-circuits those calls
with a synthesized ToolMessage so they cost nothing downstream.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger(__name__)


def _canonical(todos: Any) -> str | None:
    """Return a stable JSON string for *todos*, or ``None`` if not a list."""
    if not isinstance(todos, list):
        return None
    try:
        return json.dumps(todos, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return None


def _state_get(state: Any, key: str) -> Any:
    if isinstance(state, dict):
        return state.get(key)
    return getattr(state, key, None)


class WriteTodosDedupeMiddleware(AgentMiddleware):
    """Drop ``write_todos`` calls whose todos match the current state."""

    state_schema = AgentState

    def _maybe_short_circuit(self, request: ToolCallRequest) -> ToolMessage | None:
        tool_call = request.tool_call
        if not isinstance(tool_call, dict) or tool_call.get("name") != "write_todos":
            return None
        args = tool_call.get("args") or {}
        new_canonical = _canonical(args.get("todos"))
        if new_canonical is None:
            return None
        prior_canonical = _canonical(_state_get(request.state, "todos"))
        if prior_canonical is None or prior_canonical != new_canonical:
            return None
        logger.info("Dropping no-op write_todos call (todos unchanged)")
        return ToolMessage(
            content="No-op: todo list unchanged; skipped re-emitting the same list.",
            tool_call_id=tool_call.get("id", ""),
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        short_circuit = self._maybe_short_circuit(request)
        if short_circuit is not None:
            return short_circuit
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        short_circuit = self._maybe_short_circuit(request)
        if short_circuit is not None:
            return short_circuit
        return await handler(request)
