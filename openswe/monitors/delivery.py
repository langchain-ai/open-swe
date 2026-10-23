"""Record what monitors emit as event matches owed to their thread, and wake it.

Two triggers read a sandbox's monitors, chosen per monitor by the starting
person's ``experimental_background_callbacks`` flag: the runner calling
``/monitors/{id}/sync`` through the sandbox tools channel, or a per-thread job
polling every minute. Either one records each event as an ``EventMatch``
keyed by monitor and sequence number, so a reading that both triggers make
records it once, then lets ``EventMatch`` deliver it: before the next model call
of a running thread, or in a run started for it.
"""

import logging
import re
from collections.abc import Mapping
from typing import Final, Self

from langgraph_sdk.errors import NotFoundError
from pydantic import JsonValue

from openswe.background_tasks import completion_configurable
from openswe.database import postgres
from openswe.jobs import queue
from openswe.monitors.budget import EVENTS_SPENT_REASON, ThreadMonitorBudget
from openswe.monitors.sandbox import MonitorEvent, PendingMonitor, SandboxMonitors, owner_of
from openswe.prompts import prompt
from openswe.sandboxes.connect import connect_sandbox
from openswe.sandboxes.tool_access import SANDBOX_HOST_THREAD_KEY, ToolAccess
from openswe.threads.wake_budget import MONITOR_WAKES
from openswe.ui_invalidations.topics import Topic
from openswe.utils.background_task_state import RUNNING_MONITORS_KEY, update_background_task_state
from openswe.utils.thread_ops import langgraph_client
from openswe.webhooks.event_matches import EventMatch

logger = logging.getLogger(__name__)

POLL_JOB_KIND: Final = "monitors"
_POLL_SCHEDULE = "* * * * *"
_WAKE_LIMIT_REASON = (
    f"runs started by monitor events reached {MONITOR_WAKES.limit} since the last human message"
)
_BACKTICKS = re.compile(r"`+")


def _tracked(metadata: Mapping[str, object]) -> list[str]:
    tracked = metadata.get(RUNNING_MONITORS_KEY)
    return [item for item in tracked if isinstance(item, str)] if isinstance(tracked, list) else []


def _owed(monitors: list[PendingMonitor]) -> list[PendingMonitor]:
    return [monitor for monitor in monitors if monitor.events_pending or monitor.end]


def _within(monitors: list[PendingMonitor], allowance: int) -> list[PendingMonitor]:
    """``monitors`` keeping only the first ``allowance`` events across them."""
    kept: list[PendingMonitor] = []
    for monitor in monitors:
        events = monitor.events_pending[:allowance]
        allowance -= len(events)
        kept.append(monitor.model_copy(update={"events_pending": events}))
    return kept


def _fence(*texts: str) -> str:
    longest = max((len(run) for text in texts for run in _BACKTICKS.findall(text)), default=0)
    return "`" * max(3, longest + 1)


def _numbered(lines: list[tuple[int, str]]) -> str:
    return "\n".join(f"{number:>6}\t{text}" for number, text in lines)


def _event_content(monitor: PendingMonitor, event: MonitorEvent) -> str:
    lines = "\n".join(event.lines)
    return prompt(
        "runs/monitor-event",
        monitor_id=monitor.monitor_id,
        description=monitor.description,
        at_raw_line=event.at_raw_line,
        lines=lines,
        fence=_fence(lines),
    )


def _end_content(monitor: PendingMonitor) -> str:
    end = monitor.end
    tail = _numbered(end.tail) if end else ""
    filter_errors = end.filter_errors if end else ""
    return prompt(
        "runs/monitor-ended",
        monitor_id=monitor.monitor_id,
        description=monitor.description,
        status=monitor.status,
        stop_reason=monitor.stop_reason,
        timeout=monitor.timeout,
        exit_code=monitor.exit_code,
        has_filter=bool(monitor.filter),
        filter_exit_code=monitor.filter_exit_code,
        events=monitor.events,
        raw_lines=monitor.raw_lines,
        dropped_filter_bytes=monitor.dropped_filter_bytes,
        tail=tail,
        filter_errors=filter_errors,
        fence=_fence(tail, filter_errors),
    )


