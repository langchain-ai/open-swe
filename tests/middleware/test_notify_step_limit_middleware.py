from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain.agents.middleware import AgentState
from langchain_core.messages import AIMessage, HumanMessage

from openswe.middleware.notify_step_limit import notify_step_limit_reached


class TestNotifyStepLimitReached:
    def _make_runtime(self) -> MagicMock:
        return MagicMock()

    @pytest.mark.asyncio
    async def test_posts_slack_reply_for_list_content_with_limit_marker(self) -> None:
        state: AgentState = {
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
                "openswe.run_config.get_config",
                return_value={
                    "configurable": {"slack_thread": {"channel_id": "C123", "thread_ts": "171.123"}}
                },
            ),
            patch(
                "openswe.middleware.notify_step_limit.post_slack_thread_reply",
                new_callable=AsyncMock,
            ) as mock_post,
        ):
            result = await notify_step_limit_reached.aafter_agent(state, self._make_runtime())

        assert result is None
        mock_post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_when_limit_marker_absent(self) -> None:
        state: AgentState = {"messages": [HumanMessage(content="keep going")]}

        with patch(
            "openswe.middleware.notify_step_limit.post_slack_thread_reply",
            new_callable=AsyncMock,
        ) as mock_post:
            result = await notify_step_limit_reached.aafter_agent(state, self._make_runtime())

        assert result is None
        mock_post.assert_not_called()
