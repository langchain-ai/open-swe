import pytest

from agent import tools
from tests.support.slack_api import SlackAPI


async def test_list_channels_keeps_cursor_on_empty_page(slack_api: SlackAPI) -> None:
    slack_api.respond({"ok": True, "channels": [], "response_metadata": {"next_cursor": "more"}})
    result = await tools.slack_list_channels()
    assert result == {"success": True, "channels": [], "next_cursor": "more"}


async def test_post_channel_message_requires_active_channel_membership(
    slack_api: SlackAPI,
) -> None:
    slack_api.respond(
        {"ok": True, "channels": [{"id": "C456", "name": "other", "is_private": False}]}
    )
    result = await tools.slack_post_message("C123", "hello")
    assert result["success"] is False
    assert result["error"] == "not_in_channel"
    assert [method for method, _ in slack_api.calls] == ["users.conversations"]


async def test_post_channel_message_finds_membership_after_empty_page(slack_api: SlackAPI) -> None:
    slack_api.respond({"ok": True, "channels": [], "response_metadata": {"next_cursor": "more"}})
    slack_api.respond(
        {"ok": True, "channels": [{"id": "C123", "name": "target", "is_private": False}]}
    )
    slack_api.respond({"ok": True, "ts": "1700000000.000123"})
    result = await tools.slack_post_message("C123", "hello")
    assert result["success"] is True
    assert slack_api.calls[1][1]["cursor"] == "more"
    assert slack_api.calls[-1][0] == "chat.postMessage"


async def test_post_channel_message_stops_on_repeated_membership_cursor(
    slack_api: SlackAPI,
) -> None:
    for _ in range(2):
        slack_api.respond(
            {"ok": True, "channels": [], "response_metadata": {"next_cursor": "same"}}
        )
    result = await tools.slack_post_message("C123", "hello")
    assert result == {"success": False, "error": "invalid_slack_response"}
    assert len(slack_api.calls) == 2


@pytest.mark.parametrize(
    "error,status,expected",
    [("ratelimited", 429, "rate_limited: 30"), ("not_in_channel", 200, "not_in_channel")],
)
async def test_post_channel_message_reports_slack_failure_without_retry(
    slack_api: SlackAPI, error: str, status: int, expected: str
) -> None:
    slack_api.respond(
        {"ok": True, "channels": [{"id": "C123", "name": "target", "is_private": False}]}
    )
    slack_api.respond({"ok": False, "error": error}, status=status, headers={"Retry-After": "30"})
    result = await tools.slack_post_message("C123", "hello")
    assert result["success"] is False
    assert result["error"] == expected
    assert len(slack_api.calls) == 2
