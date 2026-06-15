from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.middleware.notify_incomplete_exit import notify_incomplete_exit


class TestNotifyIncompleteExit:
    def _make_runtime(self) -> MagicMock:
        return MagicMock()

    @pytest.mark.asyncio
    async def test_posts_slack_reply_when_last_message_is_tool(self) -> None:
        state = {
            "messages": [
                HumanMessage(content="open a PR"),
                AIMessage(content="opening PR", tool_calls=[]),
                ToolMessage(content="PR opened", tool_call_id="abc", name="open_pull_request"),
            ]
        }

        with (
            patch(
                "agent.middleware.notify_incomplete_exit.get_config",
                return_value={
                    "configurable": {"slack_thread": {"channel_id": "C123", "thread_ts": "171.123"}}
                },
            ),
            patch(
                "agent.middleware.notify_incomplete_exit.post_slack_thread_reply",
                new_callable=AsyncMock,
            ) as mock_post,
        ):
            result = await notify_incomplete_exit.aafter_agent(state, self._make_runtime())

        assert result is None
        mock_post.assert_awaited_once()
        assert mock_post.await_args.args[0:2] == ("C123", "171.123")
        assert "interrupted" in mock_post.await_args.args[2]

    @pytest.mark.asyncio
    async def test_skips_when_slack_thread_reply_was_already_sent(self) -> None:
        state = {
            "messages": [
                HumanMessage(content="open a PR"),
                AIMessage(content="opening PR"),
                ToolMessage(content="ok", tool_call_id="t1", name="slack_thread_reply"),
                ToolMessage(content="PR opened", tool_call_id="t2", name="open_pull_request"),
            ]
        }

        with patch(
            "agent.middleware.notify_incomplete_exit.post_slack_thread_reply",
            new_callable=AsyncMock,
        ) as mock_post:
            result = await notify_incomplete_exit.aafter_agent(state, self._make_runtime())

        assert result is None
        mock_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_last_message_is_ai(self) -> None:
        state = {
            "messages": [
                HumanMessage(content="hi"),
                AIMessage(content="all done"),
            ]
        }

        with patch(
            "agent.middleware.notify_incomplete_exit.post_slack_thread_reply",
            new_callable=AsyncMock,
        ) as mock_post:
            result = await notify_incomplete_exit.aafter_agent(state, self._make_runtime())

        assert result is None
        mock_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_slack_thread_config_missing(self) -> None:
        state = {
            "messages": [
                HumanMessage(content="open a PR"),
                AIMessage(content="opening PR"),
                ToolMessage(content="PR opened", tool_call_id="abc", name="open_pull_request"),
            ]
        }

        with (
            patch(
                "agent.middleware.notify_incomplete_exit.get_config",
                return_value={"configurable": {}},
            ),
            patch(
                "agent.middleware.notify_incomplete_exit.post_slack_thread_reply",
                new_callable=AsyncMock,
            ) as mock_post,
        ):
            result = await notify_incomplete_exit.aafter_agent(state, self._make_runtime())

        assert result is None
        mock_post.assert_not_called()
