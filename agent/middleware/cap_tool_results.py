"""Cap oversized ToolMessage content so a single tool result can't dominate context.

Runs as a post-tool wrapper: when a tool returns a `ToolMessage` whose `.content`
exceeds the configured character cap, the content is rewritten to a head + an
``[omitted N chars]`` marker + tail. ``is_error``, ``name``, ``tool_call_id``,
``status`` and any ``artifact`` are preserved untouched so callers (and the UI)
still see the full structured metadata.

The cap is a defensive ceiling for tools like ``web_search`` and ``execute``
that can produce 500kB–1.3M-char payloads which would otherwise be re-sent on
every subsequent LLM turn.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger(__name__)


def _tool_name(request: ToolCallRequest) -> str | None:
    tool_call = getattr(request, "tool_call", None)
    if isinstance(tool_call, Mapping):
        name = tool_call.get("name")
        return name if isinstance(name, str) and name else None
    return None


class CapToolResultsMiddleware(AgentMiddleware):
    """Truncate ToolMessage content above the cap to head + omitted-marker + tail."""

    state_schema = AgentState

    MAX_TOOL_RESULT_CHARS = 200_000
    HEAD_CHARS = 4_000
    TAIL_CHARS = 4_000

    def __init__(
        self,
        max_tool_result_chars: int | None = None,
        head_chars: int | None = None,
        tail_chars: int | None = None,
    ) -> None:
        super().__init__()
        self.max_tool_result_chars = (
            max_tool_result_chars
            if max_tool_result_chars is not None
            else self.MAX_TOOL_RESULT_CHARS
        )
        self.head_chars = head_chars if head_chars is not None else self.HEAD_CHARS
        self.tail_chars = tail_chars if tail_chars is not None else self.TAIL_CHARS

    def _cap(self, result: ToolMessage | Command, tool_name: str | None) -> None:
        if not isinstance(result, ToolMessage):
            return
        content = result.content
        if not isinstance(content, str):
            return
        original_len = len(content)
        if original_len <= self.max_tool_result_chars:
            return
        omitted = original_len - self.head_chars - self.tail_chars
        marker = f"\n\n... [omitted {omitted} chars by CapToolResultsMiddleware] ...\n\n"
        result.content = content[: self.head_chars] + marker + content[-self.tail_chars :]
        logger.info(
            "cap_tool_results: truncated tool=%s original_chars=%d new_chars=%d",
            tool_name or "<unknown>",
            original_len,
            len(result.content),
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        result = handler(request)
        self._cap(result, _tool_name(request))
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        result = await handler(request)
        self._cap(result, _tool_name(request))
        return result
