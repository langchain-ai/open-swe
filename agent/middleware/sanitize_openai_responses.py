"""Remove non-replayable reasoning items from stateless OpenAI history."""

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, AnyMessage
from langchain_openai import ChatOpenAI

from ..models.factory import unwrap_chat_model


def _is_stateless_chat_openai(model: object) -> bool:
    chat_model = unwrap_chat_model(model, ChatOpenAI)
    return chat_model is not None and chat_model.store is False


def _sanitize_messages(messages: list[AnyMessage]) -> list[AnyMessage]:
    sanitized: list[AnyMessage] = []
    for message in messages:
        if not isinstance(message, AIMessage) or not isinstance(message.content, list):
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
    return sanitized


class SanitizeOpenAIResponsesMiddleware(AgentMiddleware):
    """Drop reasoning item IDs that OpenAI cannot resolve with ``store=False``."""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> Any:
        if _is_stateless_chat_openai(request.model):
            request = request.override(messages=_sanitize_messages(request.messages))
        return await handler(request)
