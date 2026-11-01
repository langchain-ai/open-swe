import logging

import pytest

from openswe.slack import client as slack_utils
from openswe.utils.user_messages import warning


@pytest.mark.asyncio
async def test_slack_warning_post_logs_error(
    slack_api,
    caplog: pytest.LogCaptureFixture,
) -> None:
    slack_api.respond({"ok": True, "ts": "2.0"})

    with (
        caplog.at_level(logging.ERROR, logger=slack_utils.logger.name),
    ):
        result = await slack_utils.SlackConversation("C1").reply(
            "1.0", warning("Open SWE reached its maximum step limit.")
        )

    assert result == "2.0"
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
        result = await slack_utils.SlackConversation("C1").reply("1.0", "Normal Slack reply")

    assert result == "2.0"
    assert "Sent automated warning message to Slack" not in caplog.text
