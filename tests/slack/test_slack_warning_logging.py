import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.utils import slack as slack_utils
from agent.utils.user_messages import warning


def _ok_response() -> MagicMock:
    response = MagicMock()
    response.json.return_value = {"ok": True, "ts": "2.0"}
    response.raise_for_status.return_value = None
    return response


def _async_client_cm(post_response: MagicMock) -> AsyncMock:
    client_cm = AsyncMock()
    client_cm.__aenter__.return_value = client_cm
    client_cm.post = AsyncMock(return_value=post_response)
    return client_cm


@pytest.mark.asyncio
async def test_slack_warning_post_logs_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    client_cm = _async_client_cm(_ok_response())

    with (
        caplog.at_level(logging.ERROR, logger=slack_utils.logger.name),
        patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm),
    ):
        result = await slack_utils._post_slack_message_with_ts(
            "C1",
            warning("Open SWE reached its maximum step limit."),
            thread_ts="1.0",
        )

    assert result == ("2.0", None)
    assert "Sent automated warning message to Slack thread C1/1.0" in caplog.text
    assert "⚠️ Open SWE reached its maximum step limit." in caplog.text


@pytest.mark.asyncio
async def test_plain_slack_post_does_not_log_warning_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    client_cm = _async_client_cm(_ok_response())

    with (
        caplog.at_level(logging.ERROR, logger=slack_utils.logger.name),
        patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm),
    ):
        result = await slack_utils._post_slack_message_with_ts(
            "C1",
            "Normal Slack reply",
            thread_ts="1.0",
        )

    assert result == ("2.0", None)
    assert "Sent automated warning message to Slack" not in caplog.text
