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
@pytest.mark.parametrize("delegated", [False, True])
async def test_check_message_queue_announces_the_move_to_web_only_once(
    registry_db: None, delegated: bool
) -> None:
    await QueuedMessage.put(
        "thread-1",
        {
            "text": "and another thing",
            "source": "dashboard",
            "queue_id": "8a60896d-65ca-4e40-8a2d-1fbe81777002",
            **(
                {
                    "author": {
                        "id": "system:concierge-github:octocat",
                        "display_name": "Concierge on behalf of Octocat",
                        "sender_type": "bot",
                    }
                }
                if delegated
                else {}
            ),
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
    assert ('kind="system"' if delegated else 'kind="human"') in envelopes[-1]
    assert ('on_behalf_of="github:octocat"' in envelopes[-1]) is delegated


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
