from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from runtime_api.catalog import InboxSpec

from openswe.input_messages import RunMessage
from openswe.message_queue import QueuedMessage
from openswe.middleware.require_user_reply import ReplySurface
from openswe.threads.inbox import Taken, ThreadInbox
from openswe.utils.thread_ops import queue_system_note_for_thread


def _envelope(message: RunMessage) -> str:
    """The message's envelope text, whether its content is a string or blocks."""
    content = message["content"]
    if isinstance(content, str):
        return content
    return "".join(block["text"] for block in content if block.get("type") == "text")


async def _read(surface: ReplySurface = "web") -> Taken:
    return await ThreadInbox("thread-1").read("run-1", InboxSpec(surface=surface))


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
async def test_system_notes_reach_the_agent_without_waiting_in_the_dashboard(
    registry_db: None,
) -> None:
    from openswe.threads.handlers import get_dashboard_thread_queued_messages

    await queue_system_note_for_thread("thread-1", "Review accepted")

    with patch("openswe.threads.handlers._authorized_thread", AsyncMock()):
        assert await get_dashboard_thread_queued_messages("thread-1", "octocat") == []
    await QueuedMessage.put("thread-1", {"text": "My follow-up"})
    with patch("openswe.threads.handlers._authorized_thread", AsyncMock()):
        previews = await get_dashboard_thread_queued_messages("thread-1", "octocat")
    assert [preview.text for preview in previews] == ["My follow-up"]
    taken = await _read()
    envelope = _envelope(taken.messages[0])
    assert envelope.startswith(
        '<input-message sender="system:slack-note" surface="automation" kind="system">'
    )
    assert envelope.count("<input-message") == 1
    assert "Review accepted" in envelope
    assert "My follow-up" in _envelope(taken.messages[-1])
    await ThreadInbox("thread-1").receive(taken.queued)
    assert await QueuedMessage.for_thread("thread-1") == []


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
    await ThreadInbox("thread-1").receive(taken.queued)
    assert [message.content for message in await QueuedMessage.for_thread("thread-1")] == [
        {"text": "queued during the image fetch"}
    ]


@pytest.mark.asyncio
async def test_a_run_holds_the_events_it_was_started_with_once_prepared(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from runtime_api.loop import Prepare

    from openswe.agent_api.graph import RunSession
    from openswe.database import postgres
    from openswe.sandboxes.tool_runtime import ToolSurface
    from openswe.webhooks.event_matches import EventMatch

    carried = EventMatch(
        thread_id="thread-1", source="sandbox", delivery_id="m:1", content="ran", run_config={}
    )
    pending = EventMatch(
        thread_id="thread-1", source="sandbox", delivery_id="m:2", content="ran", run_config={}
    )
    async with postgres.session() as session:
        await carried.record(session)
        await pending.record(session)
    surface = ToolSurface(inbox=InboxSpec(events=True))
    config = {
        "configurable": {"thread_id": "thread-1"},
        "metadata": {"event_match_ids": [str(carried.id)]},
    }

    # The thread has no box here; preparing would otherwise connect to it.
    monkeypatch.setattr(RunSession, "bind_sandbox", AsyncMock())
    await RunSession(surface, config, None, started_here=True).prepare(Prepare(run_id="run-1"))

    assert [match.id for match in await EventMatch.owed("thread-1")] == [pending.id]
