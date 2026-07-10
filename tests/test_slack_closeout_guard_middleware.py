from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.middleware.slack_closeout_guard import ensure_slack_closeout_reply

_SLACK_CONFIG = {"configurable": {"slack_thread": {"channel_id": "C123", "thread_ts": "171.123"}}}


def _pr_trajectory() -> list:
    return [
        HumanMessage(content="revert the feature flag"),
        AIMessage(
            content="Opening the PR now.",
            tool_calls=[{"name": "open_pull_request", "args": {}, "id": "pr1"}],
        ),
        ToolMessage(
            content='{"success": true, "url": "https://github.com/o/r/pull/1717", "number": 1717}',
            tool_call_id="pr1",
        ),
        AIMessage(content="Reverted the flag and opened PR #1717."),
    ]


class TestEnsureSlackCloseoutReply:
    def _runtime(self) -> MagicMock:
        return MagicMock()

    @pytest.mark.asyncio
    async def test_posts_synthesized_closeout_when_pr_opened_without_reply(self) -> None:
        state = {"messages": _pr_trajectory()}

        with (
            patch(
                "agent.middleware.slack_closeout_guard.get_config",
                return_value=_SLACK_CONFIG,
            ),
            patch(
                "agent.middleware.slack_closeout_guard.post_slack_thread_reply",
                new_callable=AsyncMock,
            ) as mock_post,
        ):
            result = await ensure_slack_closeout_reply.aafter_agent(state, self._runtime())

        assert result is None
        mock_post.assert_awaited_once()
        assert mock_post.await_args.args[0:2] == ("C123", "171.123")
        posted = mock_post.await_args.args[2]
        assert "https://github.com/o/r/pull/1717" in posted
        assert "Reverted the flag" in posted

    @pytest.mark.asyncio
    async def test_skips_when_slack_reply_already_succeeded(self) -> None:
        state = {
            "messages": [
                *_pr_trajectory(),
                AIMessage(
                    content="",
                    tool_calls=[{"name": "slack_thread_reply", "args": {}, "id": "r1"}],
                ),
                ToolMessage(content='{"success": true}', tool_call_id="r1"),
            ]
        }

        with (
            patch(
                "agent.middleware.slack_closeout_guard.get_config",
                return_value=_SLACK_CONFIG,
            ),
            patch(
                "agent.middleware.slack_closeout_guard.post_slack_thread_reply",
                new_callable=AsyncMock,
            ) as mock_post,
        ):
            result = await ensure_slack_closeout_reply.aafter_agent(state, self._runtime())

        assert result is None
        mock_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_posts_when_slack_reply_failed(self) -> None:
        state = {
            "messages": [
                *_pr_trajectory(),
                AIMessage(
                    content="",
                    tool_calls=[{"name": "slack_thread_reply", "args": {}, "id": "r1"}],
                ),
                ToolMessage(
                    content='{"success": false, "error": "not_in_channel"}', tool_call_id="r1"
                ),
            ]
        }

        with (
            patch(
                "agent.middleware.slack_closeout_guard.get_config",
                return_value=_SLACK_CONFIG,
            ),
            patch(
                "agent.middleware.slack_closeout_guard.post_slack_thread_reply",
                new_callable=AsyncMock,
            ) as mock_post,
        ):
            result = await ensure_slack_closeout_reply.aafter_agent(state, self._runtime())

        assert result is None
        mock_post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_when_no_slack_thread_config(self) -> None:
        state = {"messages": _pr_trajectory()}

        with (
            patch(
                "agent.middleware.slack_closeout_guard.get_config",
                return_value={"configurable": {}},
            ),
            patch(
                "agent.middleware.slack_closeout_guard.post_slack_thread_reply",
                new_callable=AsyncMock,
            ) as mock_post,
        ):
            result = await ensure_slack_closeout_reply.aafter_agent(state, self._runtime())

        assert result is None
        mock_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_nothing_to_summarize(self) -> None:
        state = {"messages": [HumanMessage(content="hello")]}

        with (
            patch(
                "agent.middleware.slack_closeout_guard.get_config",
                return_value=_SLACK_CONFIG,
            ),
            patch(
                "agent.middleware.slack_closeout_guard.post_slack_thread_reply",
                new_callable=AsyncMock,
            ) as mock_post,
        ):
            result = await ensure_slack_closeout_reply.aafter_agent(state, self._runtime())

        assert result is None
        mock_post.assert_not_called()
