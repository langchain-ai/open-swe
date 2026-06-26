from __future__ import annotations

import importlib
from typing import Any

import pytest

slack_reply_tool = importlib.import_module("agent.tools.slack_thread_reply")


def _config() -> dict[str, Any]:
    return {
        "configurable": {
            "slack_thread": {
                "channel_id": "C1",
                "thread_ts": "1.0",
            }
        }
    }


async def test_slack_thread_reply_returns_structured_error_for_msg_too_long(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_post_and_store_mapping(
        channel_id: str,
        thread_ts: str,
        message: str,
        *,
        blocks: list[dict[str, Any]] | None = None,
    ) -> tuple[str | None, str | None]:
        return None, "msg_too_long"

    monkeypatch.setattr(slack_reply_tool, "get_config", _config)
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", fake_post_and_store_mapping)

    result = await slack_reply_tool.slack_thread_reply("hello")

    assert result == {
        "success": False,
        "error": "msg_too_long",
        "slack_error": "msg_too_long",
        "message_chars": 5,
        "hint": "Slack rejected the message as too long; retry with a shorter message.",
    }


@pytest.mark.parametrize("slack_error", ["channel_not_found", "not_in_channel"])
async def test_slack_thread_reply_hints_not_to_retry_channel_errors(
    slack_error: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_post_and_store_mapping(
        channel_id: str,
        thread_ts: str,
        message: str,
        *,
        blocks: list[dict[str, Any]] | None = None,
    ) -> tuple[str | None, str | None]:
        return None, slack_error

    monkeypatch.setattr(slack_reply_tool, "get_config", _config)
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", fake_post_and_store_mapping)

    result = await slack_reply_tool.slack_thread_reply("hello")

    assert result["success"] is False
    assert result["error"] == slack_error
    assert result["slack_error"] == slack_error
    assert result["message_chars"] == 5
    assert "do not retry" in result["hint"]
    assert "trace output" in result["hint"]


async def test_slack_thread_reply_rate_limited_hint_includes_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_post_and_store_mapping(
        channel_id: str,
        thread_ts: str,
        message: str,
        *,
        blocks: list[dict[str, Any]] | None = None,
    ) -> tuple[str | None, str | None]:
        return None, "rate_limited: 30"

    monkeypatch.setattr(slack_reply_tool, "get_config", _config)
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", fake_post_and_store_mapping)

    result = await slack_reply_tool.slack_thread_reply("hello")

    assert result["success"] is False
    assert result["error"] == "rate_limited: 30"
    assert result["slack_error"] == "rate_limited: 30"
    assert "30s" in result["hint"]
    assert "wait" in result["hint"]


async def test_slack_thread_reply_rate_limited_hint_without_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_post_and_store_mapping(
        channel_id: str,
        thread_ts: str,
        message: str,
        *,
        blocks: list[dict[str, Any]] | None = None,
    ) -> tuple[str | None, str | None]:
        return None, "rate_limited"

    monkeypatch.setattr(slack_reply_tool, "get_config", _config)
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", fake_post_and_store_mapping)

    result = await slack_reply_tool.slack_thread_reply("hello")

    assert result["success"] is False
    assert result["slack_error"] == "rate_limited"
    assert "wait" in result["hint"]


async def test_slack_thread_reply_uses_post_failed_without_slack_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_post_and_store_mapping(
        channel_id: str,
        thread_ts: str,
        message: str,
        *,
        blocks: list[dict[str, Any]] | None = None,
    ) -> tuple[str | None, str | None]:
        return None, None

    monkeypatch.setattr(slack_reply_tool, "get_config", _config)
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", fake_post_and_store_mapping)

    result = await slack_reply_tool.slack_thread_reply("hello")

    assert result["success"] is False
    assert result["error"] == "post failed"
    assert result["slack_error"] is None
    assert result["message_chars"] == 5


async def test_slack_thread_reply_builds_option_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_post_and_store_mapping(
        channel_id: str,
        thread_ts: str,
        message: str,
        *,
        blocks: list[dict[str, Any]] | None = None,
    ) -> tuple[str | None, str | None]:
        captured.update(
            {"channel_id": channel_id, "thread_ts": thread_ts, "message": message, "blocks": blocks}
        )
        return "2.0", None

    monkeypatch.setattr(slack_reply_tool, "get_config", _config)
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", fake_post_and_store_mapping)

    result = await slack_reply_tool.slack_thread_reply("Pick one", options=["A", "B"])

    assert result == {"success": True}
    assert captured["channel_id"] == "C1"
    assert captured["thread_ts"] == "1.0"
    assert captured["message"] == "Pick one"
    actions = captured["blocks"][1]
    assert actions["type"] == "actions"
    assert [button["text"]["text"] for button in actions["elements"]] == ["A", "B"]
    assert actions["elements"][0]["action_id"] == "open_swe_option_select"
