from __future__ import annotations

import importlib
from typing import Any

import pytest

slack_reaction_tool = importlib.import_module("agent.tools.slack_add_reaction")


def _config() -> dict[str, Any]:
    return {
        "configurable": {
            "slack_thread": {
                "channel_id": "C1",
                "thread_ts": "1.0",
                "triggering_event_ts": "1.1",
            }
        }
    }


async def test_slack_add_reaction_defaults_to_triggering_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_add_slack_reaction(
        channel_id: str, message_ts: str, emoji: str = "eyes"
    ) -> bool:
        captured.update({"channel_id": channel_id, "message_ts": message_ts, "emoji": emoji})
        return True

    monkeypatch.setattr(slack_reaction_tool, "get_config", _config)
    monkeypatch.setattr(slack_reaction_tool, "add_slack_reaction", fake_add_slack_reaction)

    result = await slack_reaction_tool.slack_add_reaction()

    assert result == {"success": True}
    assert captured == {"channel_id": "C1", "message_ts": "1.1", "emoji": "eyes"}


async def test_slack_add_reaction_accepts_explicit_message_and_normalizes_emoji(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_add_slack_reaction(
        channel_id: str, message_ts: str, emoji: str = "eyes"
    ) -> bool:
        captured.update({"channel_id": channel_id, "message_ts": message_ts, "emoji": emoji})
        return True

    monkeypatch.setattr(slack_reaction_tool, "get_config", _config)
    monkeypatch.setattr(slack_reaction_tool, "add_slack_reaction", fake_add_slack_reaction)

    result = await slack_reaction_tool.slack_add_reaction(
        emoji=":white_check_mark:", message_ts="1.2"
    )

    assert result == {"success": True}
    assert captured == {"channel_id": "C1", "message_ts": "1.2", "emoji": "white_check_mark"}


async def test_slack_add_reaction_requires_slack_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack_reaction_tool, "get_config", lambda: {"configurable": {}})

    result = await slack_reaction_tool.slack_add_reaction()

    assert result == {"success": False, "error": "Missing slack_thread.channel_id in config"}


async def test_slack_add_reaction_rejects_empty_emoji(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack_reaction_tool, "get_config", _config)

    result = await slack_reaction_tool.slack_add_reaction(emoji="::")

    assert result == {"success": False, "error": "emoji is required"}
