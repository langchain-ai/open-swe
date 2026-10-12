from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

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
async def test_check_message_queue_keeps_follow_ups_queued_while_it_builds(
    registry_db: None,
) -> None:
    await QueuedMessage.put("thread-1", {"text": "first", "image_urls": ["https://img.test/a.png"]})

    async def build_and_queue(payload: dict[str, Any], *, model_id: str | None) -> list:
        await QueuedMessage.put("thread-1", {"text": "queued during the image fetch"})
        return [{"type": "text", "text": payload["text"]}]

    with (
        patch(
            "openswe.middleware.check_message_queue._resolve_thread_model_id",
            return_value=None,
        ),
        patch(
            "openswe.middleware.check_message_queue._build_blocks_from_payload",
            side_effect=build_and_queue,
        ),
    ):
        result = await _run({"messages": []})

    assert result is not None
    assert "first" in _envelope(result["messages"][-1])
    assert [message.content for message in await QueuedMessage.for_thread("thread-1")] == [
        {"text": "queued during the image fetch"}
    ]


@pytest.mark.parametrize("slack_enabled", [True, False])
@pytest.mark.parametrize("human_last", [True, False])
async def test_queued_relay_routes_to_slack_without_a_web_handoff(
    monkeypatch: pytest.MonkeyPatch, slack_enabled: bool, human_last: bool
) -> None:
    contents = [
        {"text": "PR is ready", "source": "dashboard", "relay_from_thread_id": "worker-thread"},
        {"text": "human follow-up", "source": "dashboard"},
    ]
    if not human_last:
        contents.reverse()
    monkeypatch.setattr(
        QueuedMessage,
        "for_thread",
        AsyncMock(
            return_value=[
                QueuedMessage(thread_id="thread-1", content=content) for content in contents
            ]
        ),
    )
    monkeypatch.setattr(QueuedMessage, "remove", AsyncMock())
    with patch(
        "openswe.middleware.check_message_queue.get_config",
        return_value={
            "configurable": {
                "thread_id": "thread-1",
                "source": "dashboard",
                "slack_thread": {"channel_id": "C123", "thread_ts": "1"} if slack_enabled else None,
            }
        },
    ):
        result = await check_message_queue_before_model.abefore_model(
            cast(LinearNotifyState, {"messages": [], "reply_surface": "web"}), MagicMock()
        )
    assert result is not None
    assert result["reply_surface"] == ("slack" if slack_enabled and not human_last else "web")
    envelopes = [_envelope(message) for message in result["messages"]]
    assert any("PR is ready" in envelope for envelope in envelopes)
    assert any("human follow-up" in envelope for envelope in envelopes)
    assert any("system:dashboard-handoff" in envelope for envelope in envelopes) is (
        slack_enabled and human_last
    )
