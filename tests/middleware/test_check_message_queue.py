from typing import Any, cast
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree

import pytest

from agent.media import MediaRef, media_refs_from_content
from agent.middleware.check_message_queue import (
    LinearNotifyState,
    check_message_queue_before_model,
)
from agent.utils.thread_ops import QueuedMessage


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

    with (
        patch(
            "agent.run_config.get_config",
            return_value={"configurable": {"thread_id": "thread-1"}},
        ),
        patch("agent.middleware.check_message_queue.get_store", return_value=store),
    ):
        result = await check_message_queue_before_model.abefore_model(
            cast(LinearNotifyState, {"messages": []}),
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
    # The handoff is carried by the injected message alone. Rewriting the system
    # prompt would say the same thing while invalidating the whole cached prefix.
    assert "rendered_system_prompt" not in result
    assert store.deleted == [(("queue", "thread-1"), "pending_messages")]


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
            "agent.run_config.get_config",
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
    text = message.findtext("content") or ""
    assert "PR babysitting event arrived" in text
    # The reviewer's actual comment is carried through, not dropped for a generic nudge.
    assert "rename to userId" in text
    assert (("autofix", "thread-1"), "pending_event") in store.deleted


_REF = MediaRef(
    path="/workspace/.open-swe-media/" + "a" * 64 + ".png",
    mime_type="image/png",
    sha256="a" * 64,
    size=3,
)


async def _inject(store: _FakeStore) -> list[dict[str, Any]]:
    with (
        patch(
            "agent.run_config.get_config",
            return_value={"configurable": {"thread_id": "thread-1"}},
        ),
        patch("agent.middleware.check_message_queue.get_store", return_value=store),
    ):
        result = await check_message_queue_before_model.abefore_model(
            cast(LinearNotifyState, {"messages": []}), MagicMock()
        )
    assert result is not None
    return result["messages"]


@pytest.mark.asyncio
async def test_queued_dashboard_message_carries_media_refs_in_its_envelope() -> None:
    queued = QueuedMessage(
        text="see this screenshot",
        sender={"id": "github:octocat", "platform": "github"},  # type: ignore[arg-type]
        media=[_REF],
    )
    store = _FakeStore(
        {(("queue", "thread-1"), "pending_messages"): {"messages": [{"content": queued.dump()}]}}
    )

    messages = await _inject(store)

    user_message = messages[-1]
    assert isinstance(user_message["content"], str)
    assert ElementTree.fromstring(user_message["content"]).findtext("content") == (
        "see this screenshot"
    )
    assert media_refs_from_content(user_message["content"]) == [_REF]


@pytest.mark.asyncio
async def test_unattributed_queued_messages_share_one_envelope_with_their_media() -> None:
    store = _FakeStore(
        {
            (("queue", "thread-1"), "pending_messages"): {
                "messages": [
                    {"content": "first edit"},
                    {"content": QueuedMessage(text="second edit", media=[_REF]).dump()},
                ]
            }
        }
    )

    messages = await _inject(store)

    notice = ElementTree.fromstring(_envelope(messages[-1]))
    assert notice.attrib["sender"] == "system:thread-queue"
    assert notice.findtext("content") == "first edit\n\nsecond edit"
    assert media_refs_from_content(messages[-1]["content"]) == [_REF]


def test_queued_message_parse_drops_unusable_payloads() -> None:
    assert QueuedMessage.parse("") is None
    assert QueuedMessage.parse(["legacy", "blocks"]) is None
    assert QueuedMessage.parse({"text": "hi", "media": [{"path": "broken"}]}) is None
    parsed = QueuedMessage.parse({"text": "hi", "images": [{"base64": "x"}]})
    assert parsed is not None
    assert (parsed.text, parsed.media, parsed.sender) == ("hi", [], None)
