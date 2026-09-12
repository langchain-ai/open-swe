import logging

import pytest

from agent.slack import client as slack_utils
from agent.utils.user_messages import warning


@pytest.mark.asyncio
async def test_slack_warning_post_logs_error(
    slack_api,
    caplog: pytest.LogCaptureFixture,
) -> None:
    slack_api.respond({"ok": True, "ts": "2.0"})

    with (
        caplog.at_level(logging.ERROR, logger=slack_utils.logger.name),
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
    slack_api,
    caplog: pytest.LogCaptureFixture,
) -> None:
    slack_api.respond({"ok": True, "ts": "2.0"})

    with (
        caplog.at_level(logging.ERROR, logger=slack_utils.logger.name),
    ):
        result = await slack_utils._post_slack_message_with_ts(
            "C1",
            "Normal Slack reply",
            thread_ts="1.0",
        )

    assert result == ("2.0", None)
    assert "Sent automated warning message to Slack" not in caplog.text
