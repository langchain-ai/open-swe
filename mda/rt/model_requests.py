"""Fixes to the messages each model call sends, applied to the model about to be called.

These run here because the model call does: the backend's model-call phase returns
only the system prompt and tools, so a message edit made there never reaches the model.
"""

from collections.abc import Awaitable, Callable, Iterator
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langchain_fireworks import ChatFireworks
from langchain_openai import ChatOpenAI

_Handler = Callable[[ModelRequest], Awaitable[ModelResponse]]
# Deep enough for any binding of tools, retries and fallbacks around a chat model.
_MAX_BINDINGS = 10


def _bindings(model: object) -> Iterator[object]:
    """``model`` and each model it wraps through ``bound``."""
    current = model
    for _ in range(_MAX_BINDINGS):
        yield current
        bound = getattr(current, "bound", None)
        if bound is None or bound is current:
            return
        current = bound


def _calls[ChatT](model: object, chat: type[ChatT]) -> ChatT | None:
    return next((m for m in _bindings(model) if isinstance(m, chat)), None)


def _without_legacy_function_call(message: AnyMessage) -> AnyMessage:
    if not isinstance(message, AIMessage) or "function_call" not in message.additional_kwargs:
        return message
    kwargs = {k: v for k, v in message.additional_kwargs.items() if k != "function_call"}
    return message.model_copy(update={"additional_kwargs": kwargs})


def _without_blocks(message: AnyMessage, drop: Callable[[dict[str, Any]], bool]) -> AnyMessage:
    if not isinstance(message, AIMessage) or not isinstance(message.content, list):
        return message
    content = [b for b in message.content if not (isinstance(b, dict) and drop(b))]
    if len(content) == len(message.content):
        return message
    return message.model_copy(update={"content": content})


def _unreplayable_reasoning(block: dict[str, Any]) -> bool:
    item_id = block.get("id")
    return (
        block.get("type") == "reasoning"
        and isinstance(item_id, str)
        and item_id.startswith("rs_")
        and not block.get("encrypted_content")
    )


def _empty_thinking(block: dict[str, Any]) -> bool:
    return block.get("type") == "thinking" and not block.get("thinking")


def _edited(request: ModelRequest, messages: list[AnyMessage]) -> ModelRequest:
    if all(new is old for new, old in zip(messages, request.messages, strict=True)):
        return request
    return request.override(messages=messages)


class SanitizeProviderMessagesMiddleware(AgentMiddleware):
    """Drop what the called provider rejects from messages another provider wrote.

    - Fireworks through the LLM Gateway: the legacy OpenAI ``function_call`` field,
      which ``langchain_fireworks`` forwards and the gateway's schema forbids.
    - OpenAI with ``store=False``: reasoning item ids with no encrypted content,
      which OpenAI cannot resolve.
    - Anthropic: empty thinking blocks, which fail its validation.
    """

    async def awrap_model_call(self, request: ModelRequest, handler: _Handler) -> ModelResponse:
        messages = request.messages
        if _calls(request.model, ChatFireworks) is not None:
            messages = [_without_legacy_function_call(m) for m in messages]
        openai = _calls(request.model, ChatOpenAI)
        if openai is not None and openai.store is False:
            messages = [_without_blocks(m, _unreplayable_reasoning) for m in messages]
        if _calls(request.model, ChatAnthropic) is not None:
            messages = [_without_blocks(m, _empty_thinking) for m in messages]
        return await handler(_edited(request, messages))


def _in_call_order(messages: list[AnyMessage]) -> list[AnyMessage]:
    ordered: list[AnyMessage] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        ordered.append(message)
        index += 1
        if not isinstance(message, AIMessage) or len(message.tool_calls) < 2:
            continue
        positions = {call["id"]: n for n, call in enumerate(message.tool_calls) if call["id"]}
        start = index
        while index < len(messages) and isinstance(messages[index], ToolMessage):
            index += 1
        ordered.extend(
            sorted(
                messages[start:index],
                key=lambda result: positions.get(
                    getattr(result, "tool_call_id", ""), len(positions)
                ),
            )
        )
    return ordered


class StableToolResultOrderMiddleware(AgentMiddleware):
    """Order each parallel batch's tool results as their tool calls are ordered.

    Results land in state in completion order, which a later run does not read
    back the same way. Provider prompt caches match a byte prefix, so one swapped
    pair re-prefills every token after it on the next run's first call.
    """

    async def awrap_model_call(self, request: ModelRequest, handler: _Handler) -> ModelResponse:
        return await handler(_edited(request, _in_call_order(request.messages)))
