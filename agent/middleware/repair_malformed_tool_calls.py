"""Repair malformed tool-call arguments before model calls."""

import json
import logging
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, ToolMessage

from agent.middleware.trace import OpenSWEMiddleware

logger = logging.getLogger(__name__)

_MALFORMED_TOOL_CALL_ERROR = (
    "The earlier tool call had malformed arguments and was replaced with an empty "
    "object. Re-issue the tool call with valid JSON-object arguments."
)


def _tool_call_dicts(message: AIMessage) -> Iterator[dict[str, Any]]:
    for attribute in ("tool_calls", "invalid_tool_calls"):
        calls = getattr(message, attribute, ())
        for call in calls:
            if isinstance(call, dict):
                yield call

    content = message.content
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") in {"tool_call", "invalid_tool_call"}:
            yield block
        value = block.get("value")
        if (
            block.get("type") == "non_standard"
            and isinstance(value, dict)
            and value.get("type") == "invalid_tool_call"
        ):
            yield value


def _repair_call(call: dict[str, Any]) -> bool:
    repaired = False
    for key in ("args", "arguments"):
        if key not in call:
            continue
        value = call[key]
        if isinstance(value, dict):
            continue
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                call[key] = parsed
                continue
        call[key] = {}
        repaired = True
    return repaired


def _synthetic_tool_message(call_id: str) -> ToolMessage:
    return ToolMessage(
        content=_MALFORMED_TOOL_CALL_ERROR,
        tool_call_id=call_id,
        status="error",
    )


def _repair_messages(messages: list[Any]) -> list[Any] | None:
    repaired_messages: list[Any] = []
    inserted = 0
    for message in messages:
        repaired_messages.append(message)
        if not isinstance(message, AIMessage):
            continue
        repaired_ids: set[str] = set()
        for call in _tool_call_dicts(message):
            if not _repair_call(call):
                continue
            call_id = call.get("id")
            if isinstance(call_id, str) and call_id and call_id not in repaired_ids:
                repaired_messages.append(_synthetic_tool_message(call_id))
                repaired_ids.add(call_id)
                inserted += 1
    if not inserted:
        return None
    logger.warning("Repaired %d malformed tool call(s) before model call", inserted)
    return repaired_messages


class RepairMalformedToolCallsMiddleware(OpenSWEMiddleware):
    """Replace malformed tool-call arguments before model calls."""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> Any:
        repaired = _repair_messages(request.messages)
        if repaired is not None:
            request.messages[:] = repaired
        return await handler(request)
