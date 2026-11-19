"""The "Thinking..." status Slack shows on a thread while its agent works."""

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import chain, repeat

from langgraph_sdk.client import LangGraphClient

from openswe.slack.client import (
    set_slack_thread_status,
)
from openswe.slack.records import lookup_slack_thread_id
from openswe.slack.routed_work import RoutedWork
from openswe.source_context import SourceContext
from openswe.tasks.store import TaskMembership, sidebar_memberships
from openswe.threads.runtimes import RUNTIMES
from openswe.threads.store import THREADS
from openswe.utils.background_task_state import RUNNING_BACKGROUND_TASKS_KEY

logger = logging.getLogger(__name__)

_DEFAULT_RETRY_SECONDS = 30.0
_THINKING_STATUS = "Thinking..."
_STATUS_REFRESH_SECONDS = 90.0
_STATUS_RETRY_DELAYS = (1.0, 2.0)
_STATUS_OBSERVERS: set[asyncio.Task[None]] = set()


async def restore_slack_thinking_status(channel_id: str, thread_ts: str) -> bool:
    """Restore the status Slack clears when the assistant posts a reply."""
    return await set_slack_thread_status(channel_id, thread_ts, _THINKING_STATUS)


async def show_slack_thinking_status(
    *,
    client: LangGraphClient,
    thread_id: str,
    run_id: str,
    channel_id: str,
    thread_ts: str,
    run_thread_id: str | None = None,
    wait_for_completion: bool = True,
) -> None:
    """Refresh Slack's status until the run and its task's active runs finish."""
    if not wait_for_completion:
        observer = asyncio.create_task(
            show_slack_thinking_status(
                client=client,
                thread_id=thread_id,
                run_id=run_id,
                channel_id=channel_id,
                thread_ts=thread_ts,
                run_thread_id=run_thread_id,
            )
        )
        _STATUS_OBSERVERS.add(observer)
        observer.add_done_callback(_STATUS_OBSERVERS.discard)
        return
    if not await restore_slack_thinking_status(channel_id, thread_ts):
        return

    home = (channel_id, thread_ts)
    last_known = home

    async def refresh() -> None:
        nonlocal last_known
        while True:
            await asyncio.sleep(_STATUS_REFRESH_SECONDS)
            location = await _current_slack_location(thread_id, unbound=home)
            if location is None:
                continue
            last_known = location
            if last_known[1] != "0" and (
                await _task_has_active_runs(thread_id)
                or await RoutedWork(thread_id).delegated_active()
            ):
                await restore_slack_thinking_status(*last_known)

    refresher = asyncio.create_task(refresh())
    try:
        for attempt, delay in enumerate(
            chain((0.0, *_STATUS_RETRY_DELAYS), repeat(_DEFAULT_RETRY_SECONDS))
        ):
            if attempt:
                await asyncio.sleep(delay)
            try:
                await client.runs.join(run_thread_id or thread_id, run_id)
                while run_thread_id is None and (
                    await _task_has_active_runs(thread_id)
                    or await RoutedWork(thread_id).delegated_active()
                ):
                    await asyncio.sleep(_STATUS_REFRESH_SECONDS)
                break
            except Exception:
                logger.warning(
                    "Slack thinking status completion wait failed",
                    extra={
                        "agent_thread_id": thread_id,
                        "run_id": run_id,
                        "attempt": attempt + 1,
                    },
                    exc_info=True,
                )
    finally:
        refresher.cancel()
        try:
            results = await asyncio.gather(refresher, return_exceptions=True)
            error = results[0]
            if isinstance(error, Exception):
                logger.warning(
                    "Slack thinking status refresher failed",
                    extra={"agent_thread_id": thread_id, "run_id": run_id},
                    exc_info=(type(error), error, error.__traceback__),
                )
        finally:
            await asyncio.shield(_settle_after_run(thread_id, home, last_known))


