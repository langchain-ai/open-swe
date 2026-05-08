from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from agent.middleware.refresh_slack_status import (
    _status_from_recent_tool_calls,
    refresh_slack_assistant_status_before_model,
)
from agent.utils.slack import DEFAULT_ASSISTANT_STATUS, DEFAULT_LOADING_MESSAGES


class TestRefreshSlackAssistantStatus:
    def _runtime(self) -> MagicMock:
        return MagicMock()

    @pytest.mark.asyncio
    async def test_calls_set_status_when_slack_thread_present(self) -> None:
        with (
            patch(
                "agent.middleware.refresh_slack_status.get_config",
                return_value={
                    "configurable": {"slack_thread": {"channel_id": "C1", "thread_ts": "1.0"}}
                },
            ),
            patch(
                "agent.middleware.refresh_slack_status.set_slack_assistant_status",
                new_callable=AsyncMock,
            ) as mock_set,
        ):
            result = await refresh_slack_assistant_status_before_model.abefore_model(
                {"messages": []}, self._runtime()
            )

        assert result is None
        mock_set.assert_awaited_once()
        kwargs = mock_set.await_args.kwargs
        args = mock_set.await_args.args
        assert args == ("C1", "1.0")
        assert kwargs["status"] == DEFAULT_ASSISTANT_STATUS
        assert kwargs["loading_messages"] == list(DEFAULT_LOADING_MESSAGES)

    @pytest.mark.asyncio
    async def test_uses_contextual_status_from_last_tool_call(self) -> None:
        ai = AIMessage(
            content="",
            tool_calls=[{"name": "grep", "args": {"pattern": "foo"}, "id": "tc1"}],
        )
        with (
            patch(
                "agent.middleware.refresh_slack_status.get_config",
                return_value={
                    "configurable": {"slack_thread": {"channel_id": "C1", "thread_ts": "1.0"}}
                },
            ),
            patch(
                "agent.middleware.refresh_slack_status.set_slack_assistant_status",
                new_callable=AsyncMock,
            ) as mock_set,
        ):
            await refresh_slack_assistant_status_before_model.abefore_model(
                {"messages": [ai]}, self._runtime()
            )

        assert mock_set.await_args.kwargs["status"] == "searching the codebase…"

    @pytest.mark.asyncio
    async def test_skips_when_slack_thread_missing(self) -> None:
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
            result = await refresh_slack_assistant_status_before_model.abefore_model(
                {"messages": []}, self._runtime()
            )

        assert result is None
        mock_set.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_channel_or_thread_blank(self) -> None:
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
            result = await refresh_slack_assistant_status_before_model.abefore_model(
                {"messages": []}, self._runtime()
            )

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
    async def test_swallows_exceptions(self) -> None:
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
            result = await refresh_slack_assistant_status_before_model.abefore_model(
                {"messages": []}, self._runtime()
            )

        assert result is None
        mock_set.assert_not_called()