class ThreadMonitorDelivery:
    """Hands one thread what its sandbox monitors emitted."""

    def __init__(
        self, thread_id: str, metadata: Mapping[str, object], monitors: SandboxMonitors
    ) -> None:
        self._thread_id = thread_id
        self._metadata = metadata
        self._monitors = monitors

    @classmethod
    async def load(cls, thread_id: str, *, access: ToolAccess | None = None) -> Self | None:
        """``None`` when the thread or its sandbox is gone.

        With ``access``, a sandbox calling back on its own behalf, the thread must
        be the one that sandbox serves; raises ``PermissionError`` otherwise.
        """
        try:
            thread = await langgraph_client().threads.get(thread_id)
        except NotFoundError:
            logger.info("Monitor thread is gone", extra={"agent_thread_id": thread_id})
            return None
        metadata = thread.get("metadata") if isinstance(thread, dict) else None
        metadata = metadata if isinstance(metadata, dict) else {}
        if access is not None and (
            metadata.get("sandbox_id") != access.sandbox_id
            or (
                thread_id != access.thread_id
                and metadata.get(SANDBOX_HOST_THREAD_KEY) != access.thread_id
            )
        ):
            raise PermissionError("Monitor owner does not belong to this sandbox")
        sandbox_id = metadata.get("sandbox_id")
        if not isinstance(sandbox_id, str) or not sandbox_id:
            if _tracked(metadata):
                await update_background_task_state(
                    langgraph_client(), thread_id, reset=True, key=RUNNING_MONITORS_KEY
                )
            return None
        backend = await connect_sandbox(sandbox_id, thread_id=thread_id)
        return cls(thread_id, metadata, SandboxMonitors(backend, thread_id))

    async def sync(self) -> bool:
        """Record and deliver what the monitors emitted; whether one is still running."""
        pending = await self._monitors.pending()
        running = [monitor.monitor_id for monitor in pending if monitor.status == "running"]
        # Anything tracked that the sandbox no longer runs is done, including a
        # monitor whose directory went with a recreated sandbox.
        await update_background_task_state(
            langgraph_client(),
            self._thread_id,
            running=running,
            finished=[item for item in _tracked(self._metadata) if item not in running],
            key=RUNNING_MONITORS_KEY,
        )
        budget = ThreadMonitorBudget(langgraph_client(), self._thread_id)
        recorded = spent = False
        owed = _owed(pending)
        while owed:
            wanted = sum(len(monitor.events_pending) for monitor in owed)
            granted = await budget.take_events(wanted)
            # Events past the allowance are skipped, not saved for later: the
            # monitors stop, and their end notices say why.
            spent = spent or granted < wanted
            await self._record(_within(owed, granted))
            for monitor in owed:
                last = monitor.events_pending[-1].seq if monitor.events_pending else 0
                await self._monitors.ack(monitor.monitor_id, seq=last, end=monitor.end is not None)
            recorded = True
            if not any(monitor.behind for monitor in owed):
                break
            owed = _owed(await self._monitors.pending())
        if spent:
            await self._stop_running(pending, EVENTS_SPENT_REASON)
        if recorded:
            await Topic.THREAD_BACKGROUND_WORK.invalidate(key=self._thread_id)
            await self._wake(pending)
        return bool(running)

    async def _record(self, monitors: list[PendingMonitor]) -> None:
        run_config: dict[str, JsonValue] = await completion_configurable(
            self._thread_id, self._metadata
        )
        matches: list[EventMatch] = []
        for monitor in monitors:
            matches.extend(
                EventMatch(
                    thread_id=self._thread_id,
                    source="sandbox",
                    delivery_id=f"{monitor.monitor_id}:{event.seq}",
                    content=_event_content(monitor, event),
                    run_config=run_config,
                )
                for event in monitor.events_pending
            )
            if monitor.end is not None:
                matches.append(
                    EventMatch(
                        thread_id=self._thread_id,
                        source="sandbox",
                        delivery_id=f"{monitor.monitor_id}:end",
                        content=_end_content(monitor),
                        run_config=run_config,
                    )
                )
        async with postgres.session() as session:
            for match in matches:
                await match.record(session)

    async def _wake(self, pending: list[PendingMonitor]) -> None:
        """Deliver what is owed; once monitor wakes are spent, stop the monitors instead."""
        if await EventMatch.deliver(self._thread_id, "enqueue"):
            return
        client = langgraph_client()
        thread = await client.threads.get(self._thread_id)
        busy = isinstance(thread, dict) and thread.get("status") == "busy"
        if busy or not await MONITOR_WAKES.exhausted(client, self._thread_id):
            return
        # What is owed now waits for the next run a person starts.
        await self._stop_running(pending, _WAKE_LIMIT_REASON)

    async def _stop_running(self, pending: list[PendingMonitor], reason: str) -> None:
        for monitor in pending:
            if monitor.status == "running":
                await self._monitors.stop(monitor.monitor_id, reason=reason)
        logger.info(
            "Monitor budget spent", extra={"agent_thread_id": self._thread_id, "reason": reason}
        )


async def ensure_monitor_polling(thread_id: str) -> None:
    """Read ``thread_id``'s monitors every minute until none is running."""
    await queue.schedule(
        POLL_JOB_KIND,
        {"thread_id": thread_id},
        cron=_POLL_SCHEDULE,
        key=f"{POLL_JOB_KIND}:{thread_id}",
        metadata={"kind": POLL_JOB_KIND, "thread_id": thread_id},
    )


async def poll_monitors(thread_id: str) -> bool:
    """One polling tick; whether to keep polling."""
    delivery = await ThreadMonitorDelivery.load(thread_id)
    return delivery is not None and await delivery.sync()


async def sync_monitor_callback(monitor_id: str, access: ToolAccess) -> None:
    """A runner's call back: deliver what its monitor's thread is owed.

    Raises ``LookupError`` for a monitor the sandbox does not hold and
    ``PermissionError`` for one whose thread the sandbox does not serve.
    """
    backend = await connect_sandbox(access.sandbox_id, thread_id=access.thread_id)
    owner = await owner_of(backend, monitor_id)
    delivery = await ThreadMonitorDelivery.load(owner, access=access)
    if delivery is not None:
        await delivery.sync()
