"""Tests for ModelFallbackMiddleware."""

from collections.abc import Awaitable, Callable
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import openai
import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, wrap_model_call
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver

from openswe.middleware.model_fallback import (
    MODEL_OUTAGE_MESSAGE,
    ModelFallbackMiddleware,
    ModelOutageError,
    is_model_outage,
)
from openswe.middleware.require_cli_result import RequireCliResultMiddleware
from openswe.middleware.require_user_reply import SLACK_REPLY_SURFACE, RequireUserReplyMiddleware


def _openai_5xx() -> openai.APIStatusError:
    request = httpx2.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx2.Response(503, request=request, json={"error": {"message": "unavailable"}})
    return openai.APIStatusError("unavailable", response=response, body=response.json())


def _make_request() -> ModelRequest[None]:
    request = MagicMock()
    request.override = MagicMock(return_value=MagicMock(name="overridden_request"))
    return cast(ModelRequest[None], request)


class TestModelFallbackMiddleware:
    @pytest.mark.parametrize("default_fallback_enabled", [True, False])
    @pytest.mark.parametrize("requested_fallback_enabled", [True, False])
    async def test_handoff_fallback_does_not_change_other_model_calls(
        self, default_fallback_enabled: bool, requested_fallback_enabled: bool
    ) -> None:
        requested = FakeListChatModel(responses=["requested"])
        requested_fallback = FakeListChatModel(responses=["requested fallback"])
        other = FakeListChatModel(responses=["other"])
        default_fallback = FakeListChatModel(responses=["default fallback"])
        middleware = ModelFallbackMiddleware(
            default_fallback if default_fallback_enabled else None, backoff_schedule=(0.0,)
        )
        middleware.register_fallback(
            requested, requested_fallback if requested_fallback_enabled else None
        )

        async def handler(request: ModelRequest[None]) -> ModelResponse[None]:
            if request.model is requested or request.model is other:
                raise TimeoutError("Provider unavailable")
            message = await request.model.ainvoke(request.messages)
            return ModelResponse(result=[message])

        if requested_fallback_enabled:
            result = await middleware.awrap_model_call(
                ModelRequest(model=requested, messages=[]), handler
            )
            assert result.result[0].content == "requested fallback"
        else:
            with pytest.raises(TimeoutError, match="Provider unavailable"):
                await middleware.awrap_model_call(
                    ModelRequest(model=requested, messages=[]), handler
                )
        if default_fallback_enabled:
            result = await middleware.awrap_model_call(
                ModelRequest(model=other, messages=[]), handler
            )
            assert result.result[0].content == "default fallback"
        else:
            with pytest.raises(TimeoutError, match="Provider unavailable"):
                await middleware.awrap_model_call(ModelRequest(model=other, messages=[]), handler)

    @pytest.mark.asyncio
    async def test_retry_spans_cover_backoff_and_attempt_without_error_body(self) -> None:
        primary = MagicMock(model_name="primary")
        fallback = MagicMock(model_name="fallback")
        request = _make_request()
        request.model = primary
        request.override = MagicMock(return_value=MagicMock(model=fallback))
        response = ModelResponse(result=[AIMessage(content="done")])
        handler = AsyncMock(side_effect=[TimeoutError("secret"), _openai_5xx(), response])
        spans: list[MagicMock] = []

        def make_span(*args: object, **kwargs: object) -> MagicMock:
            span = MagicMock()
            span.__aenter__ = AsyncMock(return_value=span)
            span.__aexit__ = AsyncMock(return_value=False)
            spans.append(span)
            return span

        with (
            patch("openswe.middleware.model_fallback.trace", side_effect=make_span) as traced,
            patch(
                "openswe.middleware.model_fallback.asyncio.sleep", new_callable=AsyncMock
            ) as sleep,
            patch("openswe.middleware.model_fallback.random.uniform", return_value=0),
        ):
            result = await ModelFallbackMiddleware(
                fallback, backoff_schedule=(0.0, 5.0)
            ).awrap_model_call(request, handler)

        assert result is response
        assert traced.call_count == 2
        first, second = [call.kwargs["metadata"] for call in traced.call_args_list]
        assert first["failed_model"] == "primary"
        assert first["next_model"] == "fallback"
        assert first["error_type"] == "TimeoutError"
        assert first["attempt"] == 2
        assert second["next_model"] == "primary"
        assert second["status_code"] == 503
        assert second["backoff_seconds"] == 5.0
        assert "secret" not in str(traced.call_args_list)
        spans[0].end.assert_called_once_with(error="APIStatusError")
        spans[1].end.assert_called_once_with(outputs={"outcome": "success"})
        sleep.assert_awaited_once_with(5.0)

    @pytest.mark.asyncio
    async def test_async_falls_over_on_openai_streaming_overload(self) -> None:
        exc = openai.APIError(
            "Our servers are currently overloaded. Please try again later.",
            request=httpx2.Request("POST", "https://api.openai.com/v1/responses"),
            body=None,
        )
        assert not hasattr(exc, "status_code")
        fallback = MagicMock(name="fallback")
        request = _make_request()
        response = ModelResponse(result=[AIMessage(content="ok from fallback")])
        handler = AsyncMock(side_effect=[exc, response])

        result = await ModelFallbackMiddleware(fallback, backoff_schedule=(0.0,)).awrap_model_call(
            request, handler
        )

        assert result is response
        override = cast(MagicMock, request.override)
        override.assert_called_once_with(model=fallback)
        assert handler.await_count == 2
        handler.assert_awaited_with(override.return_value)

    @pytest.mark.asyncio
    async def test_async_falls_over_on_httpx2_stream_transport_error(self) -> None:
        fallback_model = MagicMock(name="fallback_model")
        middleware = ModelFallbackMiddleware(fallback_model)

        calls: list[object] = []
        good_response = MagicMock(result=[AIMessage(content="ok from fallback")])

        async def handler(req: ModelRequest[None]) -> ModelResponse[Any]:
            calls.append(req)
            if len(calls) == 1:
                raise httpx2.RemoteProtocolError(
                    "peer closed connection without sending complete message body "
                    "(incomplete chunked read)"
                )
            return cast(ModelResponse[Any], good_response)

        request = _make_request()
        result = await middleware.awrap_model_call(request, handler)

        assert result is good_response
        assert len(calls) == 2
        override = cast(MagicMock, request.override)
        override.assert_called_once_with(model=fallback_model)
        assert calls[1] is override.return_value

    @pytest.mark.asyncio
    async def test_async_propagates_non_transient_error(self) -> None:
        middleware = ModelFallbackMiddleware(MagicMock())
        calls: list[object] = []

        async def handler(req: ModelRequest[None]) -> ModelResponse[Any]:
            calls.append(req)
            raise ValueError("not transient")

        with pytest.raises(ValueError, match="not transient"):
            await middleware.awrap_model_call(_make_request(), handler)

        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_async_retries_primary_after_fallback_failure(self) -> None:
        """If the fallback also fails transiently, retry the primary instead of crashing."""
        fallback_model = MagicMock(name="fallback_model")
        middleware = ModelFallbackMiddleware(fallback_model, backoff_schedule=(0.0, 0.0, 0.0))
        calls: list[object] = []
        good_response = MagicMock(result=[AIMessage(content="ok from primary retry")])

        async def handler(req: ModelRequest[None]) -> ModelResponse[Any]:
            calls.append(req)
            if len(calls) <= 2:  # primary fails, then fallback fails
                raise _openai_5xx()
            return cast(ModelResponse[Any], good_response)

        request = _make_request()
        result = await middleware.awrap_model_call(request, handler)

        assert result is good_response
        assert len(calls) == 3
        assert not is_model_outage(good_response.result[0])
        # Attempts alternate primary -> fallback -> primary.
        assert calls[0] is request
        assert calls[1] is cast(MagicMock, request.override).return_value
        assert calls[2] is request

    @pytest.mark.asyncio
    async def test_async_exhaustion_returns_outage_message(self) -> None:
        """After exhausting all attempts, the run ends with a visible message, not a crash."""
        middleware = ModelFallbackMiddleware(MagicMock(), backoff_schedule=(0.0, 0.0))
        calls: list[object] = []

        async def handler(req: ModelRequest[None]) -> ModelResponse[Any]:
            calls.append(req)
            raise _openai_5xx()

        result = await middleware.awrap_model_call(_make_request(), handler)

        assert len(calls) == 3
        assert isinstance(result, AIMessage)
        assert "retrigger" in result.text
        assert is_model_outage(result)
        assert not is_model_outage(AIMessage(content=result.content))

    @pytest.mark.asyncio
    async def test_async_exhaustion_raises_when_message_disabled(self) -> None:
        middleware = ModelFallbackMiddleware(
            MagicMock(), backoff_schedule=(0.0,), surface_outage_message=False
        )
        calls: list[object] = []

        async def handler(req: ModelRequest[None]) -> ModelResponse[Any]:
            calls.append(req)
            raise _openai_5xx()

        with pytest.raises(openai.APIStatusError):
            await middleware.awrap_model_call(_make_request(), handler)

        assert len(calls) == 2


