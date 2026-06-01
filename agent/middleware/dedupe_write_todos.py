"""Dedupe redundant write_todos calls.

Reviewer traces showed the agent calling ``write_todos`` 45-77 times per
invocation while replanning the same list with cosmetic phrasing changes,
inflating trajectory length and token cost without changing any decision.
When a new call's todos list is structurally identical (after lowercasing,
stripping, and sorting by content+status) to the prior persisted list, this
middleware short-circuits the call: it returns a ToolMessage hint instead of
invoking the underlying tool, so the state is not rewritten and the agent is
nudged toward the next concrete action.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger(__name__)

_NOOP_HINT = (
    "todos unchanged — proceed with the next concrete action instead of rewriting the same plan."
)


def _normalize_todos(todos: object) -> tuple[tuple[str, str], ...] | None:
    """Normalize a todos list for structural equality comparison."""
    if not isinstance(todos, list):
        return None
    normalized: list[tuple[str, str]] = []
    for item in todos:
        if not isinstance(item, dict):
            return None
        content = item.get("content")
        status = item.get("status")
        if not isinstance(content, str) or not isinstance(status, str):
            return None
        normalized.append((content.strip().lower(), status.strip().lower()))
    return tuple(sorted(normalized))


def _extract_prior_todos(state: object) -> object:
    if isinstance(state, dict):
        return state.get("todos")
    return getattr(state, "todos", None)


class DedupeWriteTodosMiddleware(AgentMiddleware):
    """Short-circuit write_todos calls that don't change the persisted list."""

    state_schema = AgentState

    def _maybe_noop(self, request: ToolCallRequest) -> ToolMessage | None:
        tool_call = request.tool_call
        if not isinstance(tool_call, dict) or tool_call.get("name") != "write_todos":
            return None
        args = tool_call.get("args") or {}
        new_normalized = _normalize_todos(args.get("todos"))
        if new_normalized is None:
            return None
        prior_normalized = _normalize_todos(_extract_prior_todos(request.state))
        if prior_normalized is None or prior_normalized != new_normalized:
            return None
        tool_call_id = tool_call.get("id") or ""
        logger.info("Skipping no-op write_todos call (todos unchanged)")
        return ToolMessage(_NOOP_HINT, tool_call_id=tool_call_id, name="write_todos")

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        noop = self._maybe_noop(request)
        if noop is not None:
            return noop
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        noop = self._maybe_noop(request)
        if noop is not None:
            return noop
        return await handler(request)
