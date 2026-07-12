"""Cap oversized ``execute`` tool output before it enters the model context.

A single shell command (notably a full-PR ``gh api .../compare/<sha>...<sha>``
diff fetch) can return multiple megabytes of text that is otherwise ingested
verbatim, dominating a run's token budget. This middleware truncates any
``execute`` result over ``_MAX_EXECUTE_RESULT_CHARS`` to a head/tail slice and
appends a marker that prompts the agent to re-fetch a narrower, scoped slice.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger(__name__)

_EXECUTE = "execute"

# High enough not to disturb normal command output, low enough to stop a single
# command from dumping 2M+ chars into context.
_MAX_EXECUTE_RESULT_CHARS = 300_000
# Keep the head and tail so both the command's start and its final status/errors
# survive truncation.
_HEAD_CHARS = 200_000
_TAIL_CHARS = 80_000


def _tool_name(request: ToolCallRequest) -> str | None:
    tool_call = getattr(request, "tool_call", None)
    if isinstance(tool_call, Mapping):
        name = tool_call.get("name")
        return name if isinstance(name, str) else None
    return None


def _truncate(text: str) -> str:
    if len(text) <= _MAX_EXECUTE_RESULT_CHARS:
        return text
    omitted = len(text) - _HEAD_CHARS - _TAIL_CHARS
    marker = (
        f"\n\n[output truncated at {_MAX_EXECUTE_RESULT_CHARS} chars — "
        f"{omitted} chars omitted; re-run with narrower scope or "
        "--jq field selection to fetch a bounded slice]\n\n"
    )
    return f"{text[:_HEAD_CHARS]}{marker}{text[-_TAIL_CHARS:]}"


def _cap_content(content: Any) -> Any:
    if isinstance(content, str):
        return _truncate(content)
    if isinstance(content, list):
        capped: list[Any] = []
        for block in content:
            if isinstance(block, Mapping) and isinstance(block.get("text"), str):
                capped.append({**block, "text": _truncate(block["text"])})
            elif isinstance(block, str):
                capped.append(_truncate(block))
            else:
                capped.append(block)
        return capped
    return content


class ExecuteOutputCapMiddleware(AgentMiddleware):
    """Truncate oversized ``execute`` tool results before they reach the model."""

    state_schema = AgentState

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        result = await handler(request)
        if _tool_name(request) != _EXECUTE or not isinstance(result, ToolMessage):
            return result
        try:
            result.content = _cap_content(result.content)
        except Exception:
            logger.exception("Failed to cap execute tool output")
        return result