@pytest.mark.parametrize(
    ("source", "recovers"),
    [("slack", False), ("cli", False), ("schedule", False), ("schedule", True)],
)
async def test_terminal_outage_ends_without_checkpointing_reply_nudges(
    monkeypatch: pytest.MonkeyPatch, source: str, recovers: bool
) -> None:
    from openswe.slack.tools import reply

    posted = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(reply, "slack_reply", posted)
    calls = 0

    @wrap_model_call
    async def unavailable(
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        nonlocal calls
        calls += 1
        if recovers and calls == 2:
            return ModelResponse(result=[AIMessage(content="Assessment completed")])
        raise _openai_5xx()

    middleware: list[AgentMiddleware] = [
        ModelFallbackMiddleware(FakeListChatModel(responses=["unused"]), backoff_schedule=(0.0,)),
        unavailable,
    ]
    if source == "slack":
        middleware.append(
            RequireUserReplyMiddleware(
                "slack_reply", "slack_no_reply_needed", initial_surface=SLACK_REPLY_SURFACE
            )
        )
    elif source == "cli":
        middleware.append(RequireCliResultMiddleware("cli_result"))
    graph = create_agent(
        model=FakeListChatModel(responses=["unused"]),
        middleware=middleware,
        checkpointer=InMemorySaver(),
    )
    config: RunnableConfig = {"configurable": {"thread_id": source, "source": source}}
    inputs = {"messages": [HumanMessage(content="Assess this merged PR")]}
    if source == "schedule" and not recovers:
        with pytest.raises(ModelOutageError):
            await graph.ainvoke(inputs, config)
    else:
        results = [
            event["data"]["input"]
            async for event in graph.astream_events(inputs, config)
            if event["event"] == "on_tool_start" and event["name"] == "cli_result"
        ]
        if source == "cli":
            assert results == [{"stdout": MODEL_OUTAGE_MESSAGE, "exit_code": 1}]
        elif source == "slack":
            posted.assert_awaited_once()
            assert posted.await_args is not None
            assert posted.await_args.args[:2] == (MODEL_OUTAGE_MESSAGE, "final")
    checkpoint = await graph.aget_state(config)
    assert len(checkpoint.values["messages"]) == 2
    assert is_model_outage(checkpoint.values["messages"][-1]) is not recovers
    assert checkpoint.values.get("reply_nudges", 0) == 0
    assert checkpoint.values.get("cli_result_nudges", 0) == 0
    assert calls == 2
