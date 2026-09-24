import logging

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables.config import var_child_runnable_config

from agent import tools
from tests.support.slack_api import SlackAPI


async def test_list_channels_returns_bot_memberships_and_pagination(slack_api: SlackAPI) -> None:
    slack_api.respond(
        {
            "ok": True,
            "channels": [
                {"id": "C123", "name": "engineering", "is_private": False},
                {"id": "G456", "name": "incident", "is_private": True},
            ],
            "response_metadata": {"next_cursor": "next-page"},
        }
    )

    result = await tools.slack_list_channels()

    assert result == {
        "success": True,
        "channels": [
            {"id": "C123", "name": "engineering", "is_private": False},
            {"id": "G456", "name": "incident", "is_private": True},
        ],
        "next_cursor": "next-page",
    }
    method, params = slack_api.calls[0]
    assert method == "users.conversations"
    assert params["types"] == "public_channel,private_channel"
    assert params["exclude_archived"] == "1"
    assert "user" not in params

    slack_api.respond({"ok": True, "channels": [], "response_metadata": {"next_cursor": ""}})
    assert await tools.slack_list_channels(cursor="next-page") == {
        "success": True,
        "channels": [],
        "next_cursor": "",
    }
    assert slack_api.calls[1][1]["cursor"] == "next-page"


async def test_list_channels_keeps_cursor_on_empty_page(slack_api: SlackAPI) -> None:
    slack_api.respond({"ok": True, "channels": [], "response_metadata": {"next_cursor": "more"}})
    result = await tools.slack_list_channels()
    assert result == {"success": True, "channels": [], "next_cursor": "more"}


@pytest.mark.parametrize("channel_id", ["C123", "G456"])
async def test_post_channel_message_preserves_text_and_returns_receipt(
    slack_api: SlackAPI, channel_id: str
) -> None:
    slack_api.respond(
        {"ok": True, "channels": [{"id": channel_id, "name": "target", "is_private": True}]}
    )
    slack_api.respond({"ok": True, "channel": channel_id, "ts": "1700000000.000123"})

    result = await tools.slack_post_message(channel_id, "Ship it, @Alice(U123)!")

    assert result == {"success": True, "channel_id": channel_id, "message_ts": "1700000000.000123"}
    assert slack_api.calls[-1] == (
        "chat.postMessage",
        {
            "channel": channel_id,
            "text": "Ship it, <@U123>!",
            "unfurl_links": False,
            "unfurl_media": False,
        },
    )


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


@pytest.mark.parametrize(
    "channel_id,message",
    [
        ("", "hello"),
        ("#general", "hello"),
        ("U123", "hello"),
        ("D123", "hello"),
        ("C123", " \n"),
        ("C123", "x" * 40001),
    ],
    ids=["empty-channel", "channel-name", "user-id", "dm-id", "blank-message", "long-message"],
)
async def test_post_channel_message_rejects_invalid_input_without_sending(
    slack_api: SlackAPI, channel_id: str, message: str
) -> None:
    result = await tools.slack_post_message(channel_id, message)
    assert result["success"] is False
    assert result["error"]
    assert slack_api.calls == []


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


@pytest.mark.parametrize("error", ["invalid_auth", "missing_scope"])
async def test_post_channel_message_propagates_access_failure(
    slack_api: SlackAPI, error: str
) -> None:
    slack_api.respond({"ok": False, "error": error})
    result = await tools.slack_post_message("C123", "hello")
    assert result["success"] is False
    assert result["error"] == error
    assert len(slack_api.calls) == 1


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


async def test_list_channels_reports_api_failure(
    slack_api: SlackAPI, caplog: pytest.LogCaptureFixture
) -> None:
    slack_api.respond({"ok": False, "error": "missing_scope", "detail": "private-response-details"})
    result = await tools.slack_list_channels()
    assert result["success"] is False
    assert result["error"] == "missing_scope"
    records = [record for record in caplog.records if record.name == "agent.slack.tools.channels"]
    assert any(getattr(record, "slack_error", None) == "missing_scope" for record in records)
    assert all(record.exc_info is None for record in records)
    assert "private-response-details" not in caplog.text


@pytest.mark.parametrize("channels", [None, {}, ["not-a-channel"]])
async def test_list_channels_rejects_invalid_response(
    slack_api: SlackAPI, channels: object
) -> None:
    slack_api.respond({"ok": True, "channels": channels})
    result = await tools.slack_list_channels()
    assert result["success"] is False
    assert result["error"] == "invalid_slack_response"


async def test_channel_tools_report_missing_configuration(
    slack_api: SlackAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    for result in (
        await tools.slack_list_channels(),
        await tools.slack_post_message("C123", "hello"),
    ):
        assert result["success"] is False
        assert result["error"] == "missing_slack_bot_token"
    assert slack_api.calls == []
