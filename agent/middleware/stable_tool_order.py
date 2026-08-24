"""Order parallel tool results by their tool call.

Tool results from a parallel batch land in state in completion order, which is not
the order a later run reads them back in. The provider prompt cache is a byte
prefix match, so a single swapped pair invalidates every token after the earliest
parallel batch in the thread — tens of thousands of tokens re-prefilled on the
first model call of every new run.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage


def _call_positions(message: AIMessage) -> dict[str, int]:
    positions: dict[str, int] = {}
    for position, tool_call in enumerate(message.tool_calls or []):
        call_id = (
            tool_call.get("id") if isinstance(tool_call, dict) else getattr(tool_call, "id", None)
        )
        if isinstance(call_id, str) and call_id and call_id not in positions:
            positions[call_id] = position
    return positions


def _order_messages(messages: list[AnyMessage]) -> list[AnyMessage] | None:
    """Return the message list with parallel tool results canonically ordered."""
    ordered: list[AnyMessage] = []
    changed = False
    index = 0
    while index < len(messages):
        message = messages[index]
        ordered.append(message)
        index += 1
        if not isinstance(message, AIMessage):
            continue
        positions = _call_positions(message)
        if len(positions) < 2:
            continue
        start = index
        while index < len(messages) and isinstance(messages[index], ToolMessage):
            index += 1
        block = messages[start:index]
        sorted_block = sorted(
            block,
            key=lambda result, positions=positions: positions.get(
                getattr(result, "tool_call_id", None) or "", len(positions)
            ),
        )
        if [id(result) for result in sorted_block] != [id(result) for result in block]:
            changed = True
        ordered.extend(sorted_block)
    return ordered if changed else None


class StableToolResultOrderMiddleware(AgentMiddleware):
    """Sort each parallel tool batch's results into their tool call order."""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> Any:
        ordered = _order_messages(request.messages)
        if ordered is not None:
            request = request.override(messages=ordered)
        return await handler(request)
