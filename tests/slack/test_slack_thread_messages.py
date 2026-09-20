from unittest.mock import AsyncMock

import pytest

from agent.slack import client as slack_client
from agent.slack.channels import SlackChannel
from agent.slack.tools import read_thread_messages


@pytest.mark.asyncio
async def test_read_thread_messages_reports_unreadable_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(SlackChannel, "load", AsyncMock(return_value=None))

    result = await read_thread_messages.slack_read_thread_messages(" C123 ", "1789750092.207789")

    assert result == {
        "success": False,
        "error": "The bot cannot read that Slack channel. Retrying will not help; ask the "
        "sender to paste the thread or invite the bot to the channel.",
    }


@pytest.mark.asyncio
async def test_read_thread_messages_rejects_permalink_timestamp_without_separator() -> None:
    result = await read_thread_messages.slack_read_thread_messages("C123", "1789750092207789")

    assert result["success"] is False
    assert "seconds plus a dot and six digits" in result["error"]
    assert "16-digit permalink value" in result["error"]


@pytest.mark.asyncio
async def test_read_thread_messages_reports_unresolved_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(SlackChannel, "load", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        read_thread_messages,
        "fetch_slack_thread_messages",
        AsyncMock(side_effect=slack_client.SlackThreadFetchError("thread_not_found")),
    )

    result = await read_thread_messages.slack_read_thread_messages("C123", "1789750092.207789")

    assert result["success"] is False
    assert "could not resolve that thread timestamp" in result["error"]
    assert "1789750092.207789" in result["error"]


@pytest.mark.asyncio
async def test_read_thread_messages_reports_other_slack_error_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(SlackChannel, "load", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        read_thread_messages,
        "fetch_slack_thread_messages",
        AsyncMock(side_effect=slack_client.SlackThreadFetchError("rate_limited")),
    )

    result = await read_thread_messages.slack_read_thread_messages("C123", "1789750092.207789")

    assert result == {
        "success": False,
        "error": "Could not fetch the Slack thread (rate_limited).",
    }


@pytest.mark.asyncio
async def test_read_thread_messages_keeps_successful_empty_thread_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(SlackChannel, "load", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        read_thread_messages,
        "fetch_slack_thread_messages",
        AsyncMock(return_value=[]),
    )

    result = await read_thread_messages.slack_read_thread_messages("C123", "1789750092.207789")

    assert result == {
        "success": True,
        "formatted": "(no thread messages available)",
        "count": 0,
        "truncated": False,
    }


@pytest.mark.asyncio
async def test_async_forwarded_formatting_annotates_unreadable_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(SlackChannel, "load", AsyncMock(return_value=None))
    messages = [
        {
            "ts": "1.0",
            "text": "please read this",
            "user": "U123",
            "attachments": [
                {
                    "is_share": True,
                    "text": "forwarded",
                    "from_url": "https://example.slack.com/archives/C123/p1789750092207789",
                }
            ],
        }
    ]

    formatted = await slack_client.format_slack_messages_for_prompt_async(messages)

    assert (
        "Source: https://example.slack.com/archives/C123/p1789750092207789 "
        "(thread not readable by the bot - ask the sender to paste it)" in formatted
    )


@pytest.mark.asyncio
async def test_async_forwarded_formatting_keeps_source_for_readable_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(SlackChannel, "load", AsyncMock(return_value=object()))
    source = "https://example.slack.com/archives/C123/p1789750092207789"
    messages = [
        {
            "ts": "1.0",
            "text": "forwarded",
            "user": "U123",
            "attachments": [{"is_share": True, "from_url": source}],
        }
    ]

    formatted = await slack_client.format_slack_messages_for_prompt_async(messages)

    assert f"Source: {source}" in formatted
    assert "thread not readable by the bot" not in formatted


@pytest.mark.asyncio
async def test_async_forwarded_formatting_keeps_source_on_lookup_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(SlackChannel, "load", AsyncMock(side_effect=RuntimeError("offline")))
    source = "https://example.slack.com/archives/C123/p1789750092207789"
    messages = [
        {
            "ts": "1.0",
            "text": "forwarded",
            "user": "U123",
            "attachments": [{"is_share": True, "from_url": source}],
        }
    ]

    formatted = await slack_client.format_slack_messages_for_prompt_async(messages)

    assert f"Source: {source}" in formatted
    assert "thread not readable by the bot" not in formatted
