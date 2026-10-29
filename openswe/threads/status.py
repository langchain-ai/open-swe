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

from openswe.github.pull_request_key import PullRequestKey
from openswe.threads import index
from openswe.threads.index import ThreadStatus
from openswe.threads.runtimes import RUNTIMES
from openswe.ui_invalidations import Topic

logger = logging.getLogger(__name__)

STALE_BUSY: Final = timedelta(minutes=1)
RECONCILE_JOB: Final = "thread_status_reconcile"
RECONCILE_SCHEDULE: Final = "* * * * *"


async def refresh_thread_status(thread_id: str) -> ThreadStatus | None:
    """The thread's status as its runtime reports it, written to the index."""
    thread = await index.get(thread_id)
    if thread is None:
        return None
    status = await RUNTIMES.runs_on(thread.runtime).status(thread_id)
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
    for thread in await index.busy_since(datetime.now(UTC) - STALE_BUSY):
        try:
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
