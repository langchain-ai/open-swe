from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.middleware.model_fallback import PROVIDER_ERROR_MARKER
from agent.middleware.notify_provider_error import notify_provider_error_reached


class TestNotifyProviderErrorReached:
    def _make_runtime(self) -> MagicMock:
        return MagicMock()

    @pytest.mark.asyncio
    async def test_posts_slack_reply_when_marker_present(self) -> None:
        state = {
            "messages": [
                AIMessage(
                    content=f"{PROVIDER_ERROR_MARKER} (primary=AuthenticationError, fallback=AuthenticationError)"
                )
            ]
        }

        with (
            patch(
                "agent.middleware.notify_provider_error.get_config",
                return_value={
                    "configurable": {"slack_thread": {"channel_id": "C123", "thread_ts": "171.123"}}
                },
            ),
            patch(
                "agent.middleware.notify_provider_error.post_slack_thread_reply",
                new_callable=AsyncMock,
            ) as mock_post,
        ):
            result = await notify_provider_error_reached.aafter_agent(state, self._make_runtime())

        assert result is None
        mock_post.assert_awaited_once()
        assert mock_post.await_args.args[0:2] == ("C123", "171.123")
        assert "provider error" in mock_post.await_args.args[2]

    @pytest.mark.asyncio
    async def test_posts_to_github_pr_when_no_slack(self) -> None:
        state = {"messages": [AIMessage(content=f"{PROVIDER_ERROR_MARKER} (details)")]}

        with (
            patch(
                "agent.middleware.notify_provider_error.get_config",
                return_value={
                    "configurable": {
                        "repo": {"owner": "acme", "name": "widgets"},
                        "pr_number": 42,
                    }
                },
            ),
            patch(
                "agent.middleware.notify_provider_error.get_github_token",
                return_value="ghs_test",
            ),
            patch(
                "agent.middleware.notify_provider_error.post_github_comment",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_post,
        ):
            result = await notify_provider_error_reached.aafter_agent(state, self._make_runtime())

        assert result is None
        mock_post.assert_awaited_once()
        args, kwargs = mock_post.await_args
        assert args[0] == {"owner": "acme", "name": "widgets"}
        assert args[1] == 42
        assert "provider error" in args[2]
        assert kwargs["token"] == "ghs_test"

    @pytest.mark.asyncio
    async def test_skips_when_marker_absent(self) -> None:
        state = {"messages": [HumanMessage(content="keep going")]}

        with patch(
            "agent.middleware.notify_provider_error.post_slack_thread_reply",
            new_callable=AsyncMock,
        ) as mock_post:
            result = await notify_provider_error_reached.aafter_agent(state, self._make_runtime())

        assert result is None
        mock_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_no_surface_configured(self) -> None:
        state = {"messages": [AIMessage(content=f"{PROVIDER_ERROR_MARKER} (details)")]}

        with (
            patch(
                "agent.middleware.notify_provider_error.get_config",
                return_value={"configurable": {}},
            ),
            patch(
                "agent.middleware.notify_provider_error.post_slack_thread_reply",
                new_callable=AsyncMock,
            ) as mock_slack,
            patch(
                "agent.middleware.notify_provider_error.post_github_comment",
                new_callable=AsyncMock,
            ) as mock_gh,
        ):
            result = await notify_provider_error_reached.aafter_agent(state, self._make_runtime())

        assert result is None
        mock_slack.assert_not_called()
        mock_gh.assert_not_called()
