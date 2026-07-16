from __future__ import annotations

from typing import cast

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage

from agent.middleware.timeout_wrapup import TimeoutWrapupMiddleware


@pytest.mark.asyncio
async def test_timeout_wrapup_starts_clock_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    times = [100.0, 105.0, 111.0]

    def monotonic() -> float:
        return times.pop(0) if times else 111.0

    monkeypatch.setattr("agent.middleware.timeout_wrapup.time.monotonic", monotonic)
    middleware = TimeoutWrapupMiddleware(timeout_seconds=10)
    seen: list[ModelRequest] = []

    async def handler(request: ModelRequest) -> ModelResponse:
        seen.append(request)
        return ModelResponse(result=[AIMessage(content="ok")])

    request = ModelRequest(
        model=cast(BaseChatModel, object()),
        messages=[],
        system_message=SystemMessage(content="base"),
    )

    await middleware.awrap_model_call(request, handler)
    await middleware.awrap_model_call(request, handler)

    assert seen[0].system_message is not None
    assert seen[1].system_message is not None
    assert seen[0].system_message.content == "base"
    assert isinstance(seen[1].system_message.content, str)
    assert "time_limit_warning" in seen[1].system_message.content


@pytest.mark.asyncio
async def test_timeout_wrapup_preserves_structured_system_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agent.middleware.timeout_wrapup.time.monotonic", lambda: 100.0)
    middleware = TimeoutWrapupMiddleware(timeout_seconds=1)
    middleware._start = 99.0
    seen: list[ModelRequest] = []

    async def handler(request: ModelRequest) -> ModelResponse:
        seen.append(request)
        return ModelResponse(result=[AIMessage(content="ok")])

    request = ModelRequest(
        model=cast(BaseChatModel, object()),
        messages=[],
        system_message=SystemMessage(
            content=[{"type": "text", "text": "base", "cache_control": {"type": "ephemeral"}}]
        ),
    )

    await middleware.awrap_model_call(request, handler)

    assert seen[0].system_message is not None
    content = seen[0].system_message.content
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "base", "cache_control": {"type": "ephemeral"}}
    warning_block = content[1]
    assert isinstance(warning_block, dict)
    assert warning_block["type"] == "text"
    assert "time_limit_warning" in warning_block["text"]
