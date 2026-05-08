"""Unit tests for the sandbox-circuit-breaker middleware and the
``SandboxClientError`` recovery branch in ``ToolErrorMiddleware``.

Covers the scenario described in issue 7a78d721:

* Mid-run ``SandboxClientError`` -> ``_recreate_sandbox`` is invoked.
* Repeated failures against the same ``sb-<id>`` trip the circuit breaker.
* The circuit breaker emits exactly one user-facing notification.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from langsmith.sandbox import SandboxClientError

from agent.middleware.sandbox_circuit_breaker import (
    SANDBOX_CIRCUIT_BREAKER_THRESHOLD,
    sandbox_circuit_breaker,
)
from agent.middleware.tool_error_handler import ToolErrorMiddleware


def _make_request(thread_id: str | None = "thr-1", tool_call_id: str = "call-1"):
    """Build a minimal ToolCallRequest-like object for the middleware."""
    request = MagicMock()
    request.tool_call = {"id": tool_call_id, "name": "execute"}
    request.tool_name = "execute"
    request.config = {"configurable": {"thread_id": thread_id}} if thread_id else {}
    return request


def _sandbox_error_tool_message(sandbox_id: str, tool_call_id: str = "call-1") -> ToolMessage:
    payload = {
        "status": "error",
        "error_type": "SandboxClientError",
        "error": f"Sandbox request timed out: {sandbox_id}",
    }
    return ToolMessage(content=json.dumps(payload), tool_call_id=tool_call_id, status="error")


class TestSandboxClientErrorRecovery:
    @pytest.mark.asyncio
    async def test_sandbox_client_error_triggers_recreate(self) -> None:
        """awrap_tool_call should call _recreate_sandbox on SandboxClientError."""
        middleware = ToolErrorMiddleware()
        request = _make_request()

        async def failing_handler(_req):
            raise SandboxClientError("Sandbox request timed out: sb-abc12345")

        new_backend = MagicMock()
        new_backend.id = "deadbeef"

        with (
            patch(
                "agent.server._recreate_sandbox",
                new_callable=AsyncMock,
                return_value=new_backend,
            ) as mock_recreate,
            patch("agent.utils.sandbox_state.SANDBOX_BACKENDS", {}) as backends,
        ):
            result = await middleware.awrap_tool_call(request, failing_handler)

        mock_recreate.assert_awaited_once_with("thr-1")
        assert backends["thr-1"] is new_backend
        assert isinstance(result, ToolMessage)
        assert result.status == "error"
        body = json.loads(result.content)
        assert body["error_type"] == "SandboxClientError"
        assert body["sandbox_recreated"] == "true"
        assert body["new_sandbox_id"] == "deadbeef"
        assert "Retry" in body["error"]

    @pytest.mark.asyncio
    async def test_recreate_failure_falls_through_to_generic_error(self) -> None:
        middleware = ToolErrorMiddleware()
        request = _make_request()

        async def failing_handler(_req):
            raise SandboxClientError("Sandbox request timed out: sb-abc12345")

        with patch(
            "agent.server._recreate_sandbox",
            new_callable=AsyncMock,
            side_effect=RuntimeError("recreation failed"),
        ):
            result = await middleware.awrap_tool_call(request, failing_handler)

        assert isinstance(result, ToolMessage)
        body = json.loads(result.content)
        assert body["status"] == "error"
        assert body["error_type"] == "SandboxClientError"
        # Falls through to generic payload — no "sandbox_recreated" marker.
        assert "sandbox_recreated" not in body

    @pytest.mark.asyncio
    async def test_missing_thread_id_falls_through(self) -> None:
        middleware = ToolErrorMiddleware()
        request = _make_request(thread_id=None)

        async def failing_handler(_req):
            raise SandboxClientError("Sandbox request timed out: sb-abc12345")

        with patch(
            "agent.server._recreate_sandbox", new_callable=AsyncMock
        ) as mock_recreate:
            result = await middleware.awrap_tool_call(request, failing_handler)

        mock_recreate.assert_not_called()
        assert isinstance(result, ToolMessage)
        body = json.loads(result.content)
        assert "sandbox_recreated" not in body


class TestSandboxCircuitBreaker:
    def _make_runtime(self) -> MagicMock:
        return MagicMock()

    @pytest.mark.asyncio
    async def test_no_trip_when_streak_at_or_below_threshold(self) -> None:
        # Exactly threshold => no trip.
        messages: list = [
            HumanMessage(content="do the thing"),
            *[
                _sandbox_error_tool_message("sb-aaa11111", f"c{i}")
                for i in range(SANDBOX_CIRCUIT_BREAKER_THRESHOLD)
            ],
        ]
        state = {"messages": messages}

        with patch(
            "agent.middleware.sandbox_circuit_breaker.post_slack_thread_reply",
            new_callable=AsyncMock,
        ) as mock_post:
            result = await sandbox_circuit_breaker.aafter_agent(state, self._make_runtime())

        assert result is None
        mock_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_trips_once_when_streak_exceeds_threshold(self) -> None:
        n = SANDBOX_CIRCUIT_BREAKER_THRESHOLD + 1
        messages: list = [
            HumanMessage(content="do the thing"),
            *[_sandbox_error_tool_message("sb-aaa11111", f"c{i}") for i in range(n)],
        ]
        state = {"messages": messages}

        with (
            patch(
                "agent.middleware.sandbox_circuit_breaker.get_config",
                return_value={
                    "configurable": {
                        "slack_thread": {"channel_id": "C1", "thread_ts": "171.99"}
                    }
                },
            ),
            patch(
                "agent.middleware.sandbox_circuit_breaker.post_slack_thread_reply",
                new_callable=AsyncMock,
            ) as mock_post,
        ):
            result = await sandbox_circuit_breaker.aafter_agent(state, self._make_runtime())

        assert result is None
        mock_post.assert_awaited_once()
        args = mock_post.await_args.args
        assert args[0:2] == ("C1", "171.99")
        assert "unrecoverable" in args[2].lower()

    @pytest.mark.asyncio
    async def test_streak_breaks_on_different_sandbox_id(self) -> None:
        # 5 consecutive failures, but two different sb-<id>s — streak only counts
        # the tail group.
        messages: list = [
            _sandbox_error_tool_message("sb-old00000", "c1"),
            _sandbox_error_tool_message("sb-old00000", "c2"),
            _sandbox_error_tool_message("sb-new99999", "c3"),
        ]
        state = {"messages": messages}

        with patch(
            "agent.middleware.sandbox_circuit_breaker.post_slack_thread_reply",
            new_callable=AsyncMock,
        ) as mock_post:
            result = await sandbox_circuit_breaker.aafter_agent(state, self._make_runtime())

        assert result is None
        mock_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_only_one_notification_when_called_repeatedly(self) -> None:
        """Even if invoked multiple times after_agent, each invocation that
        observes a tripping streak must post exactly one notification — the
        breaker is idempotent per call, not stateful across calls."""
        n = SANDBOX_CIRCUIT_BREAKER_THRESHOLD + 1
        messages: list = [
            *[_sandbox_error_tool_message("sb-aaa11111", f"c{i}") for i in range(n)],
        ]
        state = {"messages": messages}

        with (
            patch(
                "agent.middleware.sandbox_circuit_breaker.get_config",
                return_value={
                    "configurable": {
                        "slack_thread": {"channel_id": "C1", "thread_ts": "171.99"}
                    }
                },
            ),
            patch(
                "agent.middleware.sandbox_circuit_breaker.post_slack_thread_reply",
                new_callable=AsyncMock,
            ) as mock_post,
        ):
            await sandbox_circuit_breaker.aafter_agent(state, self._make_runtime())

        # Exactly one notification for one tripping invocation.
        assert mock_post.await_count == 1

    @pytest.mark.asyncio
    async def test_no_notification_when_no_channel_configured(self) -> None:
        n = SANDBOX_CIRCUIT_BREAKER_THRESHOLD + 1
        messages: list = [
            *[_sandbox_error_tool_message("sb-aaa11111", f"c{i}") for i in range(n)],
        ]
        state = {"messages": messages}

        with (
            patch(
                "agent.middleware.sandbox_circuit_breaker.get_config",
                return_value={"configurable": {}},
            ),
            patch(
                "agent.middleware.sandbox_circuit_breaker.post_slack_thread_reply",
                new_callable=AsyncMock,
            ) as mock_post,
        ):
            result = await sandbox_circuit_breaker.aafter_agent(state, self._make_runtime())

        assert result is None
        mock_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_successful_tool_message_breaks_streak(self) -> None:
        messages: list = [
            _sandbox_error_tool_message("sb-aaa11111", "c1"),
            _sandbox_error_tool_message("sb-aaa11111", "c2"),
            _sandbox_error_tool_message("sb-aaa11111", "c3"),
            ToolMessage(
                content=json.dumps({"status": "success", "stdout": "ok"}),
                tool_call_id="c4",
            ),
        ]
        state = {"messages": messages}

        with patch(
            "agent.middleware.sandbox_circuit_breaker.post_slack_thread_reply",
            new_callable=AsyncMock,
        ) as mock_post:
            result = await sandbox_circuit_breaker.aafter_agent(state, self._make_runtime())

        assert result is None
        mock_post.assert_not_called()
