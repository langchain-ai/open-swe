from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agent.middleware.check_message_queue import (
    DASHBOARD_HANDOFF_MARKER,
    _queued_configurable_update,
    _select_queued_messages,
    check_message_queue_before_model,
)


def test_queued_configurable_update_keeps_only_allowed_reviewer_reply_fields() -> None:
    update = _queued_configurable_update(
        {
            "configurable": {
                "reviewer_event": "finding_reply",
                "finding_reply_id": "f_1",
                "finding_reply_allow_prompt_learning": True,
                "repo": {"owner": "acme", "name": "repo", "extra": "ignored"},
                "thread_id": "should-not-change",
            }
        }
    )

    assert update == {
        "reviewer_event": "finding_reply",
        "finding_reply_id": "f_1",
        "finding_reply_allow_prompt_learning": True,
        "repo": {"owner": "acme", "name": "repo"},
    }


def test_select_queued_messages_processes_configurable_message_by_itself() -> None:
    plain_message = {"content": "plain"}
    configurable_message = {
        "content": "reply",
        "configurable": {
            "reviewer_event": "finding_reply",
            "finding_reply_allow_prompt_learning": True,
        },
    }

    selected, remaining, update = _select_queued_messages([plain_message, configurable_message])
    assert selected == [plain_message]
    assert remaining == [configurable_message]
    assert update == {}

    selected, remaining, update = _select_queued_messages([configurable_message, plain_message])
    assert selected == [configurable_message]
    assert remaining == [plain_message]
    assert update == {
        "reviewer_event": "finding_reply",
        "finding_reply_allow_prompt_learning": True,
    }


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
    ):
        result = await check_message_queue_before_model.abefore_model({}, MagicMock())

    assert result is not None
    message = result["messages"][0]
    assert message["role"] == "user"
    assert DASHBOARD_HANDOFF_MARKER in message["content"][0]["text"]
    assert message["content"][1] == {"type": "text", "text": "continue in web"}
    assert store.deleted == [(("queue", "thread-1"), "pending_messages")]
