"""A PR thread starts with the latest turns of the channel conversation it continues."""

from typing import Any

from openswe.github.webhook import CHANNEL_HISTORY_MESSAGES, recent_turns


def _human(n: int) -> dict[str, Any]:
    return {"type": "human", "content": f"h{n}"}


def _calls(n: int) -> dict[str, Any]:
    return {"type": "ai", "content": "", "tool_calls": [{"id": f"c{n}", "name": "x", "args": {}}]}


def _result(n: int) -> dict[str, Any]:
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
