"""Strip leaked structured-output reasoning envelopes from final text.

Some finalize turns emit the raw structured-output reasoning envelope
(``{"reasoning": "...", "type": "reasoning"}``) prepended to the intended
markdown instead of consuming it as structured output. Left unsanitized, the
developer sees malformed JSON before the actual answer. This module strips a
leading reasoning object from model output and from the outbound Slack reply so
only the human-facing markdown survives.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage


def _is_reasoning_envelope(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    return obj.get("type") == "reasoning" or "reasoning" in obj


def strip_reasoning_envelope(text: str) -> str:
    """Drop a leading ``{"...","type":"reasoning"}`` object, keeping the markdown."""
    if not isinstance(text, str):
        return text
    stripped = text.lstrip()
    if not stripped.startswith("{"):
        return text
    try:
        obj, end = json.JSONDecoder().raw_decode(stripped)
    except ValueError:
        return text
    if not _is_reasoning_envelope(obj):
        return text
    return stripped[end:].lstrip()


def _sanitize_messages(messages: list[Any]) -> None:
    for message in messages:
        if not isinstance(message, AIMessage) or not isinstance(message.content, str):
            continue
        cleaned = strip_reasoning_envelope(message.content)
        if cleaned != message.content:
            message.content = cleaned


class SanitizeReasoningEnvelopeMiddleware(AgentMiddleware):
    """Strip leaked reasoning envelopes from model output before it becomes the answer."""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> Any:
        response = await handler(request)
        result = getattr(response, "result", None)
        if isinstance(result, list):
            _sanitize_messages(result)
        return response
