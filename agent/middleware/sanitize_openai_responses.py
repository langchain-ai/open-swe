"""Remove non-replayable reasoning and orphaned tool outputs from OpenAI history."""

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langchain_openai import ChatOpenAI

from agent.middleware.trace import OpenSWEMiddleware


def _is_stateless_chat_openai(model: object) -> bool:
    seen: set[int] = set()
    current = model
    for _ in range(10):
        if isinstance(current, ChatOpenAI):
            return current.store is False
        current_id = id(current)
        if current_id in seen:
            return False
        seen.add(current_id)
        bound = getattr(current, "bound", None)
        if bound is None or bound is current:
            return False
        current = bound
    return False


def _sanitize_messages(messages: list[AnyMessage]) -> list[AnyMessage]:
    sanitized: list[AnyMessage] = []
    tool_call_ids: set[str] = set()
    for message in messages:
        if isinstance(message, AIMessage):
            tool_call_ids.update(
                call_id
                for tool_call in message.tool_calls
                if isinstance(call_id := tool_call.get("id"), str)
            )
            if not isinstance(message.content, list):
                sanitized.append(message)
                continue
            content = [
                block
                for block in message.content
                if not (
                    isinstance(block, dict)
                    and block.get("type") == "reasoning"
                    and isinstance(block.get("id"), str)
                    and block["id"].startswith("rs_")
                    and not block.get("encrypted_content")
                )
            ]
            sanitized.append(
                message
                if len(content) == len(message.content)
                else message.model_copy(update={"content": content})
            )
        elif not isinstance(message, ToolMessage) or message.tool_call_id in tool_call_ids:
            sanitized.append(message)
    return sanitized


class SanitizeOpenAIResponsesMiddleware(OpenSWEMiddleware):
    """Drop history items that OpenAI cannot resolve with ``store=False``."""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> Any:
        if _is_stateless_chat_openai(request.model):
            request = request.override(messages=_sanitize_messages(request.messages))
        return await handler(request)
