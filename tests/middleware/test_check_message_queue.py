from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from openswe.message_queue import QueuedMessage
from openswe.middleware.check_message_queue import (
    LinearNotifyState,
    check_message_queue_before_model,
)


def _envelope(message: dict) -> str:
    """The message's envelope text, whether its content is a string or blocks."""
    content = message["content"]
    if isinstance(content, str):
        return content
    return "".join(block["text"] for block in content if block.get("type") == "text")


async def _run(state: dict[str, Any]) -> dict[str, Any] | None:
    with patch(
        "openswe.middleware.check_message_queue.get_config",
        return_value={"configurable": {"thread_id": "thread-1"}},
    ):
        return await check_message_queue_before_model.abefore_model(
            cast(LinearNotifyState, state), MagicMock()
        )


@pytest.mark.asyncio
async def test_check_message_queue_announces_the_move_to_web_only_once(registry_db: None) -> None:
    await QueuedMessage.put(
        "thread-1",
        {
            "text": "and another thing",
            "source": "dashboard",
            "queue_id": "8a60896d-65ca-4e40-8a2d-1fbe81777002",
            "sender": {
                "id": "github:octocat",
                "platform": "github",
                "github_login": "octocat",
            },
        },
    )

    result = await _run({"messages": [], "reply_surface": "web"})

    assert result is not None
    envelopes = [_envelope(message) for message in result["messages"]]
    assert not any("system:dashboard-handoff" in envelope for envelope in envelopes)


@pytest.mark.asyncio
async def test_check_message_queue_keeps_slack_for_a_message_relayed_by_another_thread(
    registry_db: None,
) -> None:
    await QueuedMessage.put(
        "thread-1",
        {
            "text": "Pushed the fix",
            "relayed_from_thread_id": "worker-thread",
            "queue_id": "8a60896d-65ca-4e40-8a2d-1fbe81777003",
            "sender": {"id": "github:octocat", "platform": "github", "github_login": "octocat"},
        },
    )

    result = await _run({"messages": [], "reply_surface": "slack"})

    assert result is not None
    assert "reply_surface" not in result
    envelopes = "".join(_envelope(message) for message in result["messages"])
    assert "system:dashboard-handoff" not in envelopes
    assert 'surface="automation"' in envelopes
    assert 'relayed_from_thread="worker-thread"' in envelopes
