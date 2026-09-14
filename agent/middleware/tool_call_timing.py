"""Persist server-measured tool-call durations on tool results."""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from time import monotonic_ns

from langchain.agents.middleware.types import AgentState
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from agent.middleware.trace import OpenSWEMiddleware

TOOL_CALL_ELAPSED_MS_KEY = "open_swe_tool_elapsed_ms"


def _timed_tool_message(message: ToolMessage, elapsed_ms: int) -> ToolMessage:
    return message.model_copy(
        update={
            "response_metadata": {
                **message.response_metadata,
                TOOL_CALL_ELAPSED_MS_KEY: elapsed_ms,
            }
        }
    )


def _timed_message(message: object, tool_call_id: str, elapsed_ms: int) -> object:
    if not isinstance(message, ToolMessage) or message.tool_call_id != tool_call_id:
        return message
    return _timed_tool_message(message, elapsed_ms)


def _timed_result(
    result: ToolMessage | Command,
    tool_call_id: str,
    elapsed_ms: int,
) -> ToolMessage | Command:
    if isinstance(result, ToolMessage):
        return _timed_tool_message(result, elapsed_ms)

    update = result.update
    if isinstance(update, dict):
        messages = update.get("messages")
        if not isinstance(messages, list):
            return result
        timed_messages = [_timed_message(message, tool_call_id, elapsed_ms) for message in messages]
        return replace(result, update={**update, "messages": timed_messages})
    if isinstance(update, list):
        return replace(
            result,
            update=[_timed_message(message, tool_call_id, elapsed_ms) for message in update],
        )
    return result


class ToolCallTimingMiddleware(OpenSWEMiddleware):
    """Measure tool execution with a monotonic server clock."""

    state_schema = AgentState

    def __init__(self, clock: Callable[[], int] = monotonic_ns) -> None:
        self._clock = clock

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        started_at = self._clock()
        result = await handler(request)
        elapsed_ms = max(0, (self._clock() - started_at) // 1_000_000)
        tool_call_id = request.tool_call.get("id") or ""
        return _timed_result(result, tool_call_id, elapsed_ms)