async def _current_slack_location(
    thread_id: str, *, unbound: tuple[str, str]
) -> tuple[str, str] | None:
    """The thread's Slack binding now, `unbound` when it has none, or None when unreadable."""
    try:
        metadata = await _metadata(thread_id)
    except Exception:
        logger.warning(
            "Could not read the thread's current Slack location",
            extra={"agent_thread_id": thread_id},
            exc_info=True,
        )
        return None
    return SourceContext.from_metadata(metadata).slack_location or unbound


async def _settle_after_run(
    thread_id: str,
    home: tuple[str, str],
    last_known: tuple[str, str],
) -> None:
    location = None
    for attempt, delay in enumerate((0.0, *_STATUS_RETRY_DELAYS)):
        if attempt:
            await asyncio.sleep(delay)
        location = await _current_slack_location(thread_id, unbound=home)
        if location is not None:
            break
    location = location or last_known
    if location[1] != "0":
        await clear_slack_thinking_status_if_idle(thread_id, *location)


async def settle_slack_thread_status(channel_id: str, thread_ts: str) -> None:
    """Hand a Slack thread's status back to the conversation mapped there, or clear it."""
    try:
        thread_id = await lookup_slack_thread_id(channel_id, thread_ts)
    except Exception:
        logger.warning(
            "Could not look up the conversation owning a Slack thread's status",
            extra={"slack_channel": channel_id},
            exc_info=True,
        )
        thread_id = None
    if thread_id:
        await refresh_slack_background_status(thread_id, resume=True)
    else:
        await set_slack_thread_status(channel_id, thread_ts, "")


async def release_slack_location_status(channel_id: str, thread_ts: str) -> None:
    """Take the working indicator off a Slack location the thread no longer lives in."""
    if thread_ts == "0":
        return
    await set_slack_thread_status(channel_id, thread_ts, "")


async def clear_slack_thinking_status_if_idle(
    thread_id: str,
    channel_id: str,
    thread_ts: str,
    *,
    metadata: Mapping[str, object] | None = None,
) -> None:
    """Settle idle status, retrying failed reads or writes without polling active runs."""
    for attempt, delay in enumerate((0.0, *_STATUS_RETRY_DELAYS)):
        if attempt:
            await asyncio.sleep(delay)
        if await _settle_slack_thinking_status(
            thread_id,
            channel_id,
            thread_ts,
            metadata=metadata if attempt == 0 else None,
        ):
            return
    logger.warning(
        "Slack status cleanup retries exhausted",
        extra={"agent_thread_id": thread_id, "slack_channel": channel_id},
    )


async def _settle_slack_thinking_status(
    thread_id: str,
    channel_id: str,
    thread_ts: str,
    *,
    metadata: Mapping[str, object] | None,
) -> bool:
    try:
        if metadata is None:
            metadata = await _metadata(thread_id)
        if await _task_has_active_runs(thread_id):
            return True
        if await RoutedWork(thread_id).delegated_active(metadata=metadata):
            return await restore_slack_thinking_status(channel_id, thread_ts)
        waiting = bool(metadata.get(RUNNING_BACKGROUND_TASKS_KEY))
        if not waiting:
            for worker in (await sidebar_memberships([thread_id], workers_of=True)).values():
                if worker.cancelled or worker.launch_error:
                    continue
                worker_thread = await THREADS.get(worker.thread_id)
                if worker_thread is None:
                    logger.debug(
                        "Worker thread creation is incomplete",
                        extra={"worker_thread_id": worker.thread_id},
                    )
                    continue
                if worker_thread.metadata.get(RUNNING_BACKGROUND_TASKS_KEY):
                    waiting = True
                    break
        if not await set_slack_thread_status(
            channel_id, thread_ts, "Waiting for background tasks…" if waiting else ""
        ):
            logger.warning(
                "Slack rejected status cleanup",
                extra={"agent_thread_id": thread_id, "slack_channel": channel_id},
            )
            return False
        return True
    except Exception:
        logger.warning(
            "Could not settle Slack status", extra={"agent_thread_id": thread_id}, exc_info=True
        )
        return False


