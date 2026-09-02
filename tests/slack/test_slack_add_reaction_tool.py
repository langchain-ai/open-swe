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


async def test_slack_add_reaction_requires_message_ts() -> None:
    with pytest.raises(TypeError, match="missing 1 required positional argument: 'message_ts'"):
        await slack_reaction_tool.slack_add_reaction(emoji="saluting_face")


async def test_slack_add_reaction_accepts_explicit_message_and_normalizes_emoji(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_add_slack_reaction(channel_id: str, message_ts: str, emoji: str) -> str | None:
        captured.update({"channel_id": channel_id, "message_ts": message_ts, "emoji": emoji})
        return None

    monkeypatch.setattr(slack_reaction_tool, "get_config", _config)
    monkeypatch.setattr(slack_reaction_tool, "add_slack_reaction", fake_add_slack_reaction)

    result = await slack_reaction_tool.slack_add_reaction(emoji=":eyes:", message_ts="1.2")

    assert result == {"success": True}
    assert captured == {"channel_id": "C1", "message_ts": "1.2", "emoji": "eyes"}


async def test_slack_add_reaction_surfaces_slack_error_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_add_slack_reaction(channel_id: str, message_ts: str, emoji: str) -> str:
        assert (channel_id, message_ts, emoji) == ("C1", "1.2", "eyes")
        return "message_not_found"

    monkeypatch.setattr(slack_reaction_tool, "get_config", _config)
    monkeypatch.setattr(slack_reaction_tool, "add_slack_reaction", fake_add_slack_reaction)

    result = await slack_reaction_tool.slack_add_reaction(emoji="eyes", message_ts="1.2")

    assert result == {
        "success": False,
        "error": "Slack reactions.add failed: message_not_found",
        "channel_id": "C1",
        "target_ts": "1.2",
    }


@pytest.mark.parametrize("emoji", ["white_check_mark", ":white_check_mark:"])
async def test_slack_add_reaction_rejects_white_check_mark(
    monkeypatch: pytest.MonkeyPatch,
    emoji: str,
) -> None:
    async def fail_if_called(channel_id: str, message_ts: str, reaction: str) -> bool:
        pytest.fail(f"unexpected Slack reaction: {channel_id} {message_ts} {reaction}")

    monkeypatch.setattr(slack_reaction_tool, "get_config", _config)
    monkeypatch.setattr(slack_reaction_tool, "add_slack_reaction", fail_if_called)

    result = await slack_reaction_tool.slack_add_reaction(emoji=emoji, message_ts="1.1")

    assert result == {
        "success": False,
        "error": "white_check_mark is not allowed because it can imply PR approval",
    }


async def test_slack_add_reaction_requires_slack_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack_reaction_tool, "get_config", lambda: {"configurable": {}})

    result = await slack_reaction_tool.slack_add_reaction(emoji="saluting_face", message_ts="1.1")

    assert result == {"success": False, "error": "Missing slack_thread.channel_id in config"}


async def test_slack_add_reaction_rejects_empty_emoji(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack_reaction_tool, "get_config", _config)

    result = await slack_reaction_tool.slack_add_reaction(emoji="::", message_ts="1.1")

    assert result == {"success": False, "error": "emoji is required"}
