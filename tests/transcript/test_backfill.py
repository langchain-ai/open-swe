"""Upgrading a legacy thread: its LangGraph history folded into a transcript, once."""

from typing import Any
from uuid import uuid7

import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from openswe.transcript import backfill, routes, tool_output
from openswe.transcript.backfill import ThreadBusy, upgrade_transcript
from openswe.transcript.snapshot import load_access, load_snapshot

OWNER = "test-user"


class FakeThreads:
    """One LangGraph thread: its metadata, status and checkpointed messages."""

    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id
        self.metadata: dict[str, object] = {
            "owner_login": OWNER,
            "source": "dashboard",
            "visibility": "public",
            "title": "Imported session",
        }
        self.status = "idle"
        self.checkpoint = "cp-1"
        self.messages = [
            HumanMessage("fix the bug", id="h1"),
            AIMessage(
                "Looking",
                id="a1",
                tool_calls=[{"name": "read_file", "args": {"path": "a.py"}, "id": "c1"}],
            ),
            ToolMessage("contents", tool_call_id="c1", id="t1"),
            AIMessage("Fixed it", id="a2"),
            HumanMessage("thanks", id="h2"),
            AIMessage("Anytime", id="a3"),
        ]
        self.fail_stamp = False

    async def get(self, thread_id: str) -> dict[str, Any]:
        assert thread_id == self.thread_id
        return {
            "thread_id": thread_id,
            "status": self.status,
            "metadata": dict(self.metadata),
            "created_at": "2026-01-01T00:00:00+00:00",
        }

    async def get_state(self, thread_id: str) -> dict[str, Any]:
        assert thread_id == self.thread_id
        return {
            "values": {"messages": [message.model_dump() for message in self.messages]},
            "checkpoint": {"checkpoint_id": self.checkpoint},
        }

    async def update(self, thread_id: str, *, metadata: dict[str, object]) -> None:
        if self.fail_stamp:
            raise RuntimeError("LangGraph unavailable")
        self.metadata.update(metadata)


@pytest.fixture
def threads(monkeypatch: pytest.MonkeyPatch) -> FakeThreads:
    fake = FakeThreads(str(uuid7()))

    class Client:
        threads = fake

    monkeypatch.setattr(backfill, "langgraph_client", Client)
    monkeypatch.setattr(routes, "langgraph_client", Client)
    return fake


async def test_a_legacy_thread_is_folded_into_turns_and_stamped_once(
    registry_db: None, threads: FakeThreads
) -> None:
    threads.fail_stamp = True
    with pytest.raises(RuntimeError):
        await upgrade_transcript(threads.thread_id)
    assert "transcript" not in threads.metadata

    # The retry reuses the committed log instead of folding a second copy.
    threads.fail_stamp = False
    progress: list[int] = []
    await upgrade_transcript(threads.thread_id, lambda update: progress.append(update.done))
    assert threads.metadata["transcript"] == "v2"
    assert progress == []

    snapshot = await load_snapshot(threads.thread_id)
    assert snapshot is not None
    assert snapshot.thread.status == "idle"
    assert [turn.state for turn in snapshot.turns] == ["completed", "completed"]
    assert [(m.message_id, m.role) for m in snapshot.messages] == [
        ("h1", "human"),
        ("a1", "ai"),
        ("a2", "ai"),
        ("h2", "human"),
        ("a3", "ai"),
    ]
    assert snapshot.messages[0].sender is not None
    assert snapshot.messages[0].sender["login"] == OWNER
    [call] = snapshot.tool_calls
    assert (call.message_id, call.status, call.has_output) == ("a1", "completed", True)
    assert await tool_output.load(threads.thread_id, "c1") == "contents"
    access = await load_access(threads.thread_id)
    assert access is not None and access["transcript"] == "v2"

    version = snapshot.version
    await upgrade_transcript(threads.thread_id)
    after = await load_snapshot(threads.thread_id)
    assert after is not None and after.version == version


async def test_a_log_from_an_older_checkpoint_is_folded_again(
    registry_db: None, threads: FakeThreads
) -> None:
    threads.fail_stamp = True
    with pytest.raises(RuntimeError):
        await upgrade_transcript(threads.thread_id)
    threads.fail_stamp = False
    threads.messages.append(HumanMessage("one more", id="h3"))
    threads.checkpoint = "cp-2"

    progress: list[int] = []
    await upgrade_transcript(threads.thread_id, lambda update: progress.append(update.done))

    assert progress[-1] > 0
    snapshot = await load_snapshot(threads.thread_id)
    assert snapshot is not None
    assert [m.message_id for m in snapshot.messages if m.role == "human"] == ["h1", "h2", "h3"]


async def test_a_thread_with_a_live_run_is_left_alone(
    registry_db: None, threads: FakeThreads
) -> None:
    threads.status = "busy"
    with pytest.raises(ThreadBusy):
        await upgrade_transcript(threads.thread_id)
    assert "transcript" not in threads.metadata
    assert await load_snapshot(threads.thread_id) is None


async def test_only_the_owner_may_upgrade_a_thread_others_can_read(
    registry_db: None, threads: FakeThreads, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def opted_in(login: str) -> dict[str, object]:
        return {"experimental_transcript_upgrade": True}

    monkeypatch.setattr(routes, "get_profile", opted_in)
    with pytest.raises(HTTPException) as refused:
        await routes.api_upgrade_thread_transcript(threads.thread_id, {"sub": "trusted-user"})
    assert refused.value.status_code == 403
    assert "transcript" not in threads.metadata
    assert await load_snapshot(threads.thread_id) is None
