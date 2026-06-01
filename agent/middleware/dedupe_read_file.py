"""Per-invocation read_file deduplication middleware.

The reviewer agent occasionally re-issues identical `read_file(path, offset,
limit)` calls many times within a single run, inflating trajectories without
new information. This middleware memoizes the ToolMessage for each
``(file_path, offset, limit)`` triple seen during the invocation; on a cache
hit it returns the original content prefixed with a warning that names the
prior tool_call_id so the model adapts instead of asking again.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger(__name__)


def _cache_key(args: dict[str, Any]) -> tuple[str, int, int] | None:
    """Return the (path, offset, limit) key for a read_file call, or None."""
    path = args.get("file_path")
    if not isinstance(path, str) or not path:
        return None
    offset = args.get("offset", 0)
    limit = args.get("limit", 0)
    if not isinstance(offset, int) or not isinstance(limit, int):
        return None
    return (path, offset, limit)


def _prefix_warning(
    message: ToolMessage,
    *,
    prior_call_id: str | None,
    new_call_id: str | None,
) -> ToolMessage:
    """Return a copy of *message* with a redundant-read warning prefixed."""
    prior = prior_call_id or "<unknown>"
    warning = (
        f"WARNING: this file content was already returned by call {prior}; "
        "do not request it again. Reference the prior tool result instead of "
        "re-reading the same (file_path, offset, limit).\n\n"
    )
    content = message.content
    if isinstance(content, str):
        return ToolMessage(
            content=warning + content,
            name=message.name,
            tool_call_id=new_call_id or message.tool_call_id,
            status=message.status,
            additional_kwargs=dict(message.additional_kwargs or {}),
        )
    return ToolMessage(
        content=warning,
        name=message.name,
        tool_call_id=new_call_id or message.tool_call_id,
        status=message.status,
        additional_kwargs=dict(message.additional_kwargs or {}),
    )


class DedupeReadFileMiddleware(AgentMiddleware):
    """Memoize `read_file` results by (file_path, offset, limit) per invocation.

    A fresh middleware instance is constructed per reviewer agent build (see
    `agent.reviewer.get_reviewer_agent`), so the cache scope is one
    invocation — exactly what we want to suppress redundant identical reads
    inside a single trajectory without leaking content across runs.
    """

    state_schema = AgentState

    def __init__(self) -> None:
        super().__init__()
        self._cache: dict[tuple[str, int, int], ToolMessage] = {}

    def _maybe_cached(self, request: ToolCallRequest) -> ToolMessage | None:
        tool_call = request.tool_call
        if not isinstance(tool_call, dict) or tool_call.get("name") != "read_file":
            return None
        args = tool_call.get("args")
        if not isinstance(args, dict):
            return None
        key = _cache_key(args)
        if key is None:
            return None
        cached = self._cache.get(key)
        if cached is None:
            return None
        logger.info("read_file cache hit for %s offset=%d limit=%d", *key)
        return _prefix_warning(
            cached,
            prior_call_id=cached.tool_call_id,
            new_call_id=tool_call.get("id"),
        )

    def _store(self, request: ToolCallRequest, result: ToolMessage | Command) -> None:
        if not isinstance(result, ToolMessage) or result.status == "error":
            return
        tool_call = request.tool_call
        if not isinstance(tool_call, dict) or tool_call.get("name") != "read_file":
            return
        args = tool_call.get("args")
        if not isinstance(args, dict):
            return
        key = _cache_key(args)
        if key is None:
            return
        self._cache.setdefault(key, result)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        cached = self._maybe_cached(request)
        if cached is not None:
            return cached
        result = handler(request)
        self._store(request, result)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        cached = self._maybe_cached(request)
        if cached is not None:
            return cached
        result = await handler(request)
        self._store(request, result)
        return result
