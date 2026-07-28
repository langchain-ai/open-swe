from __future__ import annotations

import asyncio
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse

from agent.middleware.model_call_timeout import (
    DEFAULT_MODEL_CALL_TIMEOUT_SECONDS,
    ModelCallTimeoutError,
    ModelCallTimeoutMiddleware,
)


def _request() -> ModelRequest[None]:
    return cast(ModelRequest[None], MagicMock())


class TestModelCallTimeoutMiddleware:
    @pytest.mark.asyncio
    async def test_passes_through_a_prompt_response(self) -> None:
        response = MagicMock()

        async def handler(_req: ModelRequest[None]) -> ModelResponse[Any]:
            return cast(ModelResponse[Any], response)

        result = await ModelCallTimeoutMiddleware(timeout_seconds=5).awrap_model_call(
            _request(), handler
        )

        assert result is response

    @pytest.mark.asyncio
    async def test_raises_when_the_model_call_hangs(self) -> None:
        started = asyncio.Event()

        async def handler(_req: ModelRequest[None]) -> ModelResponse[Any]:
            started.set()
            await asyncio.sleep(60)
            raise AssertionError("handler should have been cancelled")

        with pytest.raises(ModelCallTimeoutError):
            await ModelCallTimeoutMiddleware(timeout_seconds=0.01).awrap_model_call(
                _request(), handler
            )

        assert started.is_set()

    def test_timeout_reads_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPEN_SWE_MODEL_CALL_TIMEOUT_SECONDS", "120")
        assert ModelCallTimeoutMiddleware()._timeout_seconds == 120

    def test_timeout_falls_back_on_invalid_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPEN_SWE_MODEL_CALL_TIMEOUT_SECONDS", "soon")
        assert ModelCallTimeoutMiddleware()._timeout_seconds == DEFAULT_MODEL_CALL_TIMEOUT_SECONDS
