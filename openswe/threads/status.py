"""Keep the thread index's run status in step with each thread's runtime.

Starting a run marks its thread busy. What the run left behind (idle, an
interrupt waiting on a person, an error) is the runtime's to say, so the
run-complete webhook asks it, and a reconcile job asks again for any thread
still marked busy long after a webhook should have come.

On an MDA thread the job also completes a latest run that ended unannounced, as
the webhook would have. MDA channels' runs have no webhook: the channel reports
each one that ends (``openswe.mda_channels.completion``), but only from the
process that started it, so a restart loses that report. Completion is once per
run, so a webhook or report that did arrive wins. It reads MDA as LangSmith
Studio does, so it sees the threads channels own too.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from openswe.dispatch import CHANNEL_METADATA_KEY, channel_of
from openswe.threads import index
from openswe.threads.index import IndexedThread, ThreadStatus
from openswe.threads.runtimes import RUNTIMES

logger = logging.getLogger(__name__)

STALE_BUSY: Final = timedelta(minutes=1)
RECONCILE_JOB: Final = "thread_status_reconcile"
RECONCILE_SCHEDULE: Final = "* * * * *"
_ENDED_RUN_STATUSES: Final = frozenset({"success", "error", "timeout", "interrupted"})
_FAILED_RUN_STATUSES: Final = frozenset({"error", "timeout"})


async def refresh_thread_status(thread_id: str) -> ThreadStatus | None:
    """The thread's status as its runtime reports it, written to the index."""
    thread = await index.get(thread_id)
    if thread is None:
        return None
    runtime = RUNTIMES.runs_on(thread.runtime)
    if channel_of(thread.metadata) is not None:
        # Read as Studio, as every MDA read is. A channel's thread MDA doesn't show
        # the backend says nothing about its run, which must not read as idle.
        found = await runtime.indexed(thread_id)
        status = found.status if found else None
    else:
        status = await runtime.status(thread_id)
    if status is not None:
        await index.set_status(thread_id, status)
    return status


async def thread_run_error(thread_id: str) -> str | None:
    """Why the thread's latest run failed, as its runtime recorded it; the index keeps no state."""
    thread = await index.get(thread_id)
    return await RUNTIMES.runs_on(thread.runtime if thread else "local").run_error(thread_id)


async def complete_ended_run(thread: IndexedThread, run_id: str | None = None) -> bool:
    """Complete the thread's run on MDA, its latest by default, if it ended; whether it had."""
    runtime = RUNTIMES.runs_on(thread.runtime)
    run = await (
        runtime.run(thread.thread_id, run_id) if run_id else runtime.latest_run(thread.thread_id)
    )
    status = run.get("status") if run else None
    ended_run_id = run.get("run_id") if run else None
    if run is None or status not in _ENDED_RUN_STATUSES or not isinstance(ended_run_id, str):
        return False
    raw_metadata = run.get("metadata")
    metadata: dict[str, Any] = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
    # Every run on a channel's thread is the channel's.
    if (channel := channel_of(thread.metadata)) is not None:
        metadata.setdefault(CHANNEL_METADATA_KEY, channel)
    payload: dict[str, Any] = {
        "thread_id": thread.thread_id,
        "run_id": ended_run_id,
        "status": status,
        "metadata": metadata,
    }
    recorded = await runtime.get(thread.thread_id) or {}
    if status in _FAILED_RUN_STATUSES:
        payload["error"] = recorded.get("error")
    # The run's messages, for its usage, as a webhook carries them.
    if isinstance(values := recorded.get("values"), dict):
        payload["values"] = values
    # Completion reads the thread status from here.
    from openswe.completion import complete_run_once

    await complete_run_once(payload)
    return True


async def reconcile_busy_threads() -> int:
    """Settle threads busy for longer than any webhook should take.

    Bounded by ``index.busy_since``'s page; a thread left over is next minute's.
    """
    settled = 0
    for thread in await index.busy_since(datetime.now(UTC) - STALE_BUSY):
        try:
            if thread.runtime == "mda":
                await complete_ended_run(thread)
            status = await refresh_thread_status(thread.thread_id)
        except Exception:
            logger.warning(
                "Could not refresh a busy thread's status",
                extra={"thread_id": thread.thread_id},
                exc_info=True,
            )
            continue
        settled += status is not None and status != "busy"
    return settled
