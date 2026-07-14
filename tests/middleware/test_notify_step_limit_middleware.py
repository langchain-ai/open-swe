from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.middleware.notify_step_limit import notify_step_limit_reached


class TestNotifyStepLimitReached:
    def _make_runtime(self) -> MagicMock:
        return MagicMock()

    @pytest.mark.asyncio
    async def test_posts_slack_reply_when_limit_marker_present(self) -> None:
        state = {"messages": [AIMessage(content="Model call limits exceeded: run limit reached")]}

        with (
            patch(
                "agent.middleware.notify_step_limit.get_config",
                return_value={
                    "configurable": {"slack_thread": {"channel_id": "C123", "thread_ts": "171.123"}}
                },
            ),
            patch(
                "agent.middleware.notify_step_limit.post_slack_thread_reply",
                new_callable=AsyncMock,
            ) as mock_post,
        ):
            result = await notify_step_limit_reached.aafter_agent(state, self._make_runtime())

        assert result is None
        mock_post.assert_awaited_once()
        assert mock_post.await_args.args[0:2] == ("C123", "171.123")
        assert "maximum step limit" in mock_post.await_args.args[2]

    @pytest.mark.asyncio
    async def test_posts_slack_reply_for_list_content_with_limit_marker(self) -> None:
        state = {
            "messages": [
                AIMessage(
                    content=[
                        {"type": "text", "text": "Model call limits exceeded:"},
                        {"type": "text", "text": "run limit reached"},
                    ]
                )
            ]
        }

        with (
            patch(
                "agent.middleware.notify_step_limit.get_config",
                return_value={
                    "configurable": {"slack_thread": {"channel_id": "C123", "thread_ts": "171.123"}}
                },
            ),
            patch(
                "agent.middleware.notify_step_limit.post_slack_thread_reply",
                new_callable=AsyncMock,
            ) as mock_post,
        ):
            result = await notify_step_limit_reached.aafter_agent(state, self._make_runtime())

        assert result is None
        mock_post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_when_limit_marker_absent(self) -> None:
        state = {"messages": [HumanMessage(content="keep going")]}

        with patch(
            "agent.middleware.notify_step_limit.post_slack_thread_reply",
            new_callable=AsyncMock,
        ) as mock_post:
            result = await notify_step_limit_reached.aafter_agent(state, self._make_runtime())

        assert result is None
        mock_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_slack_thread_config_missing(self) -> None:
        state = {"messages": [AIMessage(content="Model call limits exceeded: run limit reached")]}

        with (
            patch(
                "agent.middleware.notify_step_limit.get_config",
                return_value={"configurable": {}},
            ),
            patch(
                "agent.middleware.notify_step_limit.post_slack_thread_reply",
                new_callable=AsyncMock,
            ) as mock_post,
        ):
            result = await notify_step_limit_reached.aafter_agent(state, self._make_runtime())

        assert result is None
        mock_post.assert_not_called()
