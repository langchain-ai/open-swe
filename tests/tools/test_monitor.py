import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from deepagents.backends.protocol import ExecuteResponse

from openswe.database import postgres
from openswe.monitors import budget, delivery, sandbox
from openswe.monitors.delivery import ThreadMonitorDelivery
from openswe.monitors.sandbox import MonitorEnd, MonitorEvent, PendingMonitor, SandboxMonitors
from openswe.sandboxes.scripts import SandboxScript, source
from openswe.sandboxes.tool_access import ToolAccess
from openswe.webhooks import event_matches
from openswe.webhooks.event_matches import EventMatch


class LocalShell:
    """A sandbox backend that runs commands on this machine."""

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        proc = await asyncio.create_subprocess_shell(
            command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        output, _ = await asyncio.wait_for(proc.communicate(), timeout)
        return ExecuteResponse(output=output.decode(), exit_code=proc.returncode)


@pytest.fixture
def monitors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SandboxMonitors:
    monkeypatch.setattr(sandbox, "ROOT", str(tmp_path))
    return SandboxMonitors(LocalShell(), "thread-1")


async def _ended(monitors: SandboxMonitors) -> PendingMonitor:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        [monitor] = await monitors.pending()
        if monitor.end is not None:
            return monitor
        await asyncio.sleep(0.2)
    raise AssertionError("monitor did not end")


async def test_filter_picks_events_while_raw_output_keeps_every_line(
    monitors: SandboxMonitors,
) -> None:
    await monitors.start(
        "mon-1",
        command="echo starting; echo 'ERROR one'; echo between; echo 'ERROR two'; exit 3",
        filter="grep ERROR",
        description="errors",
        timeout=30,
        callback=False,
    )

    ended = await _ended(monitors)

    assert (ended.status, ended.exit_code, ended.filter_exit_code) == ("failed", 3, 0)
    assert [line for event in ended.events_pending for line in event.lines] == [
        "ERROR one",
        "ERROR two",
    ]
    assert ended.end is not None
    assert ended.end.tail == [(1, "starting"), (2, "ERROR one"), (3, "between"), (4, "ERROR two")]
    output = await monitors.output("mon-1", start_line=2, end_line=3)
    assert output["output"] == "     2\tERROR one\n     3\tbetween"

    await monitors.ack("mon-1", seq=ended.events_pending[-1].seq, end=True)
    [acked] = await monitors.pending()
    assert (acked.events_pending, acked.end) == ([], None)


async def test_timeout_without_events_still_reports_the_raw_tail(
    monitors: SandboxMonitors,
) -> None:
    await monitors.start(
        "mon-2",
        command="echo 'waiting for port 8080'; sleep 30",
        filter="grep Ready",
        description="server ready",
        timeout=1,
        callback=False,
    )

    ended = await _ended(monitors)

    assert ended.status == "timed_out"
    assert ended.events_pending == []
    assert ended.end is not None
    assert ended.end.tail == [(1, "waiting for port 8080")]


async def test_raw_log_rotates_and_keeps_numbering_across_segments(
    monitors: SandboxMonitors, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    small = (
        source("monitor.py")
        .replace("RAW_SEGMENT_BYTES = 10 * 1024 * 1024", "RAW_SEGMENT_BYTES = 64 * 1024")
        .replace("MAX_RAW_BYTES = 50 * 1024 * 1024", "MAX_RAW_BYTES = 256 * 1024")
    )
    monkeypatch.setattr(
        sandbox, "monitor_script", lambda: SandboxScript("monitor.py", str(tmp_path), text=small)
    )
    await monitors.start(
        "mon-3",
        command="seq -f 'line %g of the build log, padded to a realistic width' 20000",
        filter="grep NO_SUCH_LINE",
        description="build log",
        timeout=30,
        callback=False,
    )

    ended = await _ended(monitors)

    segments = sorted((tmp_path / "mon-3").glob("raw-*.log"))
    assert len(segments) > 1
    # A segment closes once a read takes it past its size, so it can overshoot by one read.
    assert sum(segment.stat().st_size for segment in segments) <= (256 + 64 + 64) * 1024
    assert ended.end is not None and ended.end.tail[-1][0] == 20000
    first = int(segments[0].stem.removeprefix("raw-"))
    assert first > 1
    boundary = int(segments[1].stem.removeprefix("raw-"))
    output = await monitors.output("mon-3", start_line=boundary - 1, end_line=boundary)
    assert [int(line.split("\t")[0]) for line in str(output["output"]).splitlines()] == [
        boundary - 1,
        boundary,
    ]
    assert f"line {boundary} of" in str(output["output"])


async def test_sandbox_cannot_deliver_for_a_thread_it_does_not_serve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = {"metadata": {"sandbox_id": "sandbox-b"}}
    client = SimpleNamespace(threads=SimpleNamespace(get=AsyncMock(return_value=other)))
    monkeypatch.setattr(delivery, "langgraph_client", lambda: client)

    with pytest.raises(PermissionError):
        await ThreadMonitorDelivery.load(
            "thread-2", access=ToolAccess(thread_id="thread-1", sandbox_id="sandbox-a")
        )


@pytest.mark.usefixtures("registry_db")
async def test_spent_wake_budget_holds_monitor_output_on_every_delivery_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spent = {"monitor_wake_generation": "no-human-message", "monitor_wake_count": 10}
    threads = SimpleNamespace(
        get=AsyncMock(return_value={"status": "idle", "metadata": spent}),
        get_state=AsyncMock(return_value={"values": {"messages": []}}),
    )
    client = SimpleNamespace(threads=threads)
    monkeypatch.setattr(delivery, "langgraph_client", lambda: client)
    monkeypatch.setattr(event_matches, "dispatch_client", lambda: client)
    monkeypatch.setattr(
        event_matches, "create_durable_run", AsyncMock(side_effect=AssertionError("run started"))
    )
    async with postgres.session() as session:
        await EventMatch(
            thread_id="thread-1",
            source="sandbox",
            delivery_id="mon-running:1",
            content="ERROR one",
            run_config={"thread_id": "thread-1"},
        ).record(session)
    stopped: list[str] = []

    class Monitors:
        async def stop(self, monitor_id: str, *, reason: str = "") -> dict[str, object]:
            stopped.append(monitor_id)
            return {}

    running = PendingMonitor(monitor_id="mon-running", status="running")
    ended = PendingMonitor(monitor_id="mon-ended", status="completed")

    # What the completion webhook does once a run ends, then a monitor sync.
    assert await EventMatch.deliver("thread-1", "enqueue") is False
    await ThreadMonitorDelivery("thread-1", {}, Monitors())._wake([running, ended])

    assert stopped == ["mon-running"]
    assert [match.delivery_id for match in await EventMatch.owed("thread-1")] == ["mon-running:1"]


@pytest.mark.usefixtures("registry_db")
async def test_monitor_output_is_owed_once_however_often_it_is_read() -> None:
    event = MonitorEvent(seq=1, at_raw_line=2, lines=["ERROR one"])
    ended = PendingMonitor(
        monitor_id="mon-1",
        description="errors",
        status="failed",
        exit_code=1,
        events_pending=[event],
        end=MonitorEnd(tail=[(1, "starting"), (2, "ERROR one")]),
    )
    delivery = ThreadMonitorDelivery("thread-1", {}, SandboxMonitors(LocalShell(), "thread-1"))

    # The callback and the polling job can both read a monitor before either acks it.
    await delivery._record([ended])
    await delivery._record([ended])

    owed = await EventMatch.owed("thread-1")
    assert [match.delivery_id for match in owed] == ["mon-1:1", "mon-1:end"]
    assert "ERROR one" in owed[0].content
    assert "the command failed" in owed[1].content


async def test_dashboard_never_wakes_the_sandbox_of_a_thread_with_nothing_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openswe.threads import background_work

    metadata = {"sandbox_id": "sandbox-a", "running_background_tasks": [], "running_monitors": []}
    monkeypatch.setattr(
        background_work, "_readable_thread_metadata", AsyncMock(return_value=metadata)
    )
    monkeypatch.setattr(background_work, "_assert_thread_promptable", lambda *_: None)
    connect = AsyncMock(side_effect=AssertionError("connected to an idle sandbox"))
    monkeypatch.setattr(background_work, "connect_sandbox", connect)

    work = await background_work.get_dashboard_thread_background_work("thread-1", "alice")

    assert (work.commands, work.monitors) == ([], [])


async def test_dashboard_shows_no_output_of_another_threads_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    from openswe import background_tasks
    from openswe.threads import background_work

    metadata = {"sandbox_id": "sandbox-a", "running_background_tasks": ["cmd-mine"]}
    monkeypatch.setattr(
        background_work, "_readable_thread_metadata", AsyncMock(return_value=metadata)
    )
    monkeypatch.setattr(background_work, "_assert_thread_promptable", lambda *_: None)
    monkeypatch.setattr(background_work, "connect_sandbox", AsyncMock(return_value=object()))
    # A sandbox shared by a parent thread holds its commands too.
    monkeypatch.setattr(
        background_tasks,
        "thread_tasks",
        AsyncMock(return_value=[{"task_id": "cmd-mine", "status": "running"}]),
    )

    with pytest.raises(HTTPException) as refused:
        await background_work.get_dashboard_thread_background_output(
            "thread-1", "cmd-theirs", "alice"
        )

    assert refused.value.status_code == 404


class FakeThreads:
    """An Agent Server thread whose metadata and messages live in memory."""

    def __init__(self) -> None:
        self.metadata: dict[str, object] = {}
        self.messages: list[dict[str, object]] = []

    async def get(self, _thread_id: str) -> dict[str, object]:
        return {"status": "idle", "metadata": self.metadata}

    async def get_state(self, _thread_id: str) -> dict[str, object]:
        return {"values": {"messages": self.messages}}

    async def update(self, _thread_id: str, *, metadata: dict[str, object]) -> None:
        self.metadata = {**self.metadata, **metadata}


def _human(text: str) -> dict[str, object]:
    return {
        "id": text,
        "content": f'<input-message sender="u" surface="web" kind="human">{text}</input-message>',
    }


@pytest.mark.usefixtures("memory_event_claims")
async def test_each_human_message_opens_one_allowance_of_starts_and_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads = FakeThreads()
    threads.messages = [_human("watch the deploy")]
    allowance = budget.ThreadMonitorBudget(SimpleNamespace(threads=threads), "thread-1")
    now = 1_000_000.0
    monkeypatch.setattr(budget.time, "time", lambda: now)

    assert [await allowance.start() for _ in range(budget.MAX_STARTS)][0] == budget.WINDOW_SECONDS
    with pytest.raises(budget.MonitorBudgetSpent):
        await allowance.start()

    threads.messages.append(_human("keep going"))
    now += 1
    assert await allowance.start() == budget.WINDOW_SECONDS
    now += budget.WINDOW_SECONDS
    with pytest.raises(budget.MonitorBudgetSpent):
        await allowance.start()

    threads.messages.append(_human("one more"))
    assert await allowance.take_events(budget.MAX_EVENTS + 5) == budget.MAX_EVENTS
    with pytest.raises(budget.MonitorBudgetSpent):
        await allowance.start()


@pytest.mark.usefixtures("registry_db", "memory_event_claims")
async def test_events_past_the_allowance_stop_the_monitors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads = FakeThreads()
    client = SimpleNamespace(threads=threads)
    monkeypatch.setattr(delivery, "langgraph_client", lambda: client)
    monkeypatch.setattr(event_matches, "dispatch_client", lambda: client)
    monkeypatch.setattr(EventMatch, "deliver", AsyncMock(return_value=True))
    noisy = PendingMonitor(
        monitor_id="mon-noisy",
        status="running",
        events_pending=[
            MonitorEvent(seq=seq, at_raw_line=seq, lines=[f"ERROR {seq}"]) for seq in range(1, 61)
        ],
    )
    stopped: dict[str, str] = {}
    acked: list[int] = []

    class Monitors:
        async def pending(self) -> list[PendingMonitor]:
            return [noisy]

        async def ack(self, monitor_id: str, *, seq: int, end: bool) -> None:
            acked.append(seq)

        async def stop(self, monitor_id: str, *, reason: str = "") -> dict[str, object]:
            stopped[monitor_id] = reason
            return {}

    await ThreadMonitorDelivery("thread-1", {}, Monitors()).sync()

    owed = await EventMatch.owed("thread-1")
    assert len(owed) == budget.MAX_EVENTS
    assert acked == [60]
    assert stopped == {"mon-noisy": budget.EVENTS_SPENT_REASON}
