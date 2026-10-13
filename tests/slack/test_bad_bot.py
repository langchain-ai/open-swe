from typing import Any
from unittest.mock import AsyncMock

import pytest

from openswe.slack import bad_bot


@pytest.fixture
def slack(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    mocks = {
        "lookup_slack_thread_id": AsyncMock(return_value="thread-1"),
        "record_thread_feedback": AsyncMock(return_value=True),
        "add_slack_reaction": AsyncMock(return_value=True),
        "post_slack_ephemeral_message": AsyncMock(return_value=True),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(bad_bot, name, mock)
    monkeypatch.setattr(bad_bot, "langgraph_client", lambda: None)
    return mocks


async def test_records_negative_feedback_and_reacts(slack: dict[str, Any]) -> None:
    await bad_bot.process_slack_bad_bot("C1", "1.0", "2.0", "U1", "ignored the test")

    slack["record_thread_feedback"].assert_awaited_once_with(
        "thread-1",
        "bad",
        "ignored the test",
        event_id="slack_badbot:C1:2.0",
        run_id="",
        source="slack",
        source_info={
            "source": "slack_badbot",
            "channel_id": "C1",
            "message_ts": "2.0",
            "user_id": "U1",
        },
        slack_user_id="U1",
    )
    slack["add_slack_reaction"].assert_awaited_once_with("C1", "2.0", "memo")


async def test_without_a_thread_tells_only_the_sender(slack: dict[str, Any]) -> None:
    slack["lookup_slack_thread_id"].return_value = None

    await bad_bot.process_slack_bad_bot("C1", "1.0", "2.0", "U1", "bad")

    slack["record_thread_feedback"].assert_not_awaited()
    slack["post_slack_ephemeral_message"].assert_awaited_once()
