import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from agent.middleware.refresh_slack_status import (
    SlackAssistantStatusMiddleware,
    _status_from_recent_tool_calls,
)
from agent.utils.slack import DEFAULT_ASSISTANT_STATUS, DEFAULT_LOADING_MESSAGES


class TestSlackAssistantStatusMiddleware:
    def _runtime(self) -> MagicMock:
        return MagicMock()

    def _config(self) -> dict:
        return {"configurable": {"slack_thread": {"channel_id": "C1", "thread_ts": "1.0"}}}

    @pytest.mark.asyncio
    async def test_before_agent_sets_status_when_slack_thread_present(self) -> None:
        middleware = SlackAssistantStatusMiddleware()
        with (
            patch("agent.middleware.refresh_slack_status.get_config", return_value=self._config()),
            patch(
                "agent.middleware.refresh_slack_status.set_slack_assistant_status",
                new_callable=AsyncMock,
            ) as mock_set,
        ):
            result = await middleware.abefore_agent({"messages": []}, self._runtime())

        assert result is None
        mock_set.assert_awaited_once()
        args = mock_set.await_args.args
        kwargs = mock_set.await_args.kwargs
        assert args == ("C1", "1.0")
        assert kwargs["status"] == DEFAULT_ASSISTANT_STATUS
        assert kwargs["loading_messages"] == list(DEFAULT_LOADING_MESSAGES)

    @pytest.mark.asyncio
    async def test_after_agent_clears_status_when_slack_thread_present(self) -> None:
        middleware = SlackAssistantStatusMiddleware()
        with (
            patch("agent.middleware.refresh_slack_status.get_config", return_value=self._config()),
            patch(
                "agent.middleware.refresh_slack_status.set_slack_assistant_status",
                new_callable=AsyncMock,
            ) as mock_set,
        ):
            result = await middleware.aafter_agent({"messages": []}, self._runtime())

        assert result is None
        assert mock_set.await_args.kwargs["status"] == ""
        assert mock_set.await_args.kwargs["loading_messages"] is None

    @pytest.mark.asyncio
    async def test_model_call_uses_contextual_status_from_last_tool_call(self) -> None:
        middleware = SlackAssistantStatusMiddleware()
        ai = AIMessage(
            content="",
            tool_calls=[{"name": "grep", "args": {"pattern": "foo"}, "id": "tc1"}],
        )
        request = ModelRequest(
            model=MagicMock(),
            messages=[],
            state={"messages": [ai]},
            runtime=self._runtime(),
        )

        async def handler(_request: ModelRequest) -> ModelResponse:
            return ModelResponse(result=[AIMessage(content="done")])

        with (
            patch("agent.middleware.refresh_slack_status.get_config", return_value=self._config()),
            patch(
                "agent.middleware.refresh_slack_status.set_slack_assistant_status",
                new_callable=AsyncMock,
            ) as mock_set,
        ):
            response = await middleware.awrap_model_call(request, handler)

        assert response.result[0].content == "done"
        assert mock_set.await_args_list[0].kwargs["status"] == "searching the codebase..."

    @pytest.mark.asyncio
    async def test_tool_call_uses_tool_specific_status(self) -> None:
        middleware = SlackAssistantStatusMiddleware()
        request = ToolCallRequest(
            tool_call={"name": "execute", "args": {}, "id": "tc1"},
            tool=MagicMock(),
            state={},
            runtime=self._runtime(),
        )

        async def handler(_request: ToolCallRequest) -> MagicMock:
            return MagicMock()

        with (
            patch("agent.middleware.refresh_slack_status.get_config", return_value=self._config()),
            patch(
                "agent.middleware.refresh_slack_status.set_slack_assistant_status",
                new_callable=AsyncMock,
            ) as mock_set,
        ):
            await middleware.awrap_tool_call(request, handler)

        assert mock_set.await_args_list[0].kwargs["status"] == "running commands..."

    @pytest.mark.asyncio
    async def test_heartbeat_refreshes_while_call_is_running(self) -> None:
        middleware = SlackAssistantStatusMiddleware(
            heartbeat_interval_seconds=0.01,
            max_heartbeat_seconds=10,
        )
        refreshed = asyncio.Event()
        set_count = 0
        real_sleep = asyncio.sleep

        async def fake_set_status(*_args: object, **_kwargs: object) -> bool:
            nonlocal set_count
            set_count += 1
            if set_count >= 2:
                refreshed.set()
            return True

        async def fake_sleep(_delay: float) -> None:
            await real_sleep(0)

        request = ModelRequest(
            model=MagicMock(),
            messages=[],
            state={"messages": []},
            runtime=self._runtime(),
        )

        async def handler(_request: ModelRequest) -> ModelResponse:
            await refreshed.wait()
            return ModelResponse(result=[AIMessage(content="done")])

        with (
            patch("agent.middleware.refresh_slack_status.get_config", return_value=self._config()),
            patch(
                "agent.middleware.refresh_slack_status.set_slack_assistant_status",
                side_effect=fake_set_status,
            ),
            patch("agent.middleware.refresh_slack_status.asyncio.sleep", side_effect=fake_sleep),
        ):
            response = await middleware.awrap_model_call(request, handler)

        assert response.result[0].content == "done"
        assert set_count >= 2

    @pytest.mark.asyncio
    async def test_skips_when_slack_thread_missing(self) -> None:
        middleware = SlackAssistantStatusMiddleware()
        with (
            patch(
                "agent.middleware.refresh_slack_status.get_config",
                return_value={"configurable": {}},
            ),
            patch(
                "agent.middleware.refresh_slack_status.set_slack_assistant_status",
                new_callable=AsyncMock,
            ) as mock_set,
        ):
            result = await middleware.abefore_agent({"messages": []}, self._runtime())

        assert result is None
        mock_set.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_channel_or_thread_blank(self) -> None:
        middleware = SlackAssistantStatusMiddleware()
        with (
            patch(
                "agent.middleware.refresh_slack_status.get_config",
                return_value={
                    "configurable": {"slack_thread": {"channel_id": "", "thread_ts": "1.0"}}
                },
            ),
            patch(
                "agent.middleware.refresh_slack_status.set_slack_assistant_status",
                new_callable=AsyncMock,
            ) as mock_set,
        ):
            result = await middleware.abefore_agent({"messages": []}, self._runtime())

        assert result is None
        mock_set.assert_not_called()

    def test_status_helper_falls_back_when_no_tool_calls(self) -> None:
        assert _status_from_recent_tool_calls([]) == DEFAULT_ASSISTANT_STATUS
        assert _status_from_recent_tool_calls([AIMessage(content="hi")]) == DEFAULT_ASSISTANT_STATUS

    def test_status_helper_falls_back_for_unknown_tool(self) -> None:
        ai = AIMessage(
            content="",
            tool_calls=[{"name": "mystery_tool", "args": {}, "id": "tc1"}],
        )
        assert _status_from_recent_tool_calls([ai]) == DEFAULT_ASSISTANT_STATUS

    @pytest.mark.asyncio
    async def test_swallows_config_exceptions(self) -> None:
        middleware = SlackAssistantStatusMiddleware()
        with (
            patch(
                "agent.middleware.refresh_slack_status.get_config",
                side_effect=RuntimeError("boom"),
            ),
            patch(
                "agent.middleware.refresh_slack_status.set_slack_assistant_status",
                new_callable=AsyncMock,
            ) as mock_set,
        ):
            result = await middleware.abefore_agent({"messages": []}, self._runtime())

        assert result is None
        mock_set.assert_not_called()
