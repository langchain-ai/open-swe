from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.middleware.refresh_slack_status import (
    refresh_slack_assistant_status_before_model,
)


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
        mock_set.assert_awaited_once_with("C1", "1.0")

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
