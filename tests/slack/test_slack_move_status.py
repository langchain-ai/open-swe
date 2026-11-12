import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openswe import event_claims
from openswe.slack import move as slack_move
from openswe.slack import routed_work
from openswe.slack import thinking as slack_thinking
from openswe.slack.channels import SlackChannel
from openswe.slack.records import lookup_slack_thread_id
from openswe.slack.tools import move_thread
from openswe.source_context import SlackThreadRef

StatusCall = tuple[str, str, str]


class _Threads:
    def __init__(self, metadata: dict[str, object]) -> None:
        self.metadata = metadata
        self.locks: set[str] = set()
        self.updates = 0
        self.reads_fail_after_first_update = False
        self.failing_reads = 0
        self.max_updates: int | None = None

    async def get(self, thread_id: str) -> dict[str, object]:  # noqa: ARG002
        if self.failing_reads:
            self.failing_reads -= 1
            raise RuntimeError("read timed out")
        if self.reads_fail_after_first_update and self.updates:
            raise RuntimeError("read timed out")
        return {"metadata": dict(self.metadata)}

    async def update(self, *, thread_id: str, metadata: dict[str, object]) -> None:  # noqa: ARG002
        if self.max_updates is not None and self.updates >= self.max_updates:
            raise RuntimeError("update timed out")
        self.updates += 1
        self.metadata.update(metadata)

    async def create(self, *, thread_id: str, if_exists: str, ttl: int) -> None:  # noqa: ARG002
        self.locks.add(thread_id)

    async def delete(self, thread_id: str) -> None:
        self.locks.discard(thread_id)


class _Runs:
    def __init__(self) -> None:
        self.finished = asyncio.Event()

    async def join(self, thread_id: str, run_id: str) -> dict[str, object]:  # noqa: ARG002
        await self.finished.wait()
        return {}

    async def list(self, thread_id: str, *, status: str, limit: int) -> list[dict[str, str]]:  # noqa: ARG002
        return [] if self.finished.is_set() else [{"run_id": "run-1"}]


class _LangGraph:
    def __init__(self, slack_thread: dict[str, str]) -> None:
        self.threads = _Threads(
            {"source": "slack", "source_context": {"slack_thread": slack_thread}}
        )
        self.runs = _Runs()
        _INDEXED.append(self)


# The thread index (``THREADS``) and runtime (``RUNTIMES``) the backend uses: the newest fake's.
_INDEXED: list[_LangGraph] = []


pytestmark = pytest.mark.usefixtures("registry_db")


@pytest.fixture(autouse=True)
def unclaimed_locks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(event_claims, "claim", AsyncMock(return_value=True))
    monkeypatch.setattr(event_claims, "release", AsyncMock())


@pytest.fixture
def statuses(monkeypatch: pytest.MonkeyPatch) -> list[StatusCall]:
    calls: list[StatusCall] = []

    async def set_status(channel_id: str, thread_ts: str, status: str) -> bool:
        calls.append((channel_id, thread_ts, status))
        return True

    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)
    monkeypatch.setattr(slack_thinking, "_STATUS_REFRESH_SECONDS", 0.001)
    monkeypatch.setattr(slack_thinking, "_STATUS_RETRY_DELAYS", (0.0, 0.0))
    _INDEXED.clear()

    async def indexed(thread_id: str) -> SimpleNamespace | None:
        if not _INDEXED:
            return None
        thread = await _INDEXED[-1].threads.get(thread_id)
        return SimpleNamespace(thread_id=thread_id, runtime="mda", metadata=thread["metadata"])

    async def update(thread_id: str, metadata: dict[str, object]) -> None:
        await _INDEXED[-1].threads.update(thread_id=thread_id, metadata=metadata)

    async def has_active_run(thread_id: str) -> bool:
        runs = _INDEXED[-1].runs
        return bool(await runs.list(thread_id, status="running", limit=1))

    monkeypatch.setattr(routed_work.THREADS, "get", indexed)
    monkeypatch.setattr(routed_work.THREADS, "update", update)
    monkeypatch.setattr(
        type(routed_work.RUNTIMES),
        "runs_on",
        lambda _self, _runtime: SimpleNamespace(has_active_run=has_active_run),
    )
    return calls


async def _ticks(n: int = 20) -> None:
    for _ in range(n):
        await asyncio.sleep(0.002)


async def test_breakout_mid_run_moves_the_working_status(
    monkeypatch: pytest.MonkeyPatch, statuses: list[StatusCall]
) -> None:
    async def post_root(*_args: object, **_kwargs: object) -> str:
        return "2.0"

    monkeypatch.setattr(slack_move, "post_slack_top_level_message_with_ts", post_root)
    client = _LangGraph({"channel_id": "C1", "thread_ts": "1.0"})
    await slack_move.bind_slack_thread_id("C1", "1.0", "thread-1")

    async with asyncio.timeout(2):
        watcher = asyncio.create_task(
            slack_thinking.show_slack_thinking_status(
                client=client,
                thread_id="thread-1",
                run_id="run-1",
                channel_id="C1",
                thread_ts="1.0",
            )
        )
        await _ticks()
        result = await slack_move.move_slack_thread(
            "thread-1", {"channel_id": "C1", "thread_ts": "1.0"}, "C2", "Breakout"
        )
        moved_at = len(statuses)
        await _ticks()
        client.runs.finished.set()
        await watcher

    assert result["success"] is True
    after_move = statuses[moved_at:]
    assert ("C1", "1.0", "") in statuses[:moved_at]
    assert ("C2", "2.0", "Thinking...") in statuses[:moved_at]
    assert all(channel_id == "C2" for channel_id, _, _ in after_move)
    assert ("C2", "2.0", "Thinking...") in after_move
    assert statuses[-1] == ("C2", "2.0", "")


