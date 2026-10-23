from typing import Any
from unittest.mock import patch

import pytest

from openswe.input_messages import RunMessage
from openswe.message_queue import QueuedMessage
from openswe.middleware.require_user_reply import ReplySurface
from openswe.threads.inbox import Taken, ThreadInbox


def _envelope(message: RunMessage) -> str:
    """The message's envelope text, whether its content is a string or blocks."""
    content = message["content"]
    if isinstance(content, str):
        return content
    return "".join(block["text"] for block in content if block.get("type") == "text")


async def _read(surface: ReplySurface = "web") -> Taken:
    with patch(
        "openswe.threads.inbox.get_config",
        return_value={"configurable": {"thread_id": "thread-1"}},
    ):
        return await ThreadInbox("thread-1", events=False).read([], surface)


@pytest.mark.asyncio
async def test_inbox_announces_the_move_to_web_only_once(registry_db: None) -> None:
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

    taken = await _read("web")

    envelopes = [_envelope(message) for message in taken.messages]
    assert not any("system:dashboard-handoff" in envelope for envelope in envelopes)


@pytest.mark.asyncio
async def test_inbox_keeps_slack_for_a_message_relayed_by_another_thread(
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

    taken = await _read("slack")

    assert taken.reply_surface is None
    envelopes = "".join(_envelope(message) for message in taken.messages)
    assert "system:dashboard-handoff" not in envelopes
    assert 'surface="automation"' in envelopes
    assert 'relayed_from_thread="worker-thread"' in envelopes


@pytest.mark.asyncio
async def test_inbox_receives_only_the_follow_ups_it_read(
    registry_db: None,
) -> None:
    await QueuedMessage.put("thread-1", {"text": "first", "image_urls": ["https://img.test/a.png"]})

    async def build_and_queue(payload: dict[str, Any], *, model_id: str | None) -> list:
        await QueuedMessage.put("thread-1", {"text": "queued during the image fetch"})
        return [{"type": "text", "text": payload["text"]}]

    with (
        patch(
            "openswe.threads.inbox._resolve_thread_model_id",
            return_value=None,
        ),
        patch(
            "openswe.threads.inbox._build_blocks_from_payload",
            side_effect=build_and_queue,
        ),
    ):
        taken = await _read()

    assert "first" in _envelope(taken.messages[-1])
    await ThreadInbox("thread-1", events=False).receive(taken.queued)
    assert [message.content for message in await QueuedMessage.for_thread("thread-1")] == [
        {"text": "queued during the image fetch"}
    ]