async def sync_slack_background_status(
    client: LangGraphClient,
    thread_id: str,
    *,
    resume: bool = False,
    run_id: str | None = None,
    metadata: Mapping[str, object] | None = None,
    source_context: SourceContext | None = None,
) -> None:
    """Refresh the indicator, and follow a worker's run `run_id` until its task is idle.

    Following the run joins it, which is all this needs the client for.
    """
    refreshed = await refresh_slack_background_status(
        thread_id, resume=resume, metadata=metadata, source_context=source_context
    )
    if run_id is None or refreshed is None or refreshed.thread_id == thread_id:
        return
    await show_slack_thinking_status(
        client=client,
        thread_id=refreshed.thread_id,
        run_id=run_id,
        channel_id=refreshed.channel_id,
        thread_ts=refreshed.thread_ts,
        run_thread_id=thread_id,
        wait_for_completion=False,
    )


@dataclass(frozen=True)
class StatusLocation:
    """The thread whose Slack status a refresh settled (a worker's coordinator) and where."""

    thread_id: str
    channel_id: str
    thread_ts: str


async def refresh_slack_background_status(
    thread_id: str,
    *,
    resume: bool = False,
    metadata: Mapping[str, object] | None = None,
    source_context: SourceContext | None = None,
) -> StatusLocation | None:
    """Refresh the indicator using a current snapshot or a destination-only hint.

    A worker's indicator is its coordinator's. A source context without Slack is
    authoritative; None means it is unknown. A destination hint never substitutes
    for task metadata when settling idle work. None when there's no Slack thread
    to show it in, or it couldn't be refreshed.
    """
    try:
        context = await TaskMembership.context_for_thread(thread_id)
        if context is not None and context.membership.role == "worker":
            thread_id = context.task.require_coordinator()
            metadata = None
            source_context = None
        if source_context is None:
            if metadata is None:
                metadata = await _metadata(thread_id)
            source_context = SourceContext.from_metadata(metadata)
        slack_thread = source_context.slack_thread
        if slack_thread is None or not slack_thread.location:
            return None
        channel_id, thread_ts = slack_thread.location
        if thread_ts == "0":
            return None
        if await _task_has_active_runs(thread_id):
            if resume:
                await restore_slack_thinking_status(channel_id, thread_ts)
        else:
            await clear_slack_thinking_status_if_idle(
                thread_id, channel_id, thread_ts, metadata=metadata
            )
    except Exception:
        logger.warning(
            "Could not refresh Slack background status",
            extra={"agent_thread_id": thread_id},
            exc_info=True,
        )
        return None
    return StatusLocation(thread_id, channel_id, thread_ts)


async def _task_has_active_runs(thread_id: str) -> bool:
    if await _thread_has_active_runs(thread_id):
        return True
    for worker in (await sidebar_memberships([thread_id], workers_of=True)).values():
        if worker.cancelled or worker.launch_error:
            continue
        try:
            if await _thread_has_active_runs(worker.thread_id):
                return True
        except LookupError:
            logger.warning("Worker thread is missing", extra={"worker_thread_id": worker.thread_id})
    return False


async def _thread_has_active_runs(thread_id: str) -> bool:
    """Whether a run on the thread is still pending or running, asked of its runtime."""
    thread = await THREADS.get(thread_id)
    if thread is None:
        raise LookupError(f"Thread {thread_id} is not indexed")
    return await RUNTIMES.runs_on(thread.runtime).has_active_run(thread_id)


async def _metadata(thread_id: str) -> Mapping[str, object]:
    thread = await THREADS.get(thread_id)
    if thread is None:
        raise LookupError(f"Thread {thread_id} is not indexed")
    return thread.metadata
