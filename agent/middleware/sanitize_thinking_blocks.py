"""Middleware that strips malformed Anthropic thinking blocks from prior AI messages.

Anthropic's API rejects assistant messages containing a ``thinking`` content
block with a ``signature`` but a missing or empty ``thinking`` text field
(``messages.N.content.0.thinking.thinking: Field required``). This can happen
when langgraph state serialization preserves the signature but drops the
thinking body. Replaying the persisted history on the next turn raises a
non-retryable 400 BadRequestError that bricks the whole Slack thread. We strip
such malformed blocks before every model call to keep the thread recoverable.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage


def _is_anthropic(model: Any) -> bool:
    return type(model).__name__ == "ChatAnthropic"


def _sanitize_content(content: Any) -> Any:
    if not isinstance(content, list):
        return content
    cleaned = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "thinking":
            if not block.get("thinking"):
                continue
        cleaned.append(block)
    return cleaned


def _sanitize_messages(request: ModelRequest) -> None:
    if not _is_anthropic(request.model):
        return
    for msg in request.messages:
        if isinstance(msg, AIMessage):
            msg.content = _sanitize_content(msg.content)


class SanitizeThinkingBlocksMiddleware(AgentMiddleware):
    """Drop AI message thinking blocks with empty bodies before the Anthropic call."""

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        _sanitize_messages(request)
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> Any:
        _sanitize_messages(request)
        return await handler(request)
