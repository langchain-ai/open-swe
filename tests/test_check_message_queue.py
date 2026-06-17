from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.middleware.check_message_queue import (
    DASHBOARD_HANDOFF_MARKER,
    _build_blocks_from_payload,
    check_message_queue_before_model,
)


class _QueuedItem:
    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value


class _FakeStore:
    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value
        self.deleted: list[tuple[tuple[str, ...], str]] = []

    async def aget(self, namespace: tuple[str, ...], key: str) -> _QueuedItem:
        return _QueuedItem(self.value)

    async def adelete(self, namespace: tuple[str, ...], key: str) -> None:
        self.deleted.append((namespace, key))


@pytest.mark.asyncio
async def test_check_message_queue_injects_dashboard_handoff_instruction() -> None:
    store = _FakeStore(
        {
            "messages": [
                {"content": {"text": "continue in web", "source": "dashboard"}},
            ]
        }
    )

    with (
        patch(
            "agent.middleware.check_message_queue.get_config",
            return_value={"configurable": {"thread_id": "thread-1"}},
        ),
        patch("agent.middleware.check_message_queue.get_store", return_value=store),
        patch(
            "agent.middleware.check_message_queue.get_client", side_effect=Exception("no client")
        ),
    ):
        result = await check_message_queue_before_model.abefore_model({}, MagicMock())

    assert result is not None
    message = result["messages"][0]
    assert message["role"] == "user"
    assert DASHBOARD_HANDOFF_MARKER in message["content"][0]["text"]
    assert message["content"][1] == {"type": "text", "text": "continue in web"}
    assert store.deleted == [(("queue", "thread-1"), "pending_messages")]


@pytest.mark.asyncio
async def test_check_message_queue_injects_pending_autofix_event() -> None:
    client = MagicMock()
    client.threads.get = AsyncMock(
        return_value={"metadata": {"autofix_pending_event": "ci_failure"}}
    )
    client.threads.update = AsyncMock()

    with (
        patch(
            "agent.middleware.check_message_queue.get_config",
            return_value={"configurable": {"thread_id": "thread-1"}},
        ),
        patch("agent.middleware.check_message_queue.get_store", return_value=None),
        patch("agent.middleware.check_message_queue.get_client", return_value=client),
    ):
        result = await check_message_queue_before_model.abefore_model({}, MagicMock())

    assert result is not None
    message = result["messages"][0]
    assert message["role"] == "user"
    assert "PR babysitting event arrived" in message["content"][0]["text"]
    client.threads.update.assert_awaited_once_with(
        thread_id="thread-1",
        metadata={"autofix_pending_event": "", "autofix_pending_event_at_ms": None},
    )


@pytest.mark.asyncio
async def test_build_blocks_skips_images_for_text_only_model() -> None:
    payload = {
        "text": "see this screenshot",
        "image_urls": ["https://files.slack.com/fake.png"],
    }
    blocks = await _build_blocks_from_payload(
        payload, model_id="fireworks:accounts/fireworks/models/glm-5p2"
    )
    assert len(blocks) == 1
    assert blocks[0]["type"] == "text"
    assert "does not support image input" in blocks[0]["text"]


@pytest.mark.asyncio
async def test_build_blocks_includes_images_for_vision_model() -> None:
    payload: dict[str, Any] = {"text": "see this", "image_urls": []}
    blocks = await _build_blocks_from_payload(payload, model_id="openai:gpt-5.5")
    assert blocks == [{"type": "text", "text": "see this"}]


@pytest.mark.asyncio
async def test_build_blocks_no_model_check_fetches_images() -> None:
    payload: dict[str, Any] = {"text": "see this", "image_urls": []}
    blocks = await _build_blocks_from_payload(payload)
    assert blocks == [{"type": "text", "text": "see this"}]
