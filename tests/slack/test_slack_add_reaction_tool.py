import importlib
from typing import Any

import pytest

slack_reaction_tool = importlib.import_module("agent.slack.tools.add_reaction")


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


async def test_slack_add_reaction_accepts_explicit_message_and_normalizes_emoji(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_add_slack_reaction(channel_id: str, message_ts: str, emoji: str) -> bool:
        captured.update({"channel_id": channel_id, "message_ts": message_ts, "emoji": emoji})
        return True

    monkeypatch.setattr(slack_reaction_tool, "get_config", _config)
    monkeypatch.setattr(slack_reaction_tool, "add_slack_reaction", fake_add_slack_reaction)

    result = await slack_reaction_tool.slack_add_reaction(emoji=":eyes:", message_ts="1.2")

    assert result == {"success": True}
    assert captured == {"channel_id": "C1", "message_ts": "1.2", "emoji": "eyes"}


@pytest.mark.parametrize(
    ("active_thread_ts", "message_ts", "expected_ts"),
    [("1.0", None, "1.1"), ("1.0", "1.2", "1.2"), ("2.0", None, "2.1")],
)
async def test_slack_add_reaction_targets_current_trigger_without_repointing_moved_thread(
    monkeypatch: pytest.MonkeyPatch,
    active_thread_ts: str,
    message_ts: str | None,
    expected_ts: str,
) -> None:
    captured: list[tuple[str, str, str]] = []

    async def fake_active_thread(*args: object) -> dict[str, str]:
        return {
            "channel_id": "C1",
            "thread_ts": active_thread_ts,
            "triggering_event_ts": "1.0" if active_thread_ts == "1.0" else "2.1",
        }

    async def fake_add_reaction(channel_id: str, target_ts: str, emoji: str) -> bool:
        captured.append((channel_id, target_ts, emoji))
        return True

    monkeypatch.setattr(slack_reaction_tool, "get_config", _config)
    monkeypatch.setattr(slack_reaction_tool, "get_active_slack_thread", fake_active_thread)
    monkeypatch.setattr(slack_reaction_tool, "add_slack_reaction", fake_add_reaction)

    result = await slack_reaction_tool.slack_add_reaction("saluting_face", message_ts)

    assert result == {"success": True}
    assert captured == [("C1", expected_ts, "saluting_face")]


@pytest.mark.parametrize("emoji", ["white_check_mark", ":white_check_mark:"])
async def test_slack_add_reaction_rejects_white_check_mark(
    monkeypatch: pytest.MonkeyPatch,
    emoji: str,
) -> None:
    async def fail_if_called(channel_id: str, message_ts: str, reaction: str) -> bool:
        pytest.fail(f"unexpected Slack reaction: {channel_id} {message_ts} {reaction}")

    monkeypatch.setattr(slack_reaction_tool, "get_config", _config)
    monkeypatch.setattr(slack_reaction_tool, "add_slack_reaction", fail_if_called)

    result = await slack_reaction_tool.slack_add_reaction(emoji=emoji)

    assert result == {
        "success": False,
        "error": "white_check_mark is not allowed because it can imply PR approval",
    }
