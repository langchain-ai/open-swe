import asyncio

import pytest

from agent.slack import move as slack_move
from agent.slack import thinking as slack_thinking
from agent.source_context import SlackThreadRef

StatusCall = tuple[str, str, str]


class _Store:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, object]] = {}

    async def get_item(self, namespace: tuple[str, ...], key: str) -> dict[str, object] | None:
        value = self.items.get((tuple(namespace), key))
        return {"value": dict(value)} if value is not None else None

    async def put_item(
        self, namespace: tuple[str, ...], key: str, value: dict[str, object]
    ) -> None:
        self.items[(tuple(namespace), key)] = dict(value)

    async def delete_item(self, namespace: tuple[str, ...], key: str) -> None:
        self.items.pop((tuple(namespace), key), None)

    async def search_items(
        self,
        namespace: tuple[str, ...],
        *,
        filter: dict[str, object],  # noqa: A002
        limit: int,
        offset: int,
    ) -> dict[str, object]:
        matches = [
            {"namespace": list(ns), "key": key, "value": value}
            for (ns, key), value in self.items.items()
            if ns == tuple(namespace) and all(value.get(k) == v for k, v in filter.items())
        ]
        return {"items": matches[offset : offset + limit]}


class _Threads:
    def __init__(self, metadata: dict[str, object]) -> None:
        self.metadata = metadata
        self.locks: set[str] = set()

    async def get(self, thread_id: str) -> dict[str, object]:  # noqa: ARG002
        return {"metadata": dict(self.metadata)}

    async def update(self, *, thread_id: str, metadata: dict[str, object]) -> None:  # noqa: ARG002
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
        self.store = _Store()
        self.threads = _Threads(
            {"source": "slack", "source_context": {"slack_thread": slack_thread}}
        )
        self.runs = _Runs()


@pytest.fixture
def statuses(monkeypatch: pytest.MonkeyPatch) -> list[StatusCall]:
    calls: list[StatusCall] = []

    async def set_status(channel_id: str, thread_ts: str, status: str) -> bool:
        calls.append((channel_id, thread_ts, status))
        return True

    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)
    monkeypatch.setattr(slack_thinking, "_STATUS_REFRESH_SECONDS", 0.001)
    monkeypatch.setattr(slack_thinking, "_STATUS_RETRY_DELAYS", (0.0, 0.0))
    return calls


async def _ticks(n: int = 20) -> None:
    for _ in range(n):
        await asyncio.sleep(0.002)


async def test_breakout_mid_run_moves_the_working_status(
    monkeypatch: pytest.MonkeyPatch, statuses: list[StatusCall]
) -> None:
    async def post_root(*_args: object, **_kwargs: object) -> tuple[str, None]:
        return "2.0", None

    monkeypatch.setattr(slack_move, "post_slack_top_level_message_with_ts", post_root)
    client = _LangGraph({"channel_id": "C1", "thread_ts": "1.0"})
    await slack_move.bind_slack_thread_id(client, "C1", "1.0", "thread-1")

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
            client, "thread-1", {"channel_id": "C1", "thread_ts": "1.0"}, "C2", "Breakout"
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


async def test_moving_out_of_a_code_channel_releases_it_and_stops_its_steps(
    monkeypatch: pytest.MonkeyPatch, statuses: list[StatusCall]
) -> None:
    session_statuses: list[tuple[str, str]] = []

    async def set_session_status(channel_id: str, status: str) -> bool:
        session_statuses.append((channel_id, status))
        return True

    monkeypatch.setattr(slack_thinking, "set_session_status", set_session_status)
    monkeypatch.setattr(slack_thinking, "_LOCATION_CHECK_SECONDS", 0.0)
    client = _LangGraph({"channel_id": "C0", "thread_ts": "0"})
    await slack_move.bind_slack_thread_id(client, "C0", "0", "thread-1")
    stream = slack_thinking.SlackThinkingStream(
        client=client,
        thread_id="thread-1",
        run_id="run-1",
        channel_id="C0",
        thread_ts="5.0",
        recipient_user_id="U1",
        recipient_team_id="T1",
        mapping_thread_ts="0",
        original_message_ts="5.0",
    )
    step = slack_thinking.Step("step-1", "Reading auth.py", "in_progress")
    stream.steps[((), "call-1")] = step

    assert await stream.moved_away() is False
    await slack_move.rebind_slack_thread(
        client,
        "thread-1",
        SlackThreadRef(channel_id="C0", thread_ts="0"),
        SlackThreadRef(channel_id="C2", thread_ts="2.0"),
    )
    assert await stream.moved_away() is True
    await stream.stop("moved")

    assert session_statuses == [("C0", "active")]
    assert ("C2", "2.0", "Thinking...") in statuses
    assert step.status == "complete"
    assert step.output == "Continued in the new thread"
