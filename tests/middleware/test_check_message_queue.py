from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from agent.message_queue import QueuedMessage
from agent.middleware.check_message_queue import (
    LinearNotifyState,
    check_message_queue_before_model,
)


class _QueuedItem:
    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value


class _GraphStore:
    """The graph's LangGraph Store, holding auto-fix events and the legacy queue."""

    def __init__(self, items: dict[tuple[tuple[str, ...], str], dict[str, Any]] | None = None):
        self.items = items or {}

    async def aget(self, namespace: tuple[str, ...], key: str) -> _QueuedItem | None:
        value = self.items.get((namespace, key))
        return _QueuedItem(value) if value is not None else None

    async def adelete(self, namespace: tuple[str, ...], key: str) -> None:
        self.items.pop((namespace, key), None)


def _envelope(message: dict) -> str:
    """The message's envelope text, whether its content is a string or blocks."""
    content = message["content"]
    if isinstance(content, str):
        return content
    return "".join(block["text"] for block in content if block.get("type") == "text")


async def _run(store: _GraphStore, state: dict[str, Any]) -> dict[str, Any] | None:
    with (
        patch(
            "agent.middleware.check_message_queue.get_config",
            return_value={"configurable": {"thread_id": "thread-1"}},
        ),
        patch("agent.middleware.check_message_queue.get_store", return_value=store),
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

    result = await _run(_GraphStore(), {"messages": [], "reply_surface": "web"})

    assert result is not None
    envelopes = [_envelope(message) for message in result["messages"]]
    assert not any("system:dashboard-handoff" in envelope for envelope in envelopes)


@pytest.mark.asyncio
async def test_check_message_queue_keeps_follow_ups_queued_while_it_builds(
    registry_db: None,
) -> None:
    await QueuedMessage.put("thread-1", {"text": "first", "image_urls": ["https://img.test/a.png"]})

    async def build_and_queue(payload: dict[str, Any], *, model_id: str | None) -> list:
        await QueuedMessage.put("thread-1", {"text": "queued during the image fetch"})
        return [{"type": "text", "text": payload["text"]}]

    with (
        patch(
            "agent.middleware.check_message_queue._resolve_thread_model_id",
            return_value=None,
        ),
        patch(
            "agent.middleware.check_message_queue._build_blocks_from_payload",
            side_effect=build_and_queue,
        ),
    ):
        result = await _run(_GraphStore(), {"messages": []})

    assert result is not None
    assert "first" in _envelope(result["messages"][-1])
    assert [message.content for message in await QueuedMessage.for_thread("thread-1")] == [
        {"text": "queued during the image fetch"}
    ]


@pytest.mark.asyncio
async def test_check_message_queue_drains_the_legacy_store_list(registry_db: None) -> None:
    legacy = (("queue", "thread-1"), "pending_messages")
    store = _GraphStore({legacy: {"messages": [{"content": "queued before the move"}]}})

    result = await _run(store, {"messages": []})

    assert result is not None
    assert "queued before the move" in _envelope(result["messages"][-1])
    assert legacy not in store.items
