"""Thread lifecycle: transcript snapshots, stable sidebar order, status polling."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.dashboard import thread_api


def _messages(*types: str) -> list[dict[str, str]]:
    return [{"type": kind, "content": kind, "id": f"{kind}-{i}"} for i, kind in enumerate(types)]


def test_transcript_window_keeps_everything_below_the_limit() -> None:
    messages = _messages("human", "ai", "tool", "human", "ai")

    window, has_more = thread_api._transcript_window(messages, turn_limit=10)

    assert window == messages
    assert has_more is False


def test_transcript_window_cuts_on_human_anchors() -> None:
    messages = _messages("human", "ai", "human", "ai", "tool", "human", "ai")

    window, has_more = thread_api._transcript_window(messages, turn_limit=2)

    assert has_more is True
    # Starts at the second-to-last human, so no tool result outlives its call.
    assert [message["type"] for message in window] == ["human", "ai", "tool", "human", "ai"]


def test_transcript_window_handles_a_transcript_with_no_human_anchor() -> None:
    messages = _messages("ai", "tool")

    window, has_more = thread_api._transcript_window(messages, turn_limit=1)

    assert window == messages
    assert has_more is False


async def test_thread_transcript_reports_unavailable_when_state_fails() -> None:
    client = SimpleNamespace(threads=SimpleNamespace(get_state=AsyncMock(side_effect=RuntimeError)))

    result = await thread_api._thread_transcript(client, "thread-1")

    assert result == {"messages": [], "hasMore": False, "available": False}


async def test_thread_transcript_returns_the_tail() -> None:
    messages = _messages("human", "ai", "human", "ai")
    client = SimpleNamespace(
        threads=SimpleNamespace(
            get_state=AsyncMock(return_value={"values": {"messages": messages}})
        )
    )

    result = await thread_api._thread_transcript(client, "thread-1")

    assert result["available"] is True
    assert result["messages"] == messages


async def test_thread_transcript_survives_a_state_without_messages() -> None:
    client = SimpleNamespace(
        threads=SimpleNamespace(get_state=AsyncMock(return_value={"values": {}}))
    )

    result = await thread_api._thread_transcript(client, "thread-1")

    assert result == {"messages": [], "hasMore": False, "available": False}


def _thread(**metadata: object) -> dict[str, object]:
    return {"thread_id": "t1", "metadata": metadata}


def test_sidebar_anchor_ignores_updated_at() -> None:
    """A run bumping updated_at must not move the row."""
    thread = _thread(created_at_ms=100, updated_at_ms=9_000)

    assert thread_api._thread_sidebar_anchor_ms(thread) == 100


def test_sidebar_anchor_re_anchors_on_a_user_message() -> None:
    thread = _thread(created_at_ms=100, last_user_message_at_ms=500, updated_at_ms=9_000)

    assert thread_api._thread_sidebar_anchor_ms(thread) == 500


def test_sidebar_anchor_keeps_creation_when_it_is_the_later_of_the_two() -> None:
    thread = _thread(created_at_ms=900, last_user_message_at_ms=100)

    assert thread_api._thread_sidebar_anchor_ms(thread) == 900


def test_sidebar_anchor_tolerates_missing_metadata() -> None:
    assert thread_api._thread_sidebar_anchor_ms(_thread()) == 0


async def test_thread_statuses_skips_threads_the_caller_cannot_read(monkeypatch) -> None:
    readable = {"source": "dashboard", "latest_run_status": "success"}
    hidden = {"source": "reviewer"}

    async def get(thread_id: str) -> dict[str, object]:
        return {
            "thread_id": thread_id,
            "status": "idle",
            "metadata": readable if thread_id == "visible" else hidden,
        }

    async def refresh(_client: object, thread: dict[str, object]):
        return thread, "success", "run-1"

    monkeypatch.setattr(
        thread_api,
        "langgraph_client",
        lambda: SimpleNamespace(threads=SimpleNamespace(get=get)),
    )
    monkeypatch.setattr(thread_api, "_refresh_latest_run_metadata", refresh)

    result = await thread_api.get_dashboard_thread_statuses(["visible", "hidden"])

    assert [row["id"] for row in result["threads"]] == ["visible"]


async def test_thread_statuses_drops_a_thread_that_cannot_be_loaded(monkeypatch) -> None:
    async def get(thread_id: str) -> dict[str, object]:
        raise RuntimeError("gone")

    monkeypatch.setattr(
        thread_api,
        "langgraph_client",
        lambda: SimpleNamespace(threads=SimpleNamespace(get=get)),
    )

    result = await thread_api.get_dashboard_thread_statuses(["missing"])

    assert result == {"threads": []}


@pytest.mark.parametrize("thread_ids", [[], ["a", "b", "c"]])
async def test_thread_statuses_returns_a_row_per_readable_id(monkeypatch, thread_ids) -> None:
    async def get(thread_id: str) -> dict[str, object]:
        return {"thread_id": thread_id, "status": "busy", "metadata": {"source": "dashboard"}}

    async def refresh(_client: object, thread: dict[str, object]):
        return thread, "running", "run-1"

    monkeypatch.setattr(
        thread_api,
        "langgraph_client",
        lambda: SimpleNamespace(threads=SimpleNamespace(get=get)),
    )
    monkeypatch.setattr(thread_api, "_refresh_latest_run_metadata", refresh)

    result = await thread_api.get_dashboard_thread_statuses(thread_ids)

    assert [row["id"] for row in result["threads"]] == thread_ids
    assert all(row["status"] == "running" for row in result["threads"])


async def test_detail_skips_the_transcript_read_when_the_caller_opts_out(monkeypatch) -> None:
    """The run-status heartbeat must not re-read state every few seconds."""
    get_state = AsyncMock(return_value={"values": {"messages": _messages("human")}})
    thread = {"thread_id": "t1", "status": "idle", "metadata": {"source": "dashboard"}}

    async def refresh(_client: object, value: dict[str, object]):
        return value, "success", "run-1"

    monkeypatch.setattr(
        thread_api,
        "langgraph_client",
        lambda: SimpleNamespace(
            threads=SimpleNamespace(get=AsyncMock(return_value=thread), get_state=get_state)
        ),
    )
    monkeypatch.setattr(thread_api, "_refresh_latest_run_metadata", refresh)
    monkeypatch.setattr(thread_api, "get_langsmith_trace_url", AsyncMock(return_value=None))

    without = await thread_api.get_dashboard_thread(
        "t1", "someone", mark_viewed=False, include_transcript=False
    )
    assert "transcript" not in without
    assert get_state.await_count == 0

    with_transcript = await thread_api.get_dashboard_thread(
        "t1", "someone", mark_viewed=False, include_transcript=True
    )
    assert with_transcript["transcript"]["available"] is True
    assert get_state.await_count == 1


async def test_detail_carries_the_stable_sort_anchor(monkeypatch) -> None:
    thread = {
        "thread_id": "t1",
        "status": "idle",
        "metadata": {
            "source": "dashboard",
            "created_at_ms": 100,
            "updated_at_ms": 9_000,
        },
    }

    async def refresh(_client: object, value: dict[str, object]):
        return value, "success", "run-1"

    monkeypatch.setattr(
        thread_api,
        "langgraph_client",
        lambda: SimpleNamespace(
            threads=SimpleNamespace(get=AsyncMock(return_value=thread), get_state=AsyncMock())
        ),
    )
    monkeypatch.setattr(thread_api, "_refresh_latest_run_metadata", refresh)
    monkeypatch.setattr(thread_api, "get_langsmith_trace_url", AsyncMock(return_value=None))

    summary = await thread_api.get_dashboard_thread(
        "t1", "someone", mark_viewed=False, include_transcript=False
    )

    assert summary["sortAnchorAt"] == 100
    assert summary["updatedAt"] == 9_000
