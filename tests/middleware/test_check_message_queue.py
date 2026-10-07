from copy import deepcopy
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from openswe.middleware.check_message_queue import (
    LinearNotifyState,
    check_message_queue_before_model,
)


class _QueuedItem:
    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value


class _FakeStore:
    def __init__(self, items: dict[tuple[tuple[str, ...], str], dict[str, Any]]) -> None:
        self.items = items
        self.deleted: list[tuple[tuple[str, ...], str]] = []

    async def aget(self, namespace: tuple[str, ...], key: str) -> _QueuedItem | None:
        value = self.items.get((namespace, key))
        # The real store hands back a fresh deserialization every time.
        return _QueuedItem(deepcopy(value)) if value is not None else None

    async def aput(self, namespace: tuple[str, ...], key: str, value: dict[str, Any]) -> None:
        self.items[(namespace, key)] = value

    async def adelete(self, namespace: tuple[str, ...], key: str) -> None:
        self.deleted.append((namespace, key))
        self.items.pop((namespace, key), None)


def _envelope(message: dict) -> str:
    """The message's envelope text, whether its content is a string or blocks."""
    content = message["content"]
    if isinstance(content, str):
        return content
    return "".join(block["text"] for block in content if block.get("type") == "text")


@pytest.mark.asyncio
async def test_check_message_queue_announces_the_move_to_web_only_once() -> None:
    store = _FakeStore(
        {
            (("queue", "thread-1"), "pending_messages"): {
                "messages": [
                    {
                        "content": {
                            "text": "and another thing",
                            "source": "dashboard",
                            "queue_id": "8a60896d-65ca-4e40-8a2d-1fbe81777002",
                            "sender": {
                                "id": "github:octocat",
                                "platform": "github",
                                "github_login": "octocat",
                            },
                        }
                    },
                ]
            }
        }
    )

    with (
        patch(
            "openswe.middleware.check_message_queue.get_config",
            return_value={"configurable": {"thread_id": "thread-1"}},
        ),
        patch("openswe.middleware.check_message_queue.get_store", return_value=store),
    ):
        result = await check_message_queue_before_model.abefore_model(
            cast(LinearNotifyState, {"messages": [], "reply_surface": "web"}),
            MagicMock(),
        )

    assert result is not None
    envelopes = [_envelope(message) for message in result["messages"]]
    assert not any("system:dashboard-handoff" in envelope for envelope in envelopes)


@pytest.mark.asyncio
async def test_check_message_queue_keeps_follow_ups_queued_while_it_builds() -> None:
    namespace_key = (("queue", "thread-1"), "pending_messages")
    first = {"content": {"text": "first", "image_urls": ["https://img.test/a.png"]}}
    second = {"content": {"text": "queued during the image fetch"}}
    store = _FakeStore({namespace_key: {"messages": [first]}})

    async def build_and_append(payload: dict[str, Any], *, model_id: str | None) -> list:
        store.items[namespace_key]["messages"].append(second)
        return [{"type": "text", "text": payload["text"]}]

    with (
        patch(
            "openswe.middleware.check_message_queue.get_config",
            return_value={"configurable": {"thread_id": "thread-1"}},
        ),
        patch("openswe.middleware.check_message_queue.get_store", return_value=store),
        patch(
            "openswe.middleware.check_message_queue._resolve_thread_model_id",
            return_value=None,
        ),
        patch(
            "openswe.middleware.check_message_queue._build_blocks_from_payload",
            side_effect=build_and_append,
        ),
    ):
        result = await check_message_queue_before_model.abefore_model(
            cast(LinearNotifyState, {"messages": []}),
            MagicMock(),
        )

    assert result is not None
    assert "first" in _envelope(result["messages"][-1])
    assert store.items[namespace_key] == {"messages": [second]}
    assert store.deleted == []
