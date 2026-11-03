"""Keep the thread index's run status in step with each thread's runtime.

Starting a run marks its thread busy. What the run left behind (idle, an
interrupt waiting on a person, an error) is the runtime's to say, so the
run-complete webhook asks it, and a reconcile job asks again for any thread
still marked busy long after a webhook should have come. A run that froze while
still reporting itself running is failed by ``openswe.threads.stalls``.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Final, TypedDict

from pydantic import JsonValue

from openswe.dispatch import channel_of
from openswe.github.pull_request_key import PullRequestKey
from openswe.threads import index
from openswe.threads.index import IndexedThread, ThreadStatus
from openswe.threads.runtimes import RUNTIMES, now_ms
from openswe.threads.store import THREADS
from openswe.ui_invalidations import Topic


class WorkRunView(TypedDict):
    runId: str | None
    viewed: bool
    viewedAt: int | None


class WorkRunState:
    """The latest runtime run and its explicit Work read receipt."""

    def __init__(self, thread: IndexedThread) -> None:
        self.thread = thread

    async def refresh(
        self, *, viewed_run_id: str | None = None, unstarted_is_idle: bool = False
    ) -> IndexedThread:
        for _ in range(2):
            if self.thread.metadata.get("experience") != "agent_workspace":
                return self.thread
            runtime = RUNTIMES.runs_on(self.thread.runtime)
            run = await runtime.latest_run(self.thread.thread_id)
            channel = channel_of(self.thread.metadata)
            if run is None and channel is not None:
                status = "idle" if unstarted_is_idle else self.thread.status
            else:
                status = await runtime.status(self.thread.thread_id)
            patch: dict[str, JsonValue] = {"work_run_synced": True}
            if (
                run is None
                and status == "idle"
                and self.thread.metadata.get("latest_run_status") in {"pending", "running"}
            ):
                patch["latest_run_status"] = None
            if run is not None:
                patch.update(latest_run_id=run.run_id, latest_run_status=run.status)
                if run.active:
                    status = "busy"
                if (
                    viewed_run_id == run.run_id
                    and not run.active
                    and status is not None
                    and status != "busy"
                    and (
                        self.thread.metadata.get("last_viewed_run_id") != viewed_run_id
                        or not isinstance(
                            self.thread.metadata.get("last_viewed_at_ms"), (int, float)
                        )
                    )
                ):
                    patch.update(last_viewed_run_id=viewed_run_id, last_viewed_at_ms=now_ms())
            patch = {
                key: value for key, value in patch.items() if self.thread.metadata.get(key) != value
            }
            if not patch and status == self.thread.status:
                current = await THREADS.get(self.thread.thread_id)
                if current is None or current.updated_at == self.thread.updated_at:
                    return self.thread
                self.thread = current
                continue
            updated = await THREADS.update_if_current(
                self.thread, metadata=patch, status=status or self.thread.status
            )
            if updated is not None:
                self.thread = updated
                return updated
            current = await THREADS.get(self.thread.thread_id)
            if current is None:
                return self.thread
            self.thread = current
        return self.thread

    async def receipt(self) -> WorkRunView:
        metadata = self.thread.metadata
        raw_id = metadata.get("latest_run_id")
        run_id = raw_id if isinstance(raw_id, str) else None
        viewed = bool(run_id) and metadata.get("last_viewed_run_id") == run_id
        viewed_at = metadata.get("last_viewed_at_ms")
        return {
            "runId": run_id,
            "viewed": viewed,
            "viewedAt": int(viewed_at) if isinstance(viewed_at, (int, float)) else None,
        }


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
    if thread.metadata.get("experience") == "agent_workspace":
        return (await WorkRunState(thread).refresh(unstarted_is_idle=unstarted_is_idle)).status
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
