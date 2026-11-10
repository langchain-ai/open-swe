from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest

from openswe.slack.channels import SlackChannel
from openswe.slack.tools.read_channel_messages import slack_read_channel_messages
from tests.support.slack_api import SlackAPI


async def test_long_channel_history_keeps_newest_alerts_first(
    slack_api: SlackAPI,
    monkeypatch: pytest.MonkeyPatch,
    grant_tool_access: Callable[..., None],
) -> None:
    grant_tool_access(private=True)
    channel = SlackChannel(
        id="C1",
        payload={
            "id": "C1",
            "is_channel": True,
            "is_private": False,
            "is_ext_shared": False,
            "is_pending_ext_shared": False,
        },
    )
    monkeypatch.setattr(SlackChannel, "load", AsyncMock(return_value=channel))
    alerts = [
        {
            "ts": f"1794268800.{index:06d}",
            "thread_ts": f"1794268800.{index:06d}",
            "reply_count": index + 1,
            "bot_id": "B1",
            "text": f"Alert {index:02d}: " + "Terraform failure details. " * 200,
        }
        for index in range(15)
    ]
    slack_api.respond({"ok": True, "messages": list(reversed(alerts))})

    result = await slack_read_channel_messages("C1", limit=15)

    assert result["success"] is True
    assert result["count"] == 15
    formatted = result["formatted"]
    assert isinstance(formatted, str)
    assert "Alert 14:" in formatted[:4000]
    assert "Alert 00:" not in formatted[:4000]
    entries = formatted.splitlines()
    assert len(entries) == 15
    for index, entry in zip(reversed(range(15)), entries, strict=True):
        timestamp = f"1794268800.{index:06d}"
        assert f"Alert {index:02d}:" in entry
        assert f"[message_ts={timestamp}]" in entry
        replies = "reply" if index == 0 else "replies"
        assert f"[thread: {index + 1} {replies}, thread_ts={timestamp}]" in entry
