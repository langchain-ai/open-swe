"""A PR thread starts with the latest turns of the channel conversation it continues."""

from unittest.mock import AsyncMock

import pytest
from pydantic import JsonValue

from openswe.github import webhook
from openswe.github.webhook import CHANNEL_HISTORY_MESSAGES, recent_turns
from openswe.webhooks import common

pytestmark = pytest.mark.usefixtures("no_local_threads")


def _human(n: int) -> dict[str, JsonValue]:
    return {"type": "human", "content": f"h{n}"}


def _calls(n: int) -> dict[str, JsonValue]:
    return {"type": "ai", "content": "", "tool_calls": [{"id": f"c{n}", "name": "x", "args": {}}]}


def _result(n: int) -> dict[str, JsonValue]:
    return {"type": "tool", "tool_call_id": f"c{n}", "content": "ok"}


def test_short_conversations_are_kept_whole() -> None:
    messages = [_human(0), _calls(0), _result(0), {"type": "ai", "content": "done"}]
    assert recent_turns(messages, CHANNEL_HISTORY_MESSAGES) == messages


def test_the_cap_moves_forward_to_a_turn_boundary() -> None:
    messages = [_human(0), _calls(0), _result(0), _result(0), _human(1), _calls(1), _result(1)]
    # Neither a tool result nor the middle of a tool call starts the copy.
    assert recent_turns(messages, 4) == messages[4:]
    # An AI message's tool calls start a turn, with their results.
    assert recent_turns(messages, 2) == messages[5:]
    assert recent_turns(messages, 1) == []
    # An AI message after a complete tool exchange starts a turn too.
    answered = [*messages[:3], {"type": "ai", "content": "done"}]
    assert recent_turns(answered, 1) == answered[3:]


def test_at_most_the_cap_is_kept() -> None:
    messages = [_human(n) for n in range(CHANNEL_HISTORY_MESSAGES + 10)]
    assert recent_turns(messages, CHANNEL_HISTORY_MESSAGES) == messages[10:]


@pytest.mark.asyncio
async def test_only_who_may_prompt_a_private_conversation_continues_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Feedback from anyone else starts nothing, so the conversation never reaches them."""
    monkeypatch.setattr(
        common,
        "extract_pr_context",
        AsyncMock(
            return_value=(
                {"owner": "o", "name": "r"},
                7,
                "open-swe/thread-1",
                "mallory",
                "https://github.com/o/r/pull/7",
                42,
                None,
            )
        ),
    )
    monkeypatch.setattr(
        common,
        "get_thread_metadata_safe",
        AsyncMock(
            return_value={
                "channel": "linear",
                "source": "linear",
                "visibility": "private",
                "owner_login": "ada",
            }
        ),
    )
    create = AsyncMock()
    monkeypatch.setattr(webhook.THREADS, "create", create)
    trigger = AsyncMock(return_value=True)
    monkeypatch.setattr(common, "trigger_or_queue_run", trigger)

    await webhook.process_github_pr_comment(
        {"action": "created", "sender": {"login": "mallory", "id": 2}, "comment": {}},
        "issue_comment",
        agent_thread_id="thread-1",
    )

    create.assert_not_awaited()
    trigger.assert_not_awaited()
