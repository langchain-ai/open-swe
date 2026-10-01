import logging

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables.config import var_child_runnable_config

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


async def test_channel_post_has_origin_and_actual_model_without_logging_body(
    slack_api: SlackAPI, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    slack_api.respond(
        {"ok": True, "channels": [{"id": "C123", "name": "target", "is_private": False}]}
    )
    token = var_child_runnable_config.set(
        {
            "configurable": {
                "thread_id": "origin-thread",
                "run_id": "run-1",
                "resolved_agent_model_id": "default-model",
            }
        }
    )
    caplog.set_level(logging.INFO, logger="agent.slack.client")
    try:
        result = await tools.slack_post_message(
            "C123",
            "private message body",
            state={
                "messages": [
                    AIMessage(content="", response_metadata={"model_name": "actual-model"})
                ],
            },
        )
    finally:
        var_child_runnable_config.reset(token)
    assert result["success"]
    payload = slack_api.calls[-1][1]
    footer = "<https://dashboard.example/agents/origin-thread|Open in Web> • actual-model"
    assert payload["text"] == f"private message body {footer}"
    assert payload["blocks"][-1]["elements"][0]["text"] == footer
    assert "thread_ts" not in payload
    records = [r for r in caplog.records if r.message == "Slack message delivered"]
    assert len(records) == 1
    assert records[0].slack_message_ts == "1.0"
    assert records[0].agent_thread_id == "origin-thread"
    assert "private message body" not in str(records[0].__dict__)