async def test_unverifiable_rebind_reverts_before_releasing_the_destination(
    statuses: list[StatusCall],  # noqa: ARG001
) -> None:
    client = _LangGraph({"channel_id": "C1", "thread_ts": "1.0"})
    await slack_move.bind_slack_thread_id("C1", "1.0", "thread-1")
    client.threads.reads_fail_after_first_update = True

    with pytest.raises(slack_move.SlackRebindError) as failure:
        await slack_move.rebind_slack_thread(
            "thread-1",
            SlackThreadRef(channel_id="C1", thread_ts="1.0"),
            SlackThreadRef(channel_id="C0", thread_ts="0"),
        )

    assert failure.value.moved is False
    assert client.threads.metadata["source_context"] == {
        "slack_thread": {"channel_id": "C1", "thread_ts": "1.0"}
    }
    assert await lookup_slack_thread_id("C1", "1.0") == "thread-1"
    assert await lookup_slack_thread_id("C0", "0") is None


async def test_unrevertable_rebind_keeps_the_destination(
    statuses: list[StatusCall],  # noqa: ARG001
) -> None:
    client = _LangGraph({"channel_id": "C1", "thread_ts": "1.0"})
    await slack_move.bind_slack_thread_id("C1", "1.0", "thread-1")
    client.threads.reads_fail_after_first_update = True
    client.threads.max_updates = 1

    with pytest.raises(slack_move.SlackRebindError) as failure:
        await slack_move.rebind_slack_thread(
            "thread-1",
            SlackThreadRef(channel_id="C1", thread_ts="1.0"),
            SlackThreadRef(channel_id="C0", thread_ts="0"),
        )

    assert failure.value.moved is True
    assert await lookup_slack_thread_id("C0", "0") == "thread-1"


async def test_unreadable_binding_after_a_breakout_leaves_the_old_thread_alone(
    monkeypatch: pytest.MonkeyPatch, statuses: list[StatusCall]
) -> None:
    async def post_root(*_args: object, **_kwargs: object) -> str:
        return "2.0"

    monkeypatch.setattr(slack_move, "post_slack_top_level_message_with_ts", post_root)
    client = _LangGraph({"channel_id": "C1", "thread_ts": "1.0"})
    await slack_move.bind_slack_thread_id("C1", "1.0", "thread-1")

    async with asyncio.timeout(2):
        watcher = asyncio.create_task(
            slack_thinking.show_slack_thinking_status(
                client=client,
                thread_id="thread-1",
                run_id="run-1",
                channel_id="C1",
                thread_ts="1.0",
            )
        )
        await _ticks()
        await slack_move.move_slack_thread(
            "thread-1", {"channel_id": "C1", "thread_ts": "1.0"}, "C2", "Breakout"
        )
        moved_at = len(statuses)
        client.threads.failing_reads = 5
        await _ticks()
        client.threads.failing_reads = 2
        client.runs.finished.set()
        await watcher

    assert ("C1", "1.0", "Thinking...") not in statuses[moved_at:]
    assert statuses[-1] == ("C2", "2.0", "")


@pytest.mark.parametrize("explicit, target", [("C1", "C1"), ("C3", "C3"), ("D1", "D1")])
async def test_move_tool_uses_current_or_explicit_channel_without_moving_twice(
    monkeypatch: pytest.MonkeyPatch, statuses: list[StatusCall], explicit: str, target: str
) -> None:
    source = "D1" if target == "D1" else "C1"
    _LangGraph({"channel_id": source, "thread_ts": "1.0"})
    config = {
        "configurable": {
            "thread_id": "thread-1",
            "workspace": "engineering",
            "slack_thread": {"channel_id": source, "thread_ts": "1.0"},
        }
    }
    monkeypatch.setattr(move_thread, "get_config", lambda: config)
    monkeypatch.setattr("openswe.run_config.get_config", lambda: config)
    monkeypatch.setattr(
        SlackChannel, "load", AsyncMock(return_value=SimpleNamespace(public=target != source))
    )
    root = AsyncMock(return_value="2.0")
    monkeypatch.setattr(slack_move, "post_slack_top_level_message_with_ts", root)
    await slack_move.bind_slack_thread_id(source, "1.0", "thread-1")

    result = await move_thread.slack_move_thread("Breakout", explicit)
    assert result["success"] is True
    assert result["channel_id"] == target
    assert await lookup_slack_thread_id(target, "2.0") == "thread-1"
    assert root.await_args is not None
    assert root.await_args.args[0] == target

    repeated = await move_thread.slack_move_thread("Breakout", "C4")
    assert repeated["channel_id"] == target
    root.assert_awaited_once()
