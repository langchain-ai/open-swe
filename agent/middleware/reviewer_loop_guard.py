"""Anti-repetition guard for the reviewer.

The reviewer sometimes re-issues byte-identical ``read_file``/``grep`` (and
diff-fetch) calls on the same path when the result comes back empty/null,
looping until it exhausts the child-run cap and never publishes a review.
This middleware watches for repeated identical ``(tool_name, args)`` calls whose
prior results were empty and short-circuits the repeat with a synthesized
"already attempted, empty" tool message so the agent stops re-fetching and moves
on to publishing.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger(__name__)

_EMPTY_RESULT_MAX_CHARS = 6
_MAX_EMPTY_REPEATS = 1
_GUARDED_TOOLS = frozenset({"read_file", "grep"})

_EMPTY_LITERALS = frozenset({"", "null", "none", "{}", "[]", '""', "''"})


def _canonical_args(args: object) -> str:
    if isinstance(args, dict):
        try:
            return json.dumps(args, sort_keys=True, default=str)
        except TypeError:
            return str(sorted(args.items()))
    return str(args)


def _result_text(result: ToolMessage | Command) -> str:
    if isinstance(result, ToolMessage):
        content = result.content
    else:
        content = None
        update = getattr(result, "update", None)
        if isinstance(update, dict):
            messages = update.get("messages")
            if isinstance(messages, list):
                for message in reversed(messages):
                    if isinstance(message, ToolMessage):
                        content = message.content
                        break
    if isinstance(content, list):
        parts = [
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        ]
        content = " ".join(parts)
    return content if isinstance(content, str) else ""


def _is_empty_result(result: ToolMessage | Command) -> bool:
    text = _result_text(result).strip()
    if text.lower() in _EMPTY_LITERALS:
        return True
    return len(text) <= _EMPTY_RESULT_MAX_CHARS


class ReviewerLoopGuardMiddleware(AgentMiddleware):
    """Short-circuit repeated identical reviewer tool calls that return empty."""

    def __init__(
        self,
        *,
        guarded_tools: frozenset[str] = _GUARDED_TOOLS,
        max_empty_repeats: int = _MAX_EMPTY_REPEATS,
    ) -> None:
        super().__init__()
        self._guarded_tools = guarded_tools
        self._max_empty_repeats = max_empty_repeats
        self._empty_calls: dict[tuple[str, str], int] = {}

    def _short_circuit_message(self, request: ToolCallRequest, name: str) -> ToolMessage:
        payload = {
            "status": "skipped",
            "name": name,
            "reason": "repeated_empty_result",
            "detail": (
                f"`{name}` on these exact args already returned an empty/no-match "
                "result and was skipped to avoid looping. Do NOT repeat this "
                "identical call. Adapt the path/pattern once, or record the "
                "file/pattern as unavailable and proceed toward publish_review."
            ),
        }
        tool_call = request.tool_call if isinstance(request.tool_call, dict) else {}
        return ToolMessage(
            content=json.dumps(payload),
            tool_call_id=tool_call.get("id"),
            status="error",
        )

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        tool_call = request.tool_call if isinstance(request.tool_call, dict) else {}
        name = tool_call.get("name")
        if name not in self._guarded_tools:
            return await handler(request)

        key = (name, _canonical_args(tool_call.get("args")))
        if self._empty_calls.get(key, 0) > self._max_empty_repeats:
            logger.info("Reviewer loop guard short-circuited repeated empty call to %s", name)
            return self._short_circuit_message(request, name)

        result = await handler(request)
        if _is_empty_result(result):
            self._empty_calls[key] = self._empty_calls.get(key, 0) + 1
        return result
