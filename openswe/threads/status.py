"""Keep the thread index's run status in step with each thread's runtime.

Starting a run marks its thread busy. What the run left behind (idle, an
interrupt waiting on a person, an error) is the runtime's to say, so the
run-complete webhook asks it, and a reconcile job asks again for any thread
still marked busy long after a webhook should have come. A run that froze while
still reporting itself running is failed by ``openswe.threads.stalls``.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Final

from openswe.dispatch import channel_of
from openswe.github.pull_request_key import PullRequestKey
from openswe.threads import index
from openswe.threads.index import ThreadStatus
from openswe.threads.runtimes import RUNTIMES
from openswe.ui_invalidations import Topic

logger = logging.getLogger(__name__)

STALE_BUSY: Final = timedelta(minutes=1)
# Past this, a channel's thread MDA still doesn't have never got its run: the channel
# gave up on the backend's answer or failed to start it.
UNSTARTED_CHANNEL_RUN: Final = timedelta(minutes=10)
RECONCILE_JOB: Final = "thread_status_reconcile"
RECONCILE_SCHEDULE: Final = "* * * * *"


async def refresh_thread_status(
    thread_id: str, *, unstarted_is_idle: bool = False
) -> ThreadStatus | None:
    """The thread's status as its runtime reports it, written to the index.

    ``unstarted_is_idle``: a channel's thread MDA doesn't have is idle, its run never
    having started.
    """
    thread = await index.get(thread_id)
    if thread is None:
        return None
    runtime = RUNTIMES.runs_on(thread.runtime)
    if channel_of(thread.metadata) is not None:
        # Read as Studio, as every MDA read is. A channel's thread MDA doesn't show
        # the backend says nothing about its run, which must not read as idle.
        found = await runtime.indexed(thread_id)
        status = found.status if found else ("idle" if unstarted_is_idle else None)
    else:
        status = await runtime.status(thread_id)
    if status is not None:
        await index.set_status(thread_id, status)
        if status != thread.status and thread.metadata.get("kind") == "reviewer":
            pr = thread.metadata.get("pr")
            if isinstance(pr, dict):
                owner, repo, number = pr.get("owner"), pr.get("name"), pr.get("number")
                if isinstance(owner, str) and isinstance(repo, str) and isinstance(number, int):
                    await Topic.PULL_REQUESTS.invalidate(key=PullRequestKey.of(owner, repo, number))
    return status


async def thread_run_error(thread_id: str) -> str | None:
    """Why the thread's latest run failed, as its runtime recorded it; the index keeps no state."""
    thread = await index.get(thread_id)
    return await RUNTIMES.runs_on(thread.runtime if thread else "local").run_error(thread_id)


async def reconcile_busy_threads() -> int:
    """Ask the runtime about threads busy for longer than any webhook should take."""
    settled = 0
    now = datetime.now(UTC)
    unstarted = {thread.thread_id for thread in await index.busy_since(now - UNSTARTED_CHANNEL_RUN)}
    for thread in await index.busy_since(now - STALE_BUSY):
        try:
            status = await refresh_thread_status(
                thread.thread_id, unstarted_is_idle=thread.thread_id in unstarted
            )
        except Exception:
            logger.warning(
                "Could not refresh a busy thread's status",
                extra={"thread_id": thread.thread_id},
                exc_info=True,
            )
            continue
        settled += status is not None and status != "busy"
    return settled
