from typing import Any, cast
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree

import pytest

from agent.middleware.check_message_queue import (
    LinearNotifyState,
    _build_blocks_from_payload,
    check_message_queue_before_model,
)
from agent.prompt import construct_system_prompt


class _QueuedItem:
    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value


class _FakeStore:
    def __init__(self, items: dict[tuple[tuple[str, ...], str], dict[str, Any]]) -> None:
        self.items = items
        self.deleted: list[tuple[tuple[str, ...], str]] = []

    async def aget(self, namespace: tuple[str, ...], key: str) -> _QueuedItem | None:
        value = self.items.get((namespace, key))
        return _QueuedItem(value) if value is not None else None

    async def adelete(self, namespace: tuple[str, ...], key: str) -> None:
        self.deleted.append((namespace, key))


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

    slack_prompt = construct_system_prompt(
        working_dir="/workspace", source="slack", slack_context=True
    )
    with (
        patch(
            "agent.middleware.check_message_queue.get_config",
            return_value={"configurable": {"thread_id": "thread-1"}},
        ),
        patch("agent.middleware.check_message_queue.get_store", return_value=store),
    ):
        result = await check_message_queue_before_model.abefore_model(
            cast(
                LinearNotifyState,
                {"messages": [], "rendered_system_prompt": slack_prompt},
            ),
            MagicMock(),
        )

    assert result is not None
    messages = result["messages"]
    # One envelope per message: the transcript parses them individually, so a
    # concatenation would render as raw XML.
    assert [message["role"] for message in messages] == ["user"] * 4
    handoff_entity = ElementTree.fromstring(_envelope(messages[0]))
    handoff_message = ElementTree.fromstring(_envelope(messages[1]))
    user_entity = ElementTree.fromstring(_envelope(messages[2]))
    user_message = ElementTree.fromstring(_envelope(messages[3]))
    assert handoff_entity.attrib["id"] == "system:dashboard-handoff"
    assert handoff_message.attrib["kind"] == "system"
    assert "conversation has moved to Web" in (handoff_message.findtext("content") or "")
    assert user_entity.attrib["id"] == "github:octocat"
    assert user_message.findtext("content") == "continue in web"
    assert result["plan_approval_blocked"] is True
    assert "dashboard/Web UI" in result["rendered_system_prompt"]
    assert "Make `slack_thread_reply` your first tool call" not in result["rendered_system_prompt"]
    assert store.deleted == [(("queue", "thread-1"), "pending_messages")]


@pytest.mark.asyncio
async def test_check_message_queue_allows_owner_dashboard_approval() -> None:
    store = _FakeStore(
        {
            (("queue", "thread-1"), "pending_messages"): {
                "messages": [
                    {
                        "content": {
                            "text": "go ahead",
                            "source": "dashboard",
                            "from_owner": True,
                            "sender": {
                                "id": "github:owner",
                                "platform": "github",
                                "github_login": "owner",
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
            cast(LinearNotifyState, {"messages": []}), MagicMock()
        )

    assert result is not None
    assert result["plan_approval_blocked"] is False
    user_message = ElementTree.fromstring(_envelope(result["messages"][-1]))
    assert user_message.findtext("content") == "go ahead"


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
    message = result["messages"][0]
    assert message["role"] == "user"
    text = message["content"][0]["text"]
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
