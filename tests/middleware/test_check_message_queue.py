from copy import deepcopy
from typing import Any, cast
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree

import pytest

from agent.middleware.check_message_queue import (
    LinearNotifyState,
    _build_blocks_from_payload,
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
async def test_check_message_queue_injects_dashboard_handoff_instruction() -> None:
    store = _FakeStore(
        {
            (("queue", "thread-1"), "pending_messages"): {
                "messages": [
                    {
                        "content": {
                            "text": "continue in web",
                            "source": "dashboard",
                            "queue_id": "8a60896d-65ca-4e40-8a2d-1fbe81777001",
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
            "agent.middleware.check_message_queue.get_config",
            return_value={"configurable": {"thread_id": "thread-1"}},
        ),
        patch("agent.middleware.check_message_queue.get_store", return_value=store),
    ):
        result = await check_message_queue_before_model.abefore_model(
            cast(LinearNotifyState, {"messages": [], "reply_surface": "slack"}),
            MagicMock(),
        )

    assert result is not None
    assert result["reply_surface"] == "web"
    messages = result["messages"]
    # One envelope per message: the transcript parses them individually, so a
    # concatenation would render as raw XML.
    assert [message["role"] for message in messages] == ["user"] * 3
    handoff_entity = ElementTree.fromstring(_envelope(messages[0]))
    handoff_message = ElementTree.fromstring(_envelope(messages[1]))
    user_message = ElementTree.fromstring(_envelope(messages[2]))
    assert handoff_entity.attrib["id"] == "system:dashboard-handoff"
    assert handoff_message.attrib["kind"] == "system"
    assert "conversation has moved to Web" in (handoff_message.text or "")
    assert user_message.attrib["sender"] == "github:octocat"
    assert (user_message.text or "").strip() == "continue in web"
    assert messages[2]["id"] == "8a60896d-65ca-4e40-8a2d-1fbe81777001"
    # The handoff is carried by the injected message alone. Rewriting the system
    # prompt would say the same thing while invalidating the whole cached prefix.
    assert "rendered_system_prompt" not in result
    assert store.deleted == [(("queue", "thread-1"), "pending_messages")]


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
            "agent.middleware.check_message_queue.get_config",
            return_value={"configurable": {"thread_id": "thread-1"}},
        ),
        patch("agent.middleware.check_message_queue.get_store", return_value=store),
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
            "agent.middleware.check_message_queue.get_config",
            return_value={"configurable": {"thread_id": "thread-1"}},
        ),
        patch("agent.middleware.check_message_queue.get_store", return_value=store),
        patch(
            "agent.middleware.check_message_queue._resolve_thread_model_id",
            return_value=None,
        ),
        patch(
            "agent.middleware.check_message_queue._build_blocks_from_payload",
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


@pytest.mark.asyncio
async def test_check_message_queue_injects_pending_autofix_event() -> None:
    store = _FakeStore(
        {
            (("autofix", "thread-1"), "pending_event"): {
                "reason": "review_feedback",
                "details": ["Reviewer alice commented: rename to userId"],
            }
        }
    )

    with (
        patch(
            "agent.middleware.check_message_queue.get_config",
            return_value={"configurable": {"thread_id": "thread-1"}},
        ),
        patch("agent.middleware.check_message_queue.get_store", return_value=store),
    ):
        result = await check_message_queue_before_model.abefore_model(
            cast(LinearNotifyState, {"messages": []}), MagicMock()
        )

    assert result is not None
    entity = ElementTree.fromstring(_envelope(result["messages"][0]))
    message = ElementTree.fromstring(_envelope(result["messages"][-1]))
    assert entity.attrib["id"] == "system:thread-queue"
    assert message.attrib["kind"] == "system"
    text = message.text or ""
    assert "PR babysitting event arrived" in text
    # The reviewer's actual comment is carried through, not dropped for a generic nudge.
    assert "rename to userId" in text
    assert (("autofix", "thread-1"), "pending_event") in store.deleted


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
    blocks = await _build_blocks_from_payload(payload, model_id="openai:gpt-5.6-sol")
    assert blocks == [{"type": "text", "text": "see this"}]


@pytest.mark.asyncio
async def test_build_blocks_no_model_check_fetches_images() -> None:
    payload: dict[str, Any] = {"text": "see this", "image_urls": []}
    blocks = await _build_blocks_from_payload(payload)
    assert blocks == [{"type": "text", "text": "see this"}]
